import json
import tempfile
import unittest

from core.recording_manager import RecordingManager

try:
    from core.ws_server import WebSocketServer
except ModuleNotFoundError:
    WebSocketServer = None


class FakeEngine:
    async def get_list(self):
        return []


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


class FakeControlService:
    def catalog(self):
        return {"commands": [{"value": 7, "name": "INTEGRATOR_RESET"}]}

    def snapshot(self):
        return {"gateway_connected": True, "service": {"active": False}, "events": []}


@unittest.skipIf(WebSocketServer is None, "websockets dependency is not installed")
class WebSocketRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = RecordingManager(self.temporary.name, min_free_bytes=0)
        self.server = WebSocketServer(
            "127.0.0.1",
            8765,
            {"gateway_status": {}},
            FakeEngine(),
            "backend.log",
            self.manager,
        )
        self.websocket = FakeWebSocket()

    async def asyncTearDown(self):
        await self.manager.close()
        self.temporary.cleanup()

    async def test_initial_state_contains_recording_status(self):
        await self.server._send_initial_state(self.websocket)
        message_types = [item["type"] for item in self.websocket.messages]
        self.assertIn("recording_status", message_types)

    async def test_start_stop_and_list_actions(self):
        await self.server._handle_request(
            self.websocket,
            {
                "action": "start_recording",
                "payload": {"duration_seconds": 300, "label": "ws test"},
                "request_id": "one",
            },
        )
        self.assertEqual(self.manager.get_status()["state"], "recording")
        await self.server._handle_request(
            self.websocket,
            {
                "action": "stop_recording",
                "payload": {},
                "request_id": "two",
            },
        )
        await self.server._handle_request(
            self.websocket,
            {
                "action": "list_recordings",
                "payload": {},
                "request_id": "three",
            },
        )

        by_request = {
            item.get("request_id"): item for item in self.websocket.messages
        }
        self.assertEqual(by_request["one"]["type"], "request_ok")
        self.assertEqual(by_request["two"]["type"], "request_ok")
        self.assertEqual(by_request["three"]["type"], "recordings_list")
        entry = by_request["three"]["payload"]["recordings"][0]
        self.assertEqual(entry["label"], "ws test")
        self.assertEqual(entry["status"], "completed")

    async def test_control_context_is_returned_as_one_atomic_event(self):
        server = WebSocketServer(
            "127.0.0.1",
            8765,
            {"gateway_status": {}},
            FakeEngine(),
            "backend.log",
            self.manager,
            FakeControlService(),
        )
        websocket = FakeWebSocket()
        await server._handle_request(
            websocket,
            {
                "action": "get_control_context",
                "payload": {},
                "request_id": "context-one",
            },
        )

        self.assertEqual(len(websocket.messages), 1)
        event = websocket.messages[0]
        self.assertEqual(event["type"], "control_context")
        self.assertEqual(event["request_id"], "context-one")
        self.assertTrue(event["payload"]["snapshot"]["gateway_connected"])
        self.assertEqual(event["payload"]["catalog"]["commands"][0]["value"], 7)

        websocket.messages.clear()
        await server._handle_request(
            websocket,
            {
                "action": "get_control_snapshot",
                "payload": {},
                "request_id": "snapshot-one",
            },
        )
        self.assertEqual(len(websocket.messages), 1)
        self.assertEqual(websocket.messages[0]["type"], "control_snapshot")
        self.assertEqual(websocket.messages[0]["request_id"], "snapshot-one")


if __name__ == "__main__":
    unittest.main()
