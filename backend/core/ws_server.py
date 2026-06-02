import asyncio
import websockets
import json
import logging
from core.script_engine import ScriptEngine

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
        storage=None,
    ) -> None:
        self.host         = host
        self.port         = port
        self.shared_state = shared_state
        self.engine       = engine
        self.logs_path    = logs_path
        self.storage      = storage
        self.ws_connections = set()

    async def broadcast(self, payload: dict) -> None:
        if not self.ws_connections:
            return
        msg = json.dumps(payload)
        connections = list(self.ws_connections)
        results = await asyncio.gather(
            *(c.send(msg) for c in connections),
            return_exceptions=True,
        )
        for connection, result in zip(connections, results):
            if isinstance(result, Exception):
                self.ws_connections.discard(connection)

    async def _handle_connection(self, websocket) -> None:
        self.ws_connections.add(websocket)
        logger.info("Новое WS-подключение. Клиентов: %d", len(self.ws_connections))
        try:
            # 1. Список скриптов
            files = await self.engine.get_list()
            await websocket.send(json.dumps(
                {"type": "file_list", "payload": {"files": files}}
            ))

            # 2. Текущее состояние всех устройств (snapshot)
            devices = self.shared_state.get("devices_state", {})
            if devices:
                await websocket.send(json.dumps({
                    "type":    "devices_snapshot",
                    "payload": devices,
                }))

            # 3. История телеметрии (последние N точек)
            if self.storage and self.storage.recent_data:
                history = list(self.storage.recent_data)[-self.HISTORY_LIMIT:]
                await websocket.send(json.dumps({
                    "type":    "telemetry_history",
                    "payload": history,
                }))

            async for message in websocket:
                try:
                    req    = json.loads(message)
                    action = req.get("action")
                    payload = req.get("payload", {})

                    if action == "get_list":
                        files = await self.engine.get_list()
                        await websocket.send(json.dumps(
                            {"type": "file_list", "payload": {"files": files}}
                        ))

                    elif action == "get_devices":
                        await websocket.send(json.dumps({
                            "type":    "devices_snapshot",
                            "payload": self.shared_state.get("devices_state", {}),
                        }))

                    elif action == "remove_file":
                        fname = payload.get("filename")
                        if fname:
                            await self.engine.remove_file(fname)
                            await self.broadcast({
                                "type":    "file_list",
                                "payload": {"files": await self.engine.get_list()},
                            })

                    elif action == "set_coefficients":
                        try:
                            self.shared_state["config"]["x"] = float(payload.get("x", 1.0))
                            self.shared_state["config"]["y"] = float(payload.get("y", 1.0))
                        except (ValueError, TypeError):
                            pass

                    elif action == "read_file":
                        content = await self.engine.read_file(payload.get("filename"))
                        await websocket.send(json.dumps({
                            "type":    "file_content",
                            "payload": {
                                "filename": payload.get("filename"),
                                "content":  content,
                            },
                        }))

                    elif action == "save_and_format":
                        fname = payload.get("filename")
                        content, ruff_logs = await self.engine.save_and_format(
                            fname, payload.get("content")
                        )
                        await self.broadcast({
                            "type":    "file_list",
                            "payload": {"files": await self.engine.get_list()},
                        })
                        await websocket.send(json.dumps({
                            "type":    "file_content",
                            "payload": {"filename": fname, "content": content},
                        }))
                        await websocket.send(json.dumps({
                            "type":    "editor_console",
                            "payload": {"msg": ruff_logs},
                        }))

                    elif action == "test_script":
                        script_name = payload.get("filename")
                        code = await self.engine.read_file(script_name)
                        test_input = (
                            self.shared_state.get("last_payload", {}).copy()
                            or {"angle": 0.5, "speed": 1.2, "status": "ARM"}
                        )
                        result, test_logs = await self.engine.dry_run(code, test_input)
                        output = (
                            f"ВХОДНОЙ ПАКЕТ:\n{json.dumps(test_input, ensure_ascii=False)}\n\n"
                            f"ТРАНСФОРМАЦИЯ:\n{json.dumps(result, ensure_ascii=False)}\n\n"
                            f"ЛОГИ:\n{test_logs}"
                        )
                        await websocket.send(json.dumps({
                            "type":    "editor_console",
                            "payload": {"msg": output},
                        }))

                except Exception as ex:
                    logger.error("WS handler error: %s", ex, exc_info=True)

        finally:
            self.ws_connections.discard(websocket)

    async def start(self) -> None:
        async with websockets.serve(self._handle_connection, self.host, self.port):
            logger.info("WebSocket-сервер на %s:%d", self.host, self.port)
            await asyncio.Future()
