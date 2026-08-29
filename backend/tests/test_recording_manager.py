import asyncio
import json
import struct
import tempfile
import unittest
from pathlib import Path

from core.mj_decoder import TLV_DCU_TEL, TLV_HEADER_FMT
from core.recording_manager import RecordingManager
from tools.recordings import iter_raw_records


def make_frame(index: int = 0) -> tuple[bytes, dict]:
    payload = bytearray(236)
    struct.pack_into("<bbbb", payload, 0, 5, 5, 1, 3)
    struct.pack_into("<f", payload, 4, float(index))
    header_values = (TLV_DCU_TEL, 0xFA82, 0x6C99, 100, index, len(payload))
    header = struct.pack(TLV_HEADER_FMT, *header_values)
    return header + payload, {
        "msg_type": TLV_DCU_TEL,
        "src_id": 0xFA82,
        "dst_id": 0x6C99,
        "ts_s": 100,
        "ts_ns": index,
        "length": len(payload),
    }


class RecordingManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = RecordingManager(
            self.temp_dir.name,
            min_free_bytes=0,
            batch_size=100,
            disk_check_interval_s=0.02,
        )

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp_dir.cleanup()

    async def test_idle_does_not_create_pkl_and_session_finalizes_atomically(self):
        frame, tlv = make_frame()
        self.manager.observe_and_capture(frame, tlv)
        recordings = Path(self.temp_dir.name) / "recordings"
        self.assertEqual(list(recordings.rglob("*.pkl")), [])

        await self.manager.start_recording(label="Тест углов")
        for index in range(250):
            frame, tlv = make_frame(index)
            self.manager.observe_and_capture(frame, tlv)
        await self.manager.stop_recording(reason="manual")

        entries = self.manager.list_recordings()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["label"], "Тест углов")
        self.assertEqual(entry["packet_count"], 250)
        self.assertEqual(entry["dropped_packets"], 0)
        self.assertEqual(entry["message_counts"], {"dcu_telemetry": 250})
        self.assertEqual(entry["source_counts"], {"bup_dcu_lfd": 250})
        self.assertEqual(entry["stop_reason"], "manual")

        final_path = recordings / entry["path"]
        self.assertTrue(final_path.is_file())
        self.assertFalse(final_path.with_suffix(".pkl.part").exists())
        self.assertEqual(sum(1 for _ in iter_raw_records(final_path)), 250)
        catalog = json.loads((recordings / "index.json").read_text("utf-8"))
        self.assertEqual(catalog["recordings"][0]["id"], entry["id"])

    async def test_second_recording_is_rejected(self):
        await self.manager.start_recording()
        with self.assertRaisesRegex(RuntimeError, "уже активна"):
            await self.manager.start_recording()

    async def test_unsupported_duration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Разрешено"):
            await self.manager.start_recording(duration_seconds=120)

    async def test_backend_timer_stops_recording(self):
        await self.manager.start_recording(duration_seconds=60)
        session = self.manager._active
        session.timer_task.cancel()
        session.timer_task = asyncio.create_task(
            self.manager._timer_stop(session.recording_id, 0.01)
        )
        await asyncio.wait_for(session.timer_task, timeout=1)

        entry = self.manager.list_recordings()[0]
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["stop_reason"], "timer")

    async def test_status_callback_sees_recording_finalizing_and_idle(self):
        states = []

        async def callback(status):
            states.append(status["state"])

        self.manager.set_status_callback(callback)
        await self.manager.start_recording()
        await self.manager.stop_recording()
        self.assertEqual(states, ["recording", "finalizing", "idle"])


class RecoveryTests(unittest.TestCase):
    def test_stale_active_catalog_entry_is_marked_interrupted(self):
        with tempfile.TemporaryDirectory() as temporary:
            recordings = Path(temporary) / "recordings"
            month = recordings / "2026-08"
            month.mkdir(parents=True)
            partial = month / "rec_test.pkl.part"
            partial.write_bytes(b"partial")
            catalog = {
                "schema_version": 1,
                "updated_at": "2026-08-15T00:00:00Z",
                "recordings": [
                    {
                        "id": "abc",
                        "status": "recording",
                        "path": "2026-08/rec_test.pkl",
                        "partial_path": "2026-08/rec_test.pkl.part",
                        "started_at": "2026-08-15T00:00:00Z",
                    }
                ],
            }
            (recordings / "index.json").write_text(
                json.dumps(catalog), encoding="utf-8"
            )

            manager = RecordingManager(temporary, min_free_bytes=0)
            entry = manager.list_recordings()[0]
            self.assertEqual(entry["status"], "interrupted")
            self.assertEqual(entry["stop_reason"], "backend_restart")
            self.assertTrue(entry["incomplete"])


if __name__ == "__main__":
    unittest.main()
