"""Versioned WebSocket API for telemetry, status and script tools."""

import asyncio
import json
import logging

import websockets

from core.control_service import ControlService
from core.recording_manager import RecordingManager
from core.script_engine import ScriptEngine
from core.state import make_envelope

logger = logging.getLogger(__name__)


class WebSocketServer:
    HISTORY_LIMIT = 100

    def __init__(
        self,
        host: str,
        port: int,
        shared_state: dict,
        engine: ScriptEngine,
        logs_path: str,
        recording_manager: RecordingManager | None = None,
        control_service: ControlService | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.shared_state = shared_state
        self.engine = engine
        self.logs_path = logs_path
        self.recording_manager = recording_manager
        self.control_service = control_service
        self.ws_connections = set()

    @staticmethod
    def _serialize(message: dict) -> str:
        return json.dumps(message, ensure_ascii=False, allow_nan=False)

    async def _send_event(
        self, websocket, kind: str, payload, request_id: str | None = None
    ) -> None:
        await websocket.send(
            self._serialize(make_envelope(kind, payload, request_id))
        )

    async def broadcast(self, message: dict) -> None:
        if not self.ws_connections:
            return
        if "schema_version" not in message:
            message = make_envelope(
                message.get("type", "event"), message.get("payload")
            )
        try:
            serialized = self._serialize(message)
        except (TypeError, ValueError):
            logger.exception("WS message is not valid JSON")
            return

        connections = list(self.ws_connections)
        results = await asyncio.gather(
            *(connection.send(serialized) for connection in connections),
            return_exceptions=True,
        )
        for connection, result in zip(connections, results):
            if isinstance(result, Exception):
                self.ws_connections.discard(connection)
                logger.warning("Ошибка отправки WS-клиенту: %s", result)

    async def broadcast_event(
        self, kind: str, payload, request_id: str | None = None
    ) -> None:
        await self.broadcast(make_envelope(kind, payload, request_id))

    async def _send_initial_state(self, websocket) -> None:
        await self._send_event(
            websocket, "file_list", {"files": await self.engine.get_list()}
        )
        await self._send_event(
            websocket,
            "system_status",
            {"gateway": self.shared_state.get("gateway_status", {})},
        )
        if self.recording_manager is not None:
            await self._send_event(
                websocket,
                "recording_status",
                self.recording_manager.get_status(),
            )
        if self.control_service is not None:
            await self._send_event(
                websocket, "control_catalog", self.control_service.catalog()
            )
            await self._send_event(
                websocket, "control_snapshot", self.control_service.snapshot()
            )

        devices = self.shared_state.get("devices_state", {})
        if devices:
            await self._send_event(websocket, "devices_snapshot", devices)

        history = list(self.shared_state.get("telemetry_history", []))[
            -self.HISTORY_LIMIT :
        ]
        if history:
            await self._send_event(
                websocket, "telemetry_history", {"batches": history}
            )

    async def _handle_request(self, websocket, request: dict) -> None:
        action = request.get("action")
        payload = request.get("payload", {})
        request_id = request.get("request_id")
        if not isinstance(payload, dict):
            raise TypeError("payload должен быть JSON-объектом")

        if action == "get_list":
            await self._send_event(
                websocket,
                "file_list",
                {"files": await self.engine.get_list()},
                request_id,
            )
        elif action == "get_devices":
            await self._send_event(
                websocket,
                "devices_snapshot",
                self.shared_state.get("devices_state", {}),
                request_id,
            )
        elif action == "get_control_context":
            if self.control_service is None:
                raise RuntimeError("Канал управления не настроен")
            await self._send_event(
                websocket,
                "control_context",
                {
                    "catalog": self.control_service.catalog(),
                    "snapshot": self.control_service.snapshot(),
                },
                request_id,
            )
        elif action == "get_control_snapshot":
            if self.control_service is None:
                raise RuntimeError("Канал управления не настроен")
            await self._send_event(
                websocket,
                "control_snapshot",
                self.control_service.snapshot(),
                request_id,
            )
        elif action == "get_recording_status":
            if self.recording_manager is None:
                raise RuntimeError("Менеджер записи не настроен")
            await self._send_event(
                websocket,
                "recording_status",
                self.recording_manager.get_status(),
                request_id,
            )
        elif action == "list_recordings":
            if self.recording_manager is None:
                raise RuntimeError("Менеджер записи не настроен")
            await self._send_event(
                websocket,
                "recordings_list",
                {"recordings": self.recording_manager.list_recordings()},
                request_id,
            )
        elif action == "start_recording":
            if self.recording_manager is None:
                raise RuntimeError("Менеджер записи не настроен")
            await self.recording_manager.start_recording(
                duration_seconds=payload.get("duration_seconds"),
                label=payload.get("label", ""),
            )
            await self._send_event(
                websocket, "request_ok", {"action": action}, request_id
            )
        elif action == "stop_recording":
            if self.recording_manager is None:
                raise RuntimeError("Менеджер записи не настроен")
            await self.recording_manager.stop_recording(reason="manual")
            await self._send_event(
                websocket, "request_ok", {"action": action}, request_id
            )
        elif action in {
            "execute_command",
            "request_message",
            "read_parameter",
            "write_parameter",
            "read_all_parameters",
            "service_start",
            "service_update",
            "service_stop",
        }:
            if self.control_service is None:
                raise RuntimeError("Канал управления не настроен")
            result = await self.control_service.handle_action(
                action, payload, websocket, request_id
            )
            await self._send_event(websocket, "request_ok", result, request_id)
        elif action == "remove_file":
            await self.engine.remove_file(payload.get("filename"))
            await self.broadcast_event(
                "file_list", {"files": await self.engine.get_list()}
            )
        elif action == "set_coefficients":
            self.shared_state["config"]["x"] = float(payload.get("x", 1.0))
            self.shared_state["config"]["y"] = float(payload.get("y", 1.0))
            await self._send_event(
                websocket, "request_ok", {"action": action}, request_id
            )
        elif action == "read_file":
            filename = payload.get("filename")
            await self._send_event(
                websocket,
                "file_content",
                {
                    "filename": filename,
                    "content": await self.engine.read_file(filename),
                },
                request_id,
            )
        elif action == "save_and_format":
            filename = payload.get("filename")
            content, formatter_logs = await self.engine.save_and_format(
                filename, payload.get("content")
            )
            await self.broadcast_event(
                "file_list", {"files": await self.engine.get_list()}
            )
            await self._send_event(
                websocket,
                "file_content",
                {"filename": filename, "content": content},
                request_id,
            )
            await self._send_event(
                websocket,
                "editor_console",
                {"msg": formatter_logs},
                request_id,
            )
        elif action in {"run_script", "test_script"}:
            code = payload.get("code")
            if code is None and payload.get("filename"):
                code = await self.engine.read_file(payload.get("filename"))
            result = await self.engine.run_code(code)
            await self._send_event(websocket, "script_result", result, request_id)
        elif action:
            await self._send_event(
                websocket,
                "request_error",
                {"message": f"Неизвестное действие: {action}"},
                request_id,
            )
        else:
            raise ValueError("Поле action не задано")

    async def _handle_connection(self, websocket) -> None:
        self.ws_connections.add(websocket)
        logger.info("Новое WS-подключение. Клиентов: %d", len(self.ws_connections))
        try:
            await self._send_initial_state(websocket)
            async for message in websocket:
                request_id = None
                request = None
                try:
                    request = json.loads(message)
                    if not isinstance(request, dict):
                        raise TypeError("Запрос должен быть JSON-объектом")
                    request_id = request.get("request_id")
                    await self._handle_request(websocket, request)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    logger.warning("Некорректный WS-запрос: %s", exc)
                    await self._send_event(
                        websocket,
                        "request_error",
                        {
                            "message": str(exc),
                            "action": (
                                request.get("action")
                                if isinstance(request, dict)
                                else None
                            ),
                        },
                        request_id,
                    )
                except Exception as exc:
                    logger.exception("WS handler error")
                    await self._send_event(
                        websocket,
                        "request_error",
                        {
                            "message": str(exc),
                            "action": (
                                request.get("action")
                                if isinstance(request, dict)
                                else None
                            ),
                        },
                        request_id,
                    )
        finally:
            if self.control_service is not None:
                await self.control_service.release_owner(websocket)
            self.ws_connections.discard(websocket)
            logger.info("WS-клиент отключился. Клиентов: %d", len(self.ws_connections))

    async def start(self) -> None:
        async with websockets.serve(self._handle_connection, self.host, self.port):
            logger.info("WebSocket-сервер на %s:%d", self.host, self.port)
            await asyncio.Future()
