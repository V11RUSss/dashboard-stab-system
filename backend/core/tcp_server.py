"""
tcp_server.py — TLV-парсер для приёма бинарных MJ-пакетов от C++ gateway.

Протокол (gateway → backend):
  [uint8  type   ] — 0x01=DCU_TEL, 0x02=BUP_STATE
  [uint16 src_id ]
  [uint16 dst_id ]
  [uint32 ts_s   ]
  [uint32 ts_ns  ]
  [uint16 length ]  ← число байт payload
  [bytes  payload]
Итого заголовок = 15 байт.
"""

import asyncio
import struct
import logging
import time
from datetime import datetime, timezone

from core.storage import DataStorage
from core.script_engine import ScriptEngine
from core.ws_server import WebSocketServer
from core.mj_decoder import parse_tlv_header, decode, TLV_HEADER_SIZE

logger = logging.getLogger(__name__)

TLV_HEADER_FMT  = "<BHHIIH"   # 15 bytes


class TCPGatewayServer:
    """
    Принимает TLV-stream от C++ шлюза.
    Каждый TLV-фрейм декодируется и рассылается через WebSocket.
    """

    UI_BROADCAST_INTERVAL_S = 0.05

    def __init__(
        self,
        host: str,
        port: int,
        shared_state: dict,
        storage: DataStorage,
        engine: ScriptEngine,
        ws_server: WebSocketServer,
    ) -> None:
        self.host         = host
        self.port         = port
        self.shared_state = shared_state
        self.storage      = storage
        self.engine       = engine
        self.ws_server    = ws_server
        self._last_broadcast_by_device = {}

    async def _handle_stream(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        addr = writer.get_extra_info("peername")
        logger.info("Шлюз подключился: %s", addr)

        try:
            while True:
                # 1. Читаем фиксированный TLV-заголовок (15 байт)
                hdr_bytes = await reader.readexactly(TLV_HEADER_SIZE)
                tlv = parse_tlv_header(hdr_bytes)
                if tlv is None:
                    logger.error("Не удалось разобрать TLV заголовок")
                    break

                payload_len = tlv["length"]
                if payload_len == 0 or payload_len > 4096:
                    logger.warning("Неверная длина payload: %d", payload_len)
                    break

                # 2. Читаем payload
                payload = await reader.readexactly(payload_len)

                # 3. Сохранить сырые байты (для отладки)
                raw_frame = hdr_bytes + payload
                await self.storage.store_raw(raw_frame)

                # 4. Декодировать через ctypes
                decoded = decode(tlv, payload)
                if not decoded:
                    continue

                # 5. Добавить локальное время если нет
                if "timestamp" not in decoded:
                    decoded["timestamp"] = datetime.now(tz=timezone.utc).isoformat()

                # 6. Обновить last_payload и devices_state
                self.shared_state["last_payload"] = decoded.copy()
                dev_id = decoded.get("device_id")
                if dev_id:
                    self.shared_state["devices_state"][dev_id] = decoded.copy()

                # 7. Добавить в history буфер
                self.storage.recent_data.append(decoded)

                # 8. Broadcast через WebSocket. UI не должен получать каждый
                # raw-пакет: Dash callbacks заметно тяжелее бинарного TCP потока.
                now = time.monotonic()
                last_sent = self._last_broadcast_by_device.get(dev_id, 0.0)
                if now - last_sent >= self.UI_BROADCAST_INTERVAL_S:
                    self._last_broadcast_by_device[dev_id] = now
                    await self.ws_server.broadcast({
                        "type":    "device_telemetry",
                        "payload": decoded,
                    })

        except asyncio.IncompleteReadError:
            logger.info("Шлюз отключился: %s", addr)
        except Exception as e:
            logger.error("TCP stream error: %s", e, exc_info=True)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("TCP-соединение с %s закрыто.", addr)

    async def start(self) -> None:
        server = await asyncio.start_server(
            self._handle_stream, self.host, self.port
        )
        logger.info("TCP Шлюз-Сервер на %s:%d", self.host, self.port)
        async with server:
            await server.serve_forever()
