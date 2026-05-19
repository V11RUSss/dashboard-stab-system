"""
Хранилище данных — без изменений структуры,
только убран старый JSON-парсинг (теперь decode делает tcp_server).
"""

import os
import pickle
from datetime import datetime
import logging
from collections import deque
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)


class DataStorage:
    """Управление сохранением бинарных данных на диск."""

    def __init__(self, storage_root: str = "storage") -> None:
        self.data_dir = os.path.join(storage_root, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.filename = os.path.join(self.data_dir, "raw_data.pkl")

        self.queue       = asyncio.Queue()
        self.recent_data = deque(maxlen=1000)   # последние 1000 decoded dict
        self._writer_task = None

    async def start_writer(self) -> None:
        self._writer_task = asyncio.create_task(self._write_loop())
        logger.info("Фоновый процесс записи запущен.")

    async def _write_loop(self) -> None:
        while True:
            batch = []
            item = await self.queue.get()
            batch.append(item)
            while not self.queue.empty() and len(batch) < 100:
                batch.append(self.queue.get_nowait())
            try:
                await asyncio.to_thread(self._sync_write, batch)
            except Exception as e:
                logger.error("Ошибка записи: %s", e)
            for _ in batch:
                self.queue.task_done()

    def _sync_write(self, batch: list) -> None:
        with open(self.filename, "ab") as f:
            for raw_bytes in batch:
                pickle.dump({"timestamp": datetime.now(), "bytes": raw_bytes}, f)

    async def store_raw(self, raw_bytes: bytes) -> None:
        """Сохранить сырой TLV-фрейм на диск (для отладки)."""
        await self.queue.put(raw_bytes)

    def validate_and_convert(self, raw_bytes: bytes) -> Optional[dict]:
        """Оставлено для совместимости — не используется для MJ-пакетов."""
        return None
