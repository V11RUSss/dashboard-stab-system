import math
import struct
import unittest

from core.mj_decoder import (
    TLV_BUP_STATE,
    TLV_CMD_ANS,
    TLV_CONTROL_ACK,
    TLV_DCU_TEL,
    TLV_FORMULAR,
    TLV_MSG_ANS,
    TLV_PRM_ANS,
    TLV_HEADER_FMT,
    decode,
    parse_tlv_header,
)


class DecoderTests(unittest.TestCase):
    def _header(self, message_type: int, length: int) -> dict:
        return {
            "msg_type": message_type,
            "src_id": 0xFA82,
            "dst_id": 0x6C99,
            "ts_s": 100,
            "ts_ns": 250,
            "length": length,
        }

    def test_parse_tlv_header(self):
        raw = struct.pack(TLV_HEADER_FMT, TLV_DCU_TEL, 0xFA82, 0x6C99, 1, 2, 236)
        parsed = parse_tlv_header(raw)
        self.assertEqual(parsed["msg_type"], TLV_DCU_TEL)
        self.assertEqual(parsed["src_id"], 0xFA82)
        self.assertEqual(parsed["length"], 236)

    def test_dcu_layout_and_raw_enums(self):
        payload = bytearray(236)
        struct.pack_into("<bbbb", payload, 0, 5, 5, 1, 3)
        struct.pack_into("<f", payload, 4, 1.25)
        struct.pack_into("<f", payload, 12, 48.5)
        struct.pack_into("<f", payload, 60, 73.25)
        decoded = decode(self._header(TLV_DCU_TEL, 236), bytes(payload))
        self.assertEqual(decoded["device_id"], "bup_dcu_lfd")
        self.assertEqual(decoded["msg_type"], "dcu_telemetry")
        self.assertEqual(decoded["mode"], "SPEED")
        self.assertEqual(decoded["mode_raw"], 5)
        self.assertEqual(decoded["status"], "ARM")
        self.assertEqual(decoded["load_sens_angle"], 1.25)
        self.assertEqual(decoded["power_vdc"], 48.5)
        self.assertEqual(decoded["stator_temp"], 73.25)

    def test_bup_layout(self):
        payload = bytearray(60)
        struct.pack_into("<bbbb", payload, 0, 4, 1, 5, 2)
        struct.pack_into("<f", payload, 4, -0.75)
        struct.pack_into("<f", payload, 8, 2.5)
        decoded = decode(self._header(TLV_BUP_STATE, 60), bytes(payload))
        self.assertEqual(decoded["msg_type"], "bup_state")
        self.assertEqual(decoded["angle"], -0.75)
        self.assertEqual(decoded["speed"], 2.5)
        self.assertEqual(decoded["limit_raw"], 2)

    def test_exact_payload_size_is_required(self):
        self.assertIsNone(decode(self._header(TLV_DCU_TEL, 235), bytes(235)))
        self.assertIsNone(decode(self._header(TLV_DCU_TEL, 237), bytes(237)))

    def test_non_finite_float_becomes_null_compatible_none(self):
        payload = bytearray(236)
        struct.pack_into("<f", payload, 4, math.nan)
        decoded = decode(self._header(TLV_DCU_TEL, 236), bytes(payload))
        self.assertIsNone(decoded["load_sens_angle"])

    def test_reverse_channel_responses(self):
        command = decode(
            self._header(TLV_CMD_ANS, 44),
            struct.pack("<iiI32s", 7, 2, 19, b"done"),
        )
        self.assertEqual(command["command"], "INTEGRATOR_RESET")
        self.assertEqual(command["state"], "DONE")
        self.assertEqual(command["request_counter"], 19)

        message = decode(
            self._header(TLV_MSG_ANS, 12),
            struct.pack("<H2xIi", 0x10AD, 20, 0),
        )
        self.assertEqual(message["state"], "CONFIRMED")

        parameter = decode(
            self._header(TLV_PRM_ANS, 40),
            struct.pack("<Ii32s", 0x311FD2BA, 0, b"\x01"),
        )
        self.assertEqual(parameter["status"], "OK")
        self.assertTrue(parameter["value_hex"].startswith("01"))

        formular = decode(
            self._header(TLV_FORMULAR, 264),
            struct.pack(
                "<32s32s32s32s32s32si64sB3x",
                "Привод".encode("cp866"), b"42", b"2026", b"1.0", b"BRO30",
                b"guid", 123, b"checksum", 1,
            ),
        )
        self.assertEqual(formular["product_description"], "Привод")
        self.assertEqual(formular["operation_time"], 123)

        ack = decode(
            self._header(TLV_CONTROL_ACK, 8),
            struct.pack("<IBbH", 99, 1, 0, 21),
        )
        self.assertEqual(ack["gateway_request_id"], 99)
        self.assertEqual(ack["status"], "SENT")


if __name__ == "__main__":
    unittest.main()
