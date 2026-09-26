"""On-demand, crash-aware recording of trusted raw TLV telemetry."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import shutil
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.mj_decoder import DEVICE_NAMES, TLV_MESSAGE_NAMES

logger = logging.getLogger(__name__)

CATALOG_SCHEMA_VERSION = 1
RECORDING_FORMAT = "bro30-recording-batch-v1"
ALLOWED_DURATIONS_S = {60, 300, 600, 1800, 3600}
_STOP = object()


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RecordingSession:
    recording_id: str
    label: str
    started_at: datetime
    planned_end_at: datetime | None
    duration_seconds: int | None
    relative_path: str
    final_path: Path
    partial_path: Path
    queue: asyncio.Queue
    writer_task: asyncio.Task | None = None
    timer_task: asyncio.Task | None = None
    packets_captured: int = 0
    packets_written: int = 0
    raw_bytes: int = 0
    file_size_bytes: int = 0
    dropped_packets: int = 0
    message_counts: Counter = field(default_factory=Counter)
    source_counts: Counter = field(default_factory=Counter)


StatusCallback = Callable[[dict], Awaitable[None]]


class RecordingManager:
    """Own one global recording session and its metadata catalog."""

    def __init__(
        self,
        storage_root: str | Path = "storage",
        *,
        queue_maxsize: int = 100_000,
        batch_size: int = 1_000,
        min_free_bytes: int = 10 * 1024**3,
        disk_check_interval_s: float = 2.0,
    ) -> None:
        self.recordings_dir = Path(storage_root) / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.recordings_dir / "index.json"
        self.queue_maxsize = queue_maxsize
        self.batch_size = batch_size
        self.min_free_bytes = min_free_bytes
        self.disk_check_interval_s = disk_check_interval_s

        self._lock = asyncio.Lock()
        self._active: RecordingSession | None = None
        self._state = "idle"
        self._accepting = False
        self._last_error: str | None = None
        self._last_completed: dict | None = None
        self._status_callback: StatusCallback | None = None
        self._monitor_task: asyncio.Task | None = None
        self._abort_task: asyncio.Task | None = None

        self._rate_started = time.monotonic()
        self._rate_packets = 0
        self._rate_bytes = 0
        self._packets_per_second = 0.0
        self._bytes_per_second = 0.0

        catalog_existed = self.index_path.exists()
        self._catalog = self._load_catalog()
        if self._recover_interrupted_entries() or not catalog_existed:
            self._write_catalog_sync()

    def set_status_callback(self, callback: StatusCallback) -> None:
        self._status_callback = callback

    async def start(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._monitor_loop(), name="recording-monitor"
            )

    async def close(self) -> None:
        if self._active is not None:
            await self.stop_recording(reason="backend_shutdown")
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

    def _empty_catalog(self) -> dict:
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "updated_at": iso_utc(utc_now()),
            "recordings": [],
        }

    def _load_catalog(self) -> dict:
        if not self.index_path.exists():
            return self._empty_catalog()
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(
                data.get("recordings"), list
            ):
                raise TypeError("неверная структура каталога")
            if data.get("schema_version") != CATALOG_SCHEMA_VERSION:
                raise ValueError("неподдерживаемая версия каталога")
            return data
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            backup = self.index_path.with_suffix(".json.bak")
            logger.error("Не удалось прочитать %s: %s", self.index_path, exc)
            if backup.exists():
                try:
                    data = json.loads(backup.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and isinstance(
                        data.get("recordings"), list
                    ):
                        logger.warning("Каталог восстановлен из %s", backup)
                        return data
                except (OSError, ValueError, json.JSONDecodeError):
                    logger.exception("Резервный каталог также повреждён")
            return self._empty_catalog()

    def _recover_interrupted_entries(self) -> bool:
        changed = False
        now = utc_now()
        for entry in self._catalog["recordings"]:
            if entry.get("status") not in {"recording", "finalizing"}:
                continue
            relative = entry.get("partial_path") or entry.get("path")
            path = self.recordings_dir / relative if relative else None
            ended_at = now
            size = 0
            if path and path.exists():
                stat = path.stat()
                size = stat.st_size
                ended_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            started_at = self._parse_iso(entry.get("started_at"))
            entry.update(
                {
                    "status": "interrupted",
                    "ended_at": iso_utc(ended_at),
                    "duration_seconds": (
                        max(0.0, (ended_at - started_at).total_seconds())
                        if started_at
                        else None
                    ),
                    "file_size_bytes": size,
                    "stop_reason": "backend_restart",
                    "incomplete": True,
                }
            )
            changed = True
        return changed

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _write_catalog_sync(self) -> None:
        self._catalog["updated_at"] = iso_utc(utc_now())
        temporary = self.index_path.with_suffix(".json.tmp")
        backup = self.index_path.with_suffix(".json.bak")
        encoded = json.dumps(
            self._catalog, ensure_ascii=False, indent=2, allow_nan=False
        )
        with temporary.open("w", encoding="utf-8") as target:
            target.write(encoded)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        if self.index_path.exists():
            shutil.copy2(self.index_path, backup)
        os.replace(temporary, self.index_path)

    async def _save_catalog(self) -> None:
        await asyncio.to_thread(self._write_catalog_sync)

    def list_recordings(self) -> list[dict]:
        return [dict(item) for item in reversed(self._catalog["recordings"]) if item.get("status") != "deleted"]

    async def delete_recording(self, recording_id: str) -> dict:
        """Remove from the catalogue view; retain file in local trash for recovery."""
        from tools.recordings import recording_path
        async with self._lock:
            entry = next((r for r in self._catalog["recordings"] if r["id"] == recording_id), None)
            if not entry or entry.get("status") == "deleted":
                raise ValueError("Запись не найдена")
            if entry.get("status") in {"recording", "finalizing"}:
                raise ValueError("Нельзя удалить активную запись")
            source = recording_path(self.recordings_dir, entry)
            trash = self.recordings_dir / ".trash"
            trash.mkdir(exist_ok=True)
            destination = trash / source.name
            previous = dict(entry)
            await asyncio.to_thread(os.replace, source, destination)
            entry.update(status="deleted", deleted_at=iso_utc(utc_now()),
                         original_path=entry.get("path"),
                         path=destination.relative_to(self.recordings_dir).as_posix(), partial_path=None)
            try:
                await self._save_catalog()
            except Exception:
                entry.clear()
                entry.update(previous)
                await asyncio.to_thread(os.replace, destination, source)
                raise
        if self._last_completed and self._last_completed.get("id") == recording_id:
            self._last_completed = None
        return {"deleted": recording_id}

    def get_status(self) -> dict:
        active = self._active
        status = {
            "state": self._state,
            "last_error": self._last_error,
            "last_completed": self._last_completed,
            "packets_per_second": round(self._packets_per_second, 1),
            "bytes_per_second": round(self._bytes_per_second, 1),
            "min_free_bytes": self.min_free_bytes,
        }
        if active is None:
            return status
        status.update(
            {
                "recording_id": active.recording_id,
                "label": active.label,
                "started_at": iso_utc(active.started_at),
                "planned_end_at": (
                    iso_utc(active.planned_end_at)
                    if active.planned_end_at
                    else None
                ),
                "requested_duration_seconds": active.duration_seconds,
                "packets_captured": active.packets_captured,
                "packets_written": active.packets_written,
                "raw_bytes": active.raw_bytes,
                "file_size_bytes": active.file_size_bytes,
                "queue_depth": active.queue.qsize(),
                "dropped_packets": active.dropped_packets,
            }
        )
        return status

    async def _notify(self) -> None:
        if self._status_callback is None:
            return
        try:
            await self._status_callback(self.get_status())
        except Exception:
            logger.exception("Не удалось отправить состояние записи")

    def observe_and_capture(self, raw_frame: bytes, tlv: dict) -> None:
        """Observe ingress rate and enqueue a frame only while recording."""
        self._observe_rate(len(raw_frame))
        session = self._active
        if not self._accepting or session is None:
            return

        record = (time.time_ns(), bytes(raw_frame))
        try:
            session.queue.put_nowait(record)
        except asyncio.QueueFull:
            session.dropped_packets += 1
            self._accepting = False
            self._last_error = "Очередь записи переполнена"
            if self._abort_task is None or self._abort_task.done():
                self._abort_task = asyncio.create_task(
                    self.stop_recording(
                        reason="queue_overflow", final_status="error"
                    )
                )
            return

        session.packets_captured += 1
        session.raw_bytes += len(raw_frame)
        message_name = TLV_MESSAGE_NAMES.get(tlv.get("msg_type"), "unknown")
        session.message_counts[message_name] += 1
        source = DEVICE_NAMES.get(tlv.get("src_id"))
        source_name = source[0] if source else f"0x{tlv.get('src_id', 0):04X}"
        session.source_counts[source_name] += 1

    def _observe_rate(self, frame_size: int) -> None:
        self._rate_packets += 1
        self._rate_bytes += frame_size
        now = time.monotonic()
        elapsed = now - self._rate_started
        if elapsed < 1.0:
            return
        packet_rate = self._rate_packets / elapsed
        byte_rate = self._rate_bytes / elapsed
        if self._packets_per_second:
            self._packets_per_second = self._packets_per_second * 0.6 + packet_rate * 0.4
            self._bytes_per_second = self._bytes_per_second * 0.6 + byte_rate * 0.4
        else:
            self._packets_per_second = packet_rate
            self._bytes_per_second = byte_rate
        self._rate_started = now
        self._rate_packets = 0
        self._rate_bytes = 0

    async def start_recording(
        self, *, duration_seconds: int | None = None, label: str = ""
    ) -> dict:
        if duration_seconds is not None:
            try:
                duration_seconds = int(duration_seconds)
            except (TypeError, ValueError) as exc:
                raise ValueError("Некорректная длительность записи") from exc
            if duration_seconds not in ALLOWED_DURATIONS_S:
                raise ValueError("Разрешено 1, 5, 10, 30 или 60 минут")

        label = " ".join(str(label or "").split())[:80]
        async with self._lock:
            if self._active is not None or self._state != "idle":
                raise RuntimeError("Запись уже активна")

            usage = await asyncio.to_thread(shutil.disk_usage, self.recordings_dir)
            if usage.free <= self.min_free_bytes:
                raise OSError("Недостаточно свободного места для начала записи")

            estimated_size = self._estimate_size(duration_seconds)
            if estimated_size and estimated_size > usage.free - self.min_free_bytes:
                needed_gb = estimated_size / 1024**3
                available_gb = (usage.free - self.min_free_bytes) / 1024**3
                raise OSError(
                    f"Для записи нужно около {needed_gb:.1f} ГБ, "
                    f"доступно с учётом резерва {available_gb:.1f} ГБ"
                )

            started_at = utc_now()
            recording_id = uuid.uuid4().hex
            month_dir = self.recordings_dir / started_at.strftime("%Y-%m")
            month_dir.mkdir(parents=True, exist_ok=True)
            filename = (
                f"rec_{started_at.strftime('%Y%m%dT%H%M%SZ')}_"
                f"{recording_id[:8]}.pkl"
            )
            final_path = month_dir / filename
            partial_path = final_path.with_suffix(".pkl.part")
            relative_path = final_path.relative_to(self.recordings_dir).as_posix()
            planned_end_at = (
                started_at + timedelta(seconds=duration_seconds)
                if duration_seconds
                else None
            )
            session = RecordingSession(
                recording_id=recording_id,
                label=label,
                started_at=started_at,
                planned_end_at=planned_end_at,
                duration_seconds=duration_seconds,
                relative_path=relative_path,
                final_path=final_path,
                partial_path=partial_path,
                queue=asyncio.Queue(maxsize=self.queue_maxsize),
            )
            self._active = session
            self._state = "recording"
            self._accepting = True
            self._last_error = None

            entry = {
                "id": recording_id,
                "label": label,
                "status": "recording",
                "format": RECORDING_FORMAT,
                "path": relative_path,
                "partial_path": partial_path.relative_to(
                    self.recordings_dir
                ).as_posix(),
                "started_at": iso_utc(started_at),
                "planned_end_at": (
                    iso_utc(planned_end_at) if planned_end_at else None
                ),
                "requested_duration_seconds": duration_seconds,
                "ended_at": None,
                "duration_seconds": None,
                "file_size_bytes": 0,
                "packet_count": 0,
                "raw_bytes": 0,
                "message_counts": {},
                "source_counts": {},
                "dropped_packets": 0,
                "stop_reason": None,
                "incomplete": False,
            }
            self._catalog["recordings"].append(entry)
            try:
                await self._save_catalog()
            except Exception:
                self._catalog["recordings"].pop()
                self._active = None
                self._state = "idle"
                self._accepting = False
                raise

            session.writer_task = asyncio.create_task(
                self._writer_loop(session), name=f"recording-writer-{recording_id[:8]}"
            )
            if duration_seconds:
                session.timer_task = asyncio.create_task(
                    self._timer_stop(recording_id, duration_seconds),
                    name=f"recording-timer-{recording_id[:8]}",
                )

        logger.info(
            "Запись телеметрии начата: id=%s duration=%s path=%s",
            recording_id,
            duration_seconds or "manual",
            relative_path,
        )
        await self._notify()
        return self.get_status()

    def _estimate_size(self, duration_seconds: int | None) -> int:
        if not duration_seconds or not self._bytes_per_second:
            return 0
        # Batched pickle adds about 15 bytes per packet to the raw TLV size.
        serialized_rate = self._bytes_per_second + self._packets_per_second * 15
        return int(serialized_rate * duration_seconds * 1.1)

    async def _timer_stop(self, recording_id: str, duration_seconds: int) -> None:
        try:
            await asyncio.sleep(duration_seconds)
            if self._active and self._active.recording_id == recording_id:
                await self.stop_recording(reason="timer")
        except asyncio.CancelledError:
            pass

    async def _writer_loop(self, session: RecordingSession) -> None:
        handle = await asyncio.to_thread(session.partial_path.open, "wb")
        try:
            while True:
                item = await session.queue.get()
                if item is _STOP:
                    break
                batch = [item]
                stop_after_batch = False
                while len(batch) < self.batch_size:
                    try:
                        queued = session.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if queued is _STOP:
                        stop_after_batch = True
                        break
                    batch.append(queued)
                size = await asyncio.to_thread(self._write_batch, handle, batch)
                session.packets_written += len(batch)
                session.file_size_bytes = size
                if stop_after_batch:
                    break
        finally:
            await asyncio.to_thread(self._flush_close, handle)
            if session.partial_path.exists():
                session.file_size_bytes = session.partial_path.stat().st_size

    @staticmethod
    def _write_batch(handle, batch: list[tuple[int, bytes]]) -> int:
        pickle.dump(
            {"format": RECORDING_FORMAT, "records": batch},
            handle,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        return handle.tell()

    @staticmethod
    def _flush_close(handle) -> None:
        try:
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()

    async def stop_recording(
        self, *, reason: str = "manual", final_status: str = "completed"
    ) -> dict:
        async with self._lock:
            session = self._active
            if session is None:
                return self.get_status()

            self._accepting = False
            self._state = "finalizing"
            timer = session.timer_task
            current_task = asyncio.current_task()
            if timer is not None and timer is not current_task:
                timer.cancel()

            self._update_catalog_entry(
                session.recording_id, {"status": "finalizing"}
            )
            await self._save_catalog()
            await self._notify()

            if session.writer_task is None or not session.writer_task.done():
                await session.queue.put(_STOP)
            writer_error: Exception | None = None
            try:
                if session.writer_task is not None:
                    await session.writer_task
            except Exception as exc:
                writer_error = exc
                final_status = "error"
                reason = "write_error"
                self._last_error = str(exc)
                logger.exception("Ошибка записи телеметрии")

            if session.dropped_packets and final_status == "completed":
                final_status = "error"
                reason = "queue_overflow"

            ended_at = utc_now()
            if writer_error is None:
                await asyncio.to_thread(os.replace, session.partial_path, session.final_path)
                session.file_size_bytes = session.final_path.stat().st_size

            incomplete = final_status != "completed" or session.dropped_packets > 0
            entry_update = {
                "status": final_status,
                "ended_at": iso_utc(ended_at),
                "duration_seconds": max(
                    0.0, (ended_at - session.started_at).total_seconds()
                ),
                "file_size_bytes": session.file_size_bytes,
                "packet_count": session.packets_written,
                "raw_bytes": session.raw_bytes,
                "message_counts": dict(session.message_counts),
                "source_counts": dict(session.source_counts),
                "dropped_packets": session.dropped_packets,
                "stop_reason": reason,
                "incomplete": incomplete,
            }
            if writer_error is None:
                entry_update["partial_path"] = None
            entry = self._update_catalog_entry(session.recording_id, entry_update)
            await self._save_catalog()

            self._last_completed = dict(entry) if entry else None
            self._active = None
            self._state = "idle"
            self._accepting = False

        logger.info(
            "Запись телеметрии завершена: id=%s packets=%d size=%d reason=%s",
            session.recording_id,
            session.packets_written,
            session.file_size_bytes,
            reason,
        )
        await self._notify()
        return self.get_status()

    def _update_catalog_entry(self, recording_id: str, values: dict) -> dict | None:
        for entry in self._catalog["recordings"]:
            if entry.get("id") == recording_id:
                entry.update(values)
                return entry
        return None

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self.disk_check_interval_s)
            session = self._active
            if session is None:
                continue
            if session.writer_task is not None and session.writer_task.done():
                try:
                    error = session.writer_task.exception()
                except asyncio.CancelledError:
                    error = RuntimeError("Процесс записи остановлен")
                if error is not None:
                    self._accepting = False
                    self._last_error = str(error)
                    await self.stop_recording(
                        reason="write_error", final_status="error"
                    )
                    continue
            usage = await asyncio.to_thread(shutil.disk_usage, self.recordings_dir)
            if usage.free <= self.min_free_bytes:
                self._accepting = False
                self._last_error = "Запись остановлена: достигнут резерв диска"
                await self.stop_recording(
                    reason="low_disk_space", final_status="error"
                )
                continue
            await self._notify()
