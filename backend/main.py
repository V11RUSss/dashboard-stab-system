"""
Главная точка входа бэкенда.
"""

import asyncio
import logging
import os
from collections import deque
from logging.handlers import RotatingFileHandler

from core.recording_manager import RecordingManager
from core.recording_http import RecordingAPI
from core.control_service import ControlService
from core.script_engine import ScriptEngine
from core.tcp_server import TCPGatewayServer
from core.ws_server import WebSocketServer

STORAGE_DIR = "storage"
SCRIPTS_DIR = os.path.join(STORAGE_DIR, "scripts")
LOGS_DIR    = os.path.join(STORAGE_DIR, "logs")

for d in [SCRIPTS_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)

LOG_FILE_PATH = os.path.join(LOGS_DIR, "backend.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_FILE_PATH,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Инициализация бэкенда...")

    shared_state = {
        "config":        {"x": 1.0, "y": 1.0},
        "last_payload":  {},
        "devices_state": {},   # device_id → metadata + messages by type
        "telemetry_history": deque(maxlen=100),
        "gateway_status": {
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
    }

    min_free_gb = float(os.environ.get("RECORDING_MIN_FREE_GB", "10"))
    recording_manager = RecordingManager(
        STORAGE_DIR,
        min_free_bytes=max(0, int(min_free_gb * 1024**3)),
    )
    script_engine = ScriptEngine(
        scripts_dir=SCRIPTS_DIR,
        shared_state=shared_state,
    )
    control_service = ControlService(shared_state)

    ws_server = WebSocketServer(
        host="0.0.0.0",
        port=8765,
        shared_state=shared_state,
        engine=script_engine,
        logs_path=LOG_FILE_PATH,
        recording_manager=recording_manager,
        control_service=control_service,
    )
    recording_manager.set_status_callback(
        lambda status: ws_server.broadcast_event("recording_status", status)
    )

    tcp_server = TCPGatewayServer(
        host="0.0.0.0",
        port=8888,
        shared_state=shared_state,
        recording_manager=recording_manager,
        ws_server=ws_server,
        control_service=control_service,
    )
    control_service.set_sender(tcp_server.send_control_frame)
    control_service.set_broadcaster(ws_server.broadcast_event)

    logger.info("Запуск фоновых процессов...")
    await recording_manager.start()
    recording_api = RecordingAPI(recording_manager)
    try:
        await asyncio.gather(ws_server.start(), tcp_server.start())
    finally:
        await recording_api.close()
        await control_service.close()
        await recording_manager.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка.")
