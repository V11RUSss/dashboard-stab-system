"""
Главная точка входа бэкенда.
"""

import asyncio
import logging
import os
from core.storage import DataStorage
from core.script_engine import ScriptEngine
from core.ws_server import WebSocketServer
from core.tcp_server import TCPGatewayServer

STORAGE_DIR = "storage"
DATA_DIR    = os.path.join(STORAGE_DIR, "data")
SCRIPTS_DIR = os.path.join(STORAGE_DIR, "scripts")
LOGS_DIR    = os.path.join(STORAGE_DIR, "logs")

for d in [DATA_DIR, SCRIPTS_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)

LOG_FILE_PATH = os.path.join(LOGS_DIR, "backend.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Инициализация бэкенда...")

    shared_state = {
        "config":        {"x": 1.0, "y": 1.0},
        "last_payload":  {},
        "devices_state": {},   # device_id → последний decoded dict
    }

    storage = DataStorage(STORAGE_DIR)
    script_engine = ScriptEngine(
        scripts_dir=SCRIPTS_DIR,
        shared_state=shared_state,
        storage_data=storage.recent_data,
    )

    ws_server = WebSocketServer(
        host="0.0.0.0",
        port=8765,
        shared_state=shared_state,
        engine=script_engine,
        logs_path=LOG_FILE_PATH,
        storage=storage,
    )

    tcp_server = TCPGatewayServer(
        host="0.0.0.0",
        port=8888,
        shared_state=shared_state,
        storage=storage,
        engine=script_engine,
        ws_server=ws_server,
    )

    logger.info("Запуск фоновых процессов...")
    await storage.start_writer()
    await asyncio.gather(ws_server.start(), tcp_server.start())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка.")
