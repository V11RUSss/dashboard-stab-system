import argparse
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from core.mj_decoder import TLV_DCU_TEL, TLV_HEADER_FMT
from core.recording_manager import RecordingManager
from tools.recordings import (
    RecordingToolError,
    find_entry,
    load_catalog,
    recording_path,
    selected_records,
    write_records,
)


def frame() -> tuple[bytes, dict]:
    payload = bytearray(236)
    struct.pack_into("<bbbb", payload, 0, 5, 5, 1, 3)
    struct.pack_into("<f", payload, 4, 1.25)
    values = (TLV_DCU_TEL, 0xFA82, 0x6C99, 100, 250, len(payload))
    header = struct.pack(TLV_HEADER_FMT, *values)
    return header + payload, {
        "msg_type": TLV_DCU_TEL,
        "src_id": 0xFA82,
        "dst_id": 0x6C99,
        "ts_s": 100,
        "ts_ns": 250,
        "length": len(payload),
    }


class RecordingToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = RecordingManager(self.temporary.name, min_free_bytes=0)
        await self.manager.start_recording(label="export test")
        raw, tlv = frame()
        self.manager.observe_and_capture(raw, tlv)
        await self.manager.stop_recording()
        self.root = Path(self.temporary.name) / "recordings"

    async def asyncTearDown(self):
        await self.manager.close()
        self.temporary.cleanup()

    async def test_catalog_selector_and_json_export(self):
        catalog = load_catalog(self.root)
        entry = find_entry(catalog, catalog["recordings"][0]["id"][:8])
        path = recording_path(self.root, entry)
        args = argparse.Namespace(
            from_time=None,
            to_time=None,
            include_invalid=False,
            message_type=None,
            device=None,
            limit=1,
        )
        output = io.StringIO()
        write_records(selected_records(path, args), output, "json")
        exported = json.loads(output.getvalue())

        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["decoded"]["device_id"], "bup_dcu_lfd")
        self.assertEqual(exported[0]["decoded"]["load_sens_angle"], 1.25)

    async def test_unknown_selector_is_rejected(self):
        catalog = load_catalog(self.root)
        with self.assertRaises(RecordingToolError):
            find_entry(catalog, "does-not-exist")


if __name__ == "__main__":
    unittest.main()
