import math
import unittest

from core.telemetry_aggregator import TelemetryAggregator


def message(**values):
    return {
        "device_id": "bup_dcu_lfd",
        "msg_type": "dcu_telemetry",
        "shortname": "МУП.ТЛП",
        "description": "Левый передний",
        "raw_id": 0xFA82,
        "dst_id": 0xFFFF,
        "ts_s": 100,
        "ts_ns": 0,
        "status_raw": 5,
        "mode_raw": 5,
        "error_raw": 1,
        **values,
    }


class TelemetryAggregatorTests(unittest.TestCase):
    def test_scalar_window_contains_mean_min_max_last_and_count(self):
        aggregator = TelemetryAggregator()
        aggregator.add(message(ts_ns=0, power_vdc=40.0))
        aggregator.add(message(ts_ns=25_000_000, power_vdc=50.0))
        aggregator.add(message(ts_ns=50_000_000, power_vdc=60.0))

        series = aggregator.take_telemetry_series()

        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["sample_count"], 3)
        self.assertEqual(series[0]["timestamp"], 100.05)
        self.assertEqual(
            series[0]["values"]["power_vdc"],
            {"mean": 50.0, "min": 40.0, "max": 60.0, "last": 60.0, "count": 3},
        )
        self.assertEqual(aggregator.take_telemetry_series(), [])

    def test_angle_mean_handles_minus_pi_plus_pi_boundary(self):
        aggregator = TelemetryAggregator()
        aggregator.add(message(load_sens_angle=math.pi - 0.05))
        aggregator.add(message(load_sens_angle=-math.pi + 0.05))

        stats = aggregator.take_telemetry_series()[0]["values"]["load_sens_angle"]

        self.assertAlmostEqual(abs(stats["mean"]), math.pi, places=6)
        self.assertIsNone(stats["min"])
        self.assertIsNone(stats["max"])
        self.assertFalse(stats["out_of_range"])

    def test_out_of_range_test_angle_remains_visible_without_wrapping(self):
        aggregator = TelemetryAggregator()
        aggregator.add(message(load_sens_angle=123.0))

        stats = aggregator.take_telemetry_series()[0]["values"]["load_sens_angle"]

        self.assertEqual(stats["mean"], 123.0)
        self.assertEqual(stats["min"], 123.0)
        self.assertEqual(stats["max"], 123.0)
        self.assertEqual(stats["last"], 123.0)
        self.assertTrue(stats["out_of_range"])

    def test_bup_multi_turn_rotor_angle_is_not_wrapped(self):
        aggregator = TelemetryAggregator()
        bup_message = message(
            msg_type="bup_state",
            rotor_sens_angle=5.0 * math.pi,
        )
        aggregator.add(bup_message)

        stats = aggregator.take_telemetry_series()[0]["values"]["rotor_sens_angle"]

        self.assertAlmostEqual(stats["mean"], 5.0 * math.pi)
        self.assertNotIn("out_of_range", stats)

    def test_full_rate_window_collapses_to_one_ui_point(self):
        aggregator = TelemetryAggregator()
        for index in range(4_000):
            aggregator.add(
                message(ts_ns=index * 250_000, power_vdc=float(index % 100))
            )

        series = aggregator.take_telemetry_series()

        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["sample_count"], 4_000)
        self.assertEqual(series[0]["values"]["power_vdc"]["count"], 4_000)

    def test_card_updates_are_rate_limited_but_state_changes_are_immediate(self):
        aggregator = TelemetryAggregator(card_interval_s=0.5)
        aggregator.add(message(power_vdc=40.0))
        first = aggregator.take_device_updates(now=1.0)
        self.assertEqual(len(first), 1)

        aggregator.add(message(power_vdc=41.0))
        self.assertEqual(aggregator.take_device_updates(now=1.1), [])
        periodic = aggregator.take_device_updates(now=1.5)
        self.assertEqual(periodic[0]["power_vdc"], 41.0)

        aggregator.add(message(status_raw=-1, power_vdc=42.0))
        urgent = aggregator.take_device_updates(now=1.6)
        self.assertEqual(urgent[0]["status_raw"], -1)


if __name__ == "__main__":
    unittest.main()
