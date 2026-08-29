"""Low-rate statistical view of the full-rate decoded telemetry stream."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from core.state import utc_now_iso

GRAPHABLE_FIELDS = (
    "angle",
    "speed",
    "load_sens_angle",
    "load_sens_speed",
    "rotor_sens_angle",
    "rotor_sens_speed",
    "power_vdc",
    "power_voltage",
    "electrical_power",
    "stator_temp",
    "ibrk",
    "current_a",
    "current_b",
    "current_c",
    "id",
    "iq",
)

# Only single-turn angles whose protocol range is [-pi, pi] need circular
# averaging.  bup_state.rotor_sens_angle is a multi-turn value (up to
# +/-512*pi), so treating it as circular would lose the revolution count.
CIRCULAR_ANGLE_FIELDS = {
    "dcu_telemetry": {"load_sens_angle", "rotor_sens_angle"},
    "bup_state": {"angle", "load_sens_angle"},
}

STATE_SIGNATURE_FIELDS = (
    "status_raw",
    "mode_raw",
    "error_raw",
    "brake_state_raw",
    "limit_raw",
    "load_sens_state_raw",
    "rotor_sens_state_raw",
    "on_raw",
    "manual_raw",
    "block_raw",
    "sync_raw",
    "inv_octw_raw",
    "inv_fault_raw",
    "en_gate_raw",
)


@dataclass
class _ScalarStats:
    count: int = 0
    total: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf
    last: float = 0.0

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.last = value

    def result(self) -> dict:
        return {
            "mean": self.total / self.count,
            "min": self.minimum,
            "max": self.maximum,
            "last": self.last,
            "count": self.count,
        }


@dataclass
class _AngleStats:
    """Circular mean for angles represented in radians around ``[-pi, pi]``."""

    count: int = 0
    total: float = 0.0
    sin_sum: float = 0.0
    cos_sum: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf
    last: float = 0.0
    out_of_range: bool = False

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.sin_sum += math.sin(value)
        self.cos_sum += math.cos(value)
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.last = value
        self.out_of_range |= not -math.pi <= value <= math.pi

    def result(self) -> dict:
        # A test source may intentionally transmit arbitrary values.  Such a
        # value violates the single-turn angle contract, but silently wrapping
        # it would make the chart disagree with the decoded/raw value.  Keep it
        # visible as a linear value and expose the validation flag to the UI.
        if self.out_of_range:
            return {
                "mean": self.total / self.count,
                "min": self.minimum,
                "max": self.maximum,
                "last": self.last,
                "count": self.count,
                "out_of_range": True,
            }

        magnitude = math.hypot(self.sin_sum, self.cos_sum)
        mean = (
            math.atan2(self.sin_sum, self.cos_sum)
            if magnitude > 1e-12
            else self.last
        )
        # A large raw span normally means that the samples crossed -pi/+pi.
        # In that case a linear min/max envelope would cover almost the entire
        # chart and be misleading, so only the circular mean is published.
        wrapped = self.maximum - self.minimum > math.pi
        return {
            "mean": mean,
            "min": None if wrapped else self.minimum,
            "max": None if wrapped else self.maximum,
            "last": self.last,
            "count": self.count,
            "out_of_range": False,
        }


class _MessageBucket:
    def __init__(self, decoded: dict) -> None:
        self.device_id = decoded["device_id"]
        self.message_type = decoded["msg_type"]
        self.shortname = decoded.get("shortname")
        self.description = decoded.get("description")
        self.raw_id = decoded.get("raw_id")
        self.dst_id = decoded.get("dst_id")
        self.sample_count = 0
        self.last_ts_s = 0
        self.last_ts_ns = 0
        self.values: dict[str, _ScalarStats | _AngleStats] = {}
        self.add(decoded)

    def add(self, decoded: dict) -> None:
        self.sample_count += 1
        self.last_ts_s = int(decoded.get("ts_s", 0))
        self.last_ts_ns = int(decoded.get("ts_ns", 0))
        for field in GRAPHABLE_FIELDS:
            value = decoded.get(field)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                continue
            accumulator = self.values.get(field)
            if accumulator is None:
                circular_fields = CIRCULAR_ANGLE_FIELDS.get(
                    self.message_type, set()
                )
                accumulator = (
                    _AngleStats() if field in circular_fields else _ScalarStats()
                )
                self.values[field] = accumulator
            accumulator.add(float(value))

    def result(self) -> dict:
        return {
            "device_id": self.device_id,
            "msg_type": self.message_type,
            "shortname": self.shortname,
            "description": self.description,
            "raw_id": self.raw_id,
            "dst_id": self.dst_id,
            "ts_s": self.last_ts_s,
            "ts_ns": self.last_ts_ns,
            "timestamp": self.last_ts_s + self.last_ts_ns * 1e-9,
            "sample_count": self.sample_count,
            "values": {
                field: accumulator.result()
                for field, accumulator in self.values.items()
            },
        }


class TelemetryAggregator:
    """Aggregate numeric telemetry and rate-limit full device-card updates."""

    def __init__(self, card_interval_s: float = 0.5) -> None:
        self.card_interval_s = card_interval_s
        self._buckets: dict[tuple[str, str], _MessageBucket] = {}
        self._latest_messages: dict[tuple[str, str], dict] = {}
        self._latest_generation: dict[tuple[str, str], int] = {}
        self._sent_generation: dict[tuple[str, str], int] = {}
        self._last_card_sent: dict[tuple[str, str], float] = {}
        self._last_state_signature: dict[tuple[str, str], tuple] = {}

    @staticmethod
    def _key(decoded: dict) -> tuple[str, str]:
        return decoded["device_id"], decoded["msg_type"]

    @staticmethod
    def _state_signature(decoded: dict) -> tuple:
        return tuple(decoded.get(field) for field in STATE_SIGNATURE_FIELDS)

    def add(self, decoded: dict) -> None:
        key = self._key(decoded)
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = _MessageBucket(decoded)
        else:
            bucket.add(decoded)

        self._latest_messages[key] = decoded
        self._latest_generation[key] = self._latest_generation.get(key, 0) + 1

    def take_telemetry_series(self) -> list[dict]:
        buckets, self._buckets = self._buckets, {}
        return [buckets[key].result() for key in sorted(buckets)]

    def take_device_updates(self, now: float | None = None) -> list[dict]:
        now = time.monotonic() if now is None else now
        updates = []
        received_at = None
        for key in sorted(self._latest_messages):
            generation = self._latest_generation[key]
            if generation == self._sent_generation.get(key, 0):
                continue

            latest = self._latest_messages[key]
            signature = self._state_signature(latest)
            state_changed = signature != self._last_state_signature.get(key)
            interval_elapsed = (
                now - self._last_card_sent.get(key, -math.inf)
                >= self.card_interval_s
            )
            if not state_changed and not interval_elapsed:
                continue

            if received_at is None:
                received_at = utc_now_iso()
            published = latest.copy()
            published["received_at"] = received_at
            updates.append(published)
            self._sent_generation[key] = generation
            self._last_card_sent[key] = now
            self._last_state_signature[key] = signature
        return updates
