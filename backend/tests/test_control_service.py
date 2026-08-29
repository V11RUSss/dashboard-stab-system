import struct
import unittest

from core.control_service import (
    CONTROL_HEADER_FMT,
    CONTROL_MAGIC,
    CONTROL_VERSION,
    DEVICE_IDS,
    OP_COMMAND,
    OP_PARAMETER,
    OP_SERVICE_START,
    OP_SERVICE_STOP,
    ControlService,
    build_control_frame,
)


class ControlFrameTests(unittest.TestCase):
    def test_frame_is_little_endian_and_has_one_concrete_target(self):
        body = struct.pack("<i", 7)
        raw = build_control_frame(OP_COMMAND, DEVICE_IDS["bup_dcu_lfd"], 15, body)
        header_size = struct.calcsize(CONTROL_HEADER_FMT)
        magic, version, opcode, target, request_id, length = struct.unpack(
            CONTROL_HEADER_FMT, raw[:header_size]
        )
        self.assertEqual(magic, CONTROL_MAGIC)
        self.assertEqual(version, CONTROL_VERSION)
        self.assertEqual(opcode, OP_COMMAND)
        self.assertEqual(target, 0xFA82)
        self.assertNotEqual(target, 0xFFFF)
        self.assertEqual(request_id, 15)
        self.assertEqual(length, len(body))
        self.assertEqual(raw[header_size:], body)

    def test_broadcast_destination_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "шести МУП"):
            build_control_frame(OP_COMMAND, 0xFFFF, 1, struct.pack("<i", 7))


class ControlServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.frames = []
        self.events = []
        self.service = ControlService({})

        async def sender(frame):
            self.frames.append(frame)

        async def broadcaster(kind, payload):
            self.events.append((kind, payload))

        self.service.set_sender(sender)
        self.service.set_broadcaster(broadcaster)
        await self.service.set_gateway_connected(True)

    async def asyncTearDown(self):
        await self.service.close()

    def _split(self, frame):
        header_size = struct.calcsize(CONTROL_HEADER_FMT)
        return struct.unpack(CONTROL_HEADER_FMT, frame[:header_size]), frame[header_size:]

    async def test_catalog_comes_from_generated_spec(self):
        self.assertEqual(len(self.service.parameters), 71)
        service_mode = self.service.parameters_by_name["service_mode"]
        self.assertEqual(service_mode["id"], 0x311FD2BA)
        self.assertEqual(service_mode["type"], "bool")
        self.assertTrue(self.service.parameters_by_name["dev_id"]["system"])

    async def test_parameter_values_are_typed_binary_not_text(self):
        await self.service.handle_action(
            "write_parameter",
            {"device_id": "bup_dcu_lfd", "parameter": "curr_kp", "value": "1.25"},
            object(),
            "browser-1",
        )
        header, body = self._split(self.frames[-1])
        self.assertEqual(header[2], OP_PARAMETER)
        request_type, parameter_id, value = struct.unpack("<iI32s", body)
        self.assertEqual(request_type, 1)
        self.assertEqual(parameter_id, self.service.parameters_by_name["curr_kp"]["id"])
        self.assertAlmostEqual(struct.unpack_from("<f", value)[0], 1.25)
        self.assertNotEqual(value[:4], b"1.25")

    async def test_service_owner_disconnect_sends_stop(self):
        owner = object()
        await self.service.handle_action(
            "service_start",
            {
                "device_id": "bup_dcu_rst",
                "mode": 1,
                "angle": 0,
                "speed": 0,
                "voltage": 0,
                "current": 0,
            },
            owner,
            "browser-2",
        )
        start_header, _ = self._split(self.frames[-1])
        self.assertEqual(start_header[2], OP_SERVICE_START)
        await self.service.release_owner(owner)
        stop_header, stop_body = self._split(self.frames[-1])
        self.assertEqual(stop_header[2], OP_SERVICE_STOP)
        self.assertEqual(stop_body, b"")
        self.assertFalse(self.service.snapshot()["service"]["active"])

    async def test_parameter_answer_is_decoded_using_catalog_type(self):
        parameter = self.service.parameters_by_name["pole_pairs"]
        await self.service.handle_gateway_message(
            {
                "msg_type": "parameter_answer",
                "device_id": "bup_dcu_lfd",
                "parameter_id": parameter["id"],
                "status": "OK",
                "status_raw": 0,
                "value_hex": (struct.pack("<i", 14) + bytes(28)).hex(),
                "timestamp": "2026-08-15T12:00:00+00:00",
            }
        )
        stored = self.service.snapshot()["parameters"]["bup_dcu_lfd"]["pole_pairs"]
        self.assertEqual(stored["value"], 14)
        response = [payload for kind, payload in self.events if kind == "control_response"][-1]
        self.assertEqual(response["value"], 14)

    async def test_gateway_ack_keeps_monitor_as_source_and_adds_target(self):
        result = await self.service.handle_action(
            "read_parameter",
            {"device_id": "bup_dcu_lfd", "parameter": "service_mode"},
            object(),
            "browser-3",
        )
        await self.service.handle_gateway_message(
            {
                "msg_type": "control_ack",
                "device_id": "mon",
                "gateway_request_id": result["gateway_request_id"],
                "opcode": "parameter_request",
                "opcode_raw": 3,
                "status": "SENT",
                "status_raw": 0,
                "microjet_counter": 12,
                "dst_id": DEVICE_IDS["bup_dcu_lfd"],
            }
        )
        response = [payload for kind, payload in self.events if kind == "control_response"][-1]
        self.assertEqual(response["device_id"], "mon")
        self.assertEqual(response["target_device_id"], "bup_dcu_lfd")
        self.assertEqual(response["opcode"], "parameter_request")


if __name__ == "__main__":
    unittest.main()
