import asyncio
import struct
import tempfile
import unittest

from core.mj_decoder import TLV_DCU_TEL, TLV_HEADER_FMT
from core.recording_manager import RecordingManager
from core.tcp_server import TCPGatewayServer


class FakeWebSocketServer:
    def __init__(self):
        self.events = []

    async def broadcast_event(self, kind, payload, request_id=None):
        self.events.append((kind, payload))


class FakeWriter:
    def __init__(self):
        self.closed = False
        self.writes = []

    def get_extra_info(self, name):
        return ("test-gateway", 12345) if name == "peername" else None

    def close(self):
        self.closed = True

    def write(self, data):
        self.writes.append(bytes(data))

    async def drain(self):
        return None

    async def wait_closed(self):
        return None


class TCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.recording_manager = RecordingManager(
            self.temp_dir.name, min_free_bytes=0
        )
        self.ws = FakeWebSocketServer()
        self.shared = {
            "last_payload": {},
            "devices_state": {},
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
            },
        }
        self.server = TCPGatewayServer(
            "127.0.0.1", 8888, self.shared, self.recording_manager, self.ws
        )

    async def asyncTearDown(self):
        await self.recording_manager.close()
        self.temp_dir.cleanup()

    async def test_valid_frame_updates_state_and_emits_versioned_event_input(self):
        payload = bytearray(236)
        struct.pack_into("<bbbb", payload, 0, 5, 5, 1, 3)
        struct.pack_into("<f", payload, 4, 1.5)
        header = struct.pack(
            TLV_HEADER_FMT, TLV_DCU_TEL, 0xFA82, 0x6C99, 100, 250, len(payload)
        )

        reader = asyncio.StreamReader()
        reader.feed_data(header + payload)
        reader.feed_eof()
        writer = FakeWriter()
        await self.server._handle_stream(reader, writer)

        node = self.shared["devices_state"]["bup_dcu_lfd"]
        self.assertIn("dcu_telemetry", node["messages"])
        self.assertEqual(
            node["messages"]["dcu_telemetry"]["load_sens_angle"], 1.5
        )
        self.assertEqual(self.shared["gateway_status"]["packets_decoded"], 1)
        self.assertEqual(self.recording_manager.get_status()["state"], "idle")
        self.assertEqual(self.recording_manager.list_recordings(), [])
        self.assertTrue(
            any(kind == "telemetry_batch" for kind, _payload in self.ws.events)
        )
        self.assertTrue(
            any(kind == "devices_batch" for kind, _payload in self.ws.events)
        )
        self.assertTrue(writer.closed)

    async def test_active_recording_captures_the_tlv_frame(self):
        payload = bytearray(236)
        struct.pack_into("<bbbb", payload, 0, 5, 5, 1, 3)
        header = struct.pack(
            TLV_HEADER_FMT, TLV_DCU_TEL, 0xFA82, 0x6C99, 100, 250, len(payload)
        )
        await self.recording_manager.start_recording(label="tcp test")

        reader = asyncio.StreamReader()
        reader.feed_data(header + payload)
        reader.feed_eof()
        await self.server._handle_stream(reader, FakeWriter())
        await self.recording_manager.stop_recording()

        entry = self.recording_manager.list_recordings()[0]
        self.assertEqual(entry["packet_count"], 1)
        self.assertEqual(entry["message_counts"], {"dcu_telemetry": 1})
        self.assertEqual(entry["source_counts"], {"bup_dcu_lfd": 1})

    async def test_reverse_frame_uses_the_same_gateway_connection(self):
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        task = asyncio.create_task(self.server._handle_stream(reader, writer))
        await asyncio.sleep(0)
        await self.server.send_control_frame(b"MJC1-test")
        self.assertEqual(writer.writes, [b"MJC1-test"])
        self.assertEqual(
            self.shared["gateway_status"]["control_frames_sent"], 1
        )
        reader.feed_eof()
        await task


if __name__ == "__main__":
    unittest.main()
