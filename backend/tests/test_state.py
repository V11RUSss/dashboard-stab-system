import unittest

from core.state import make_envelope, update_device_state


class StateTests(unittest.TestCase):
    def test_messages_from_one_device_do_not_overwrite_each_other(self):
        shared = {"devices_state": {}}
        base = {
            "device_id": "bup_dcu_lfd",
            "shortname": "МУП.ТЛП",
            "description": "Левый передний",
            "raw_id": 0xFA82,
        }
        update_device_state(
            shared, {**base, "msg_type": "dcu_telemetry", "power_vdc": 48.0},
            "2026-01-01T00:00:00+00:00",
        )
        update_device_state(
            shared, {**base, "msg_type": "bup_state", "angle": 1.0},
            "2026-01-01T00:00:01+00:00",
        )
        messages = shared["devices_state"]["bup_dcu_lfd"]["messages"]
        self.assertEqual(set(messages), {"dcu_telemetry", "bup_state"})
        self.assertEqual(messages["dcu_telemetry"]["power_vdc"], 48.0)

    def test_envelope_is_versioned(self):
        message = make_envelope("device_telemetry", {"value": 1}, "request-1")
        self.assertEqual(message["schema_version"], 1)
        self.assertEqual(message["request_id"], "request-1")


if __name__ == "__main__":
    unittest.main()
