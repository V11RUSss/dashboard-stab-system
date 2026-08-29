"""TLV stream server for binary MicroJet messages from the C++ gateway."""

import asyncio
import logging
import time
from collections import deque
from contextlib import suppress
from typing import TYPE_CHECKING

from core.mj_decoder import (
    TLV_BUP_STATE,
    TLV_DCU_TEL,
    TLV_HEADER_SIZE,
    TLV_MESSAGE_NAMES,
    TLV_PAYLOAD_SIZES,
    decode,
    parse_tlv_header,
)
from core.recording_manager import RecordingManager
from core.state import update_device_state, utc_now_iso
from core.telemetry_aggregator import TelemetryAggregator

if TYPE_CHECKING:
    from core.control_service import ControlService
    from core.ws_server import WebSocketServer

logger = logging.getLogger(__name__)


class TCPGatewayServer:
    """Receive, validate and decode the gateway's framed TCP stream."""

    AGGREGATE_INTERVAL_S = 0.1
    DEVICE_CARD_INTERVAL_S = 0.5
    STATUS_BROADCAST_INTERVAL_S = 1.0
    COOPERATIVE_YIELD_EVERY = 128
    HISTORY_LIMIT = 100
    MAX_PAYLOAD_SIZE = 4096

    def __init__(
        self,
        host: str,
        port: int,
        shared_state: dict,
        recording_manager: RecordingManager,
        ws_server: "WebSocketServer",
        control_service: "ControlService | None" = None,
    ) -> None:
        self.host = host
        self.port = port
        self.shared_state = shared_state
        self.recording_manager = recording_manager
        self.ws_server = ws_server
        self.control_service = control_service
        self.aggregator = TelemetryAggregator(self.DEVICE_CARD_INTERVAL_S)
        self._last_status_broadcast = 0.0
        self._packets_at_last_status = 0
        self._connection_count = 0
        self._publisher_task: asyncio.Task | None = None
        self._gateway_writers: list[asyncio.StreamWriter] = []
        self._writer_lock = asyncio.Lock()
        self.shared_state.setdefault(
            "telemetry_history", deque(maxlen=self.HISTORY_LIMIT)
        )

    def _gateway_status(self) -> dict:
        return self.shared_state.setdefault(
            "gateway_status",
            {
                "connected": False,
                "connection_count": 0,
                "peer": None,
                "connected_at": None,
                "disconnected_at": None,
                "last_packet_at": None,
                "packets_received": 0,
                "packets_decoded": 0,
                "packets_rejected": 0,
                "control_frames_sent": 0,
            },
        )

    async def send_control_frame(self, frame: bytes) -> None:
        """Write one already validated frame to the active Gateway socket."""
        async with self._writer_lock:
            if not self._gateway_writers:
                raise RuntimeError("Gateway не подключён")
            writer = self._gateway_writers[-1]
            writer.write(frame)
            await writer.drain()
            self._gateway_status()["control_frames_sent"] = (
                self._gateway_status().get("control_frames_sent", 0) + 1
            )

    async def _broadcast_status(self) -> None:
        status_source = self._gateway_status()
        packet_count = status_source["packets_received"]
        if packet_count != self._packets_at_last_status:
            status_source["last_packet_at"] = utc_now_iso()
            self._packets_at_last_status = packet_count
        status = dict(self._gateway_status())
        recording = self.recording_manager.get_status()
        status["recording_queue_depth"] = recording.get("queue_depth", 0)
        await self.ws_server.broadcast_event("system_status", {"gateway": status})

    async def _publish_updates(self, *, force_status: bool = False) -> None:
        """Publish the low-rate UI view without touching the raw archive."""
        series = self.aggregator.take_telemetry_series()
        if series:
            payload = {
                "interval_ms": round(self.AGGREGATE_INTERVAL_S * 1000),
                "series": series,
            }
            self.shared_state["telemetry_history"].append(payload)
            await self.ws_server.broadcast_event("telemetry_batch", payload)

        device_updates = self.aggregator.take_device_updates()
        if device_updates:
            for decoded in device_updates:
                received_at = decoded["received_at"]
                update_device_state(self.shared_state, decoded, received_at)
            self.shared_state["last_payload"] = device_updates[-1].copy()
            await self.ws_server.broadcast_event(
                "devices_batch", {"devices": device_updates}
            )

        now = time.monotonic()
        if force_status or (
            now - self._last_status_broadcast
            >= self.STATUS_BROADCAST_INTERVAL_S
        ):
            self._last_status_broadcast = now
            await self._broadcast_status()

    async def _publisher_loop(self) -> None:
        while True:
            await asyncio.sleep(self.AGGREGATE_INTERVAL_S)
            try:
                await self._publish_updates()
            except Exception:
                # One malformed UI update must not permanently stop all
                # subsequent aggregate windows.
                logger.exception("Ошибка публикации агрегированной телеметрии")

    async def _handle_stream(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        addr = writer.get_extra_info("peername")
        logger.info("Шлюз подключился: %s", addr)

        self._connection_count += 1
        async with self._writer_lock:
            self._gateway_writers.append(writer)
        status = self._gateway_status()
        status.update(
            {
                "connected": True,
                "connection_count": self._connection_count,
                "peer": str(addr),
                "connected_at": utc_now_iso(),
            }
        )
        if self.control_service is not None:
            await self.control_service.set_gateway_connected(True)
        await self._broadcast_status()

        try:
            while True:
                header_bytes = await reader.readexactly(TLV_HEADER_SIZE)
                tlv = parse_tlv_header(header_bytes)
                if tlv is None:
                    logger.error("Не удалось разобрать TLV-заголовок")
                    break

                payload_length = tlv["length"]
                if payload_length > self.MAX_PAYLOAD_SIZE:
                    logger.warning("Неверная длина payload: %d", payload_length)
                    break

                payload = await reader.readexactly(payload_length)
                self.recording_manager.observe_and_capture(
                    header_bytes + payload, tlv
                )

                status["packets_received"] += 1
                if (
                    status["packets_received"]
                    % self.COOPERATIVE_YIELD_EVERY
                    == 0
                ):
                    # A continuously readable TCP buffer must not starve the
                    # writer, WebSocket publisher and the rest of the loop.
                    await asyncio.sleep(0)

                expected_size = TLV_PAYLOAD_SIZES.get(tlv["msg_type"])
                if expected_size is None:
                    status["packets_rejected"] += 1
                    logger.warning("Неизвестный TLV type: 0x%02X", tlv["msg_type"])
                    continue
                if payload_length != expected_size:
                    status["packets_rejected"] += 1
                    logger.warning(
                        "Неверный размер %s: %d, ожидался %d",
                        TLV_MESSAGE_NAMES[tlv["msg_type"]],
                        payload_length,
                        expected_size,
                    )
                    continue
                if tlv["ts_ns"] >= 1_000_000_000:
                    status["packets_rejected"] += 1
                    logger.warning("Неверная наносекундная метка: %d", tlv["ts_ns"])
                    continue

                decoded = decode(tlv, payload)
                if not decoded:
                    status["packets_rejected"] += 1
                    continue

                status["packets_decoded"] += 1
                if tlv["msg_type"] in {TLV_DCU_TEL, TLV_BUP_STATE}:
                    self.aggregator.add(decoded)
                elif self.control_service is not None:
                    await self.control_service.handle_gateway_message(decoded)

        except asyncio.IncompleteReadError:
            logger.info("Шлюз отключился: %s", addr)
        except Exception:
            logger.exception("TCP stream error")
        finally:
            # Direct unit tests and short-lived connections still receive the
            # final, possibly partial, 100 ms aggregation window.
            await self._publish_updates()
            self._connection_count = max(0, self._connection_count - 1)
            async with self._writer_lock:
                if writer in self._gateway_writers:
                    self._gateway_writers.remove(writer)
            status = self._gateway_status()
            status["connection_count"] = self._connection_count
            if self._connection_count == 0:
                status.update(
                    {
                        "connected": False,
                        "peer": None,
                        "disconnected_at": utc_now_iso(),
                    }
                )
            if self.control_service is not None:
                await self.control_service.set_gateway_connected(
                    bool(self._gateway_writers)
                )
            await self._broadcast_status()

            writer.close()
            try:
                await writer.wait_closed()
            except OSError as exc:
                logger.debug("Ошибка закрытия TCP-соединения: %s", exc)
            logger.info("TCP-соединение с %s закрыто.", addr)

    async def start(self) -> None:
        server = await asyncio.start_server(self._handle_stream, self.host, self.port)
        logger.info("TCP Шлюз-Сервер на %s:%d", self.host, self.port)
        self._publisher_task = asyncio.create_task(self._publisher_loop())
        try:
            async with server:
                await server.serve_forever()
        finally:
            self._publisher_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._publisher_task
