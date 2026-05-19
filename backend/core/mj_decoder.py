"""
mj_decoder.py — ctypes-декодер бинарных MJ-структур BRO30.

Принимает сырой payload из TLV-пакета и возвращает Python dict.
Структуры точно воспроизводят layout из mjtypes.c (с padding).
"""

import ctypes
import struct
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── TLV типы (должны совпадать с gateway/src/config.hpp) ─────
TLV_DCU_TEL   = 0x01
TLV_BUP_STATE = 0x02

TLV_HEADER_FMT  = "<BHHII H"  # type src_id dst_id ts_s ts_ns length
TLV_HEADER_SIZE = struct.calcsize(TLV_HEADER_FMT)  # = 15

# ── Карта ID устройств ────────────────────────────────────────
DEVICE_NAMES = {
    0xB8FE: ("dcu",         "МУП",      "Модуль управления приводом"),
    0xFA82: ("bup_dcu_lfd", "МУП.ТЛП",  "Лев. передний тяговый привод"),
    0xD334: ("bup_dcu_rfd", "МУП.ТПП",  "Прав. передний тяговый привод"),
    0x57F6: ("bup_dcu_lrd", "МУП.ТЛЗ",  "Лев. задний тяговый привод"),
    0x7E40: ("bup_dcu_rrd", "МУП.ТПЗ",  "Прав. задний тяговый привод"),
    0x7659: ("bup_dcu_lst", "МУП.РЛ",   "Лев. рулевой привод"),
    0x5FEF: ("bup_dcu_rst", "МУП.РП",   "Прав. рулевой привод"),
    0xF5B6: ("aufd",        "АУФД",     "Автомат управления функциями движения"),
    0x6C99: ("mon",         "СПО",      "Сервисное программное обеспечение"),
    0x1D4C: ("im_bro",      "И.ПЫЛ",    "Имитатор пылесоса"),
}

# ── Перечисления ──────────────────────────────────────────────
DCU_STATUS = {
    0: "UNKNOWN", 1: "OFF", 2: "PREPARE", 3: "CUR_CALIB",
    4: "DISARM",  5: "ARM", -1: "FAULT",
}
DRV_CTRL_MODE = {
    0: "UNKNOWN", 1: "OFF",     2: "FREEMOVE", 3: "HOLD",
    4: "ANGLE",   5: "SPEED",   6: "RSPEED",   7: "VOLTAGE",
    8: "SYNCHRO", 9: "CURRENT",
}
DRV_CTRL_ERROR = {
    0: "UNKNOWN", 1: "NONE",  2: "BLOCK", 3: "OFF",   4: "POWER",
    5: "CTRL",    6: "OLP",   7: "OCP",   8: "UVP",   9: "SCP",
    10: "MODE",   11: "INV",  12: "LANG", 13: "RANG", 14: "OHP",
    15: "BRAKE",  16: "ROTOR_SENS", 17: "MCON", 18: "SERVICE",
}
BRAKE_STATE  = {0: "UNKNOWN", 1: "CLOSE", 2: "RELEASE", 3: "FAIL"}
SENS_STATUS  = {0: "UNKNOW", 1: "PREPARE", 2: "UNCONNECTED", 3: "NORMAL", 4: "WARNING", -1: "FAULT"}
RELAY_STATE  = {0: "UNKNOWN", 5: "HIGH", 1: "LOW", 3: "GAP", -1: "OCP", -2: "FAIL"}
MOVE_LIMIT   = {0: "UNKNOW", 1: "NONE", 2: "LIMIT_UP", 3: "LIMIT_DOWN"}


# ── ctypes structures ─────────────────────────────────────────

class _PidState(ctypes.LittleEndianStructure):
    _pack_  = 1
    _fields_ = [
        ("err",          ctypes.c_float),
        ("cor_err",      ctypes.c_float),
        ("feed_forward", ctypes.c_float),
        ("track",        ctypes.c_float),
        ("back",         ctypes.c_float),
        ("int_val",      ctypes.c_float),
        ("out",          ctypes.c_float),
    ]

    def to_dict(self):
        return {
            "err":   round(self.err, 6),
            "out":   round(self.out, 6),
        }


class _DcuTelemetry(ctypes.LittleEndianStructure):
    """
    Точный layout из mjtypes.c dcu_telemetry_type_check.
    Padding добавлен явными полями.
    """
    _pack_  = 1
    _fields_ = [
        ("mode",               ctypes.c_int8),
        ("status",             ctypes.c_int8),
        ("err",                ctypes.c_int8),
        ("load_sens_state",    ctypes.c_int8),
        ("load_sens_angle",    ctypes.c_float),   # offset 4
        ("load_sens_speed",    ctypes.c_float),   # offset 8
        ("power_vdc",          ctypes.c_float),   # offset 12
        ("current_a",          ctypes.c_float),   # offset 16
        ("current_b",          ctypes.c_float),   # offset 20
        ("current_c",          ctypes.c_float),   # offset 24
        ("pwm_a",              ctypes.c_float),   # offset 28
        ("pwm_b",              ctypes.c_float),   # offset 32
        ("pwm_c",              ctypes.c_float),   # offset 36
        ("park_voltage_d",     ctypes.c_float),   # offset 40
        ("park_voltage_q",     ctypes.c_float),   # offset 44
        ("park_current_d",     ctypes.c_float),   # offset 48
        ("park_current_q",     ctypes.c_float),   # offset 52
        ("ibrk",               ctypes.c_float),   # offset 56
        ("stator_temp",        ctypes.c_float),   # offset 60
        ("brake_state",        ctypes.c_int8),    # offset 64
        ("_pad0",              ctypes.c_uint8 * 3),
        ("power_voltage",      ctypes.c_float),   # offset 68
        ("electrical_power",   ctypes.c_float),   # offset 72
        ("ang_pid",            _PidState),        # offset 76
        ("rotor_spd_pid",      _PidState),        # offset 104
        ("load_spd_pid",       _PidState),        # offset 132
        ("curr_q_pid",         _PidState),        # offset 160
        ("curr_d_pid",         _PidState),        # offset 188
        ("rotor_sens_state",   ctypes.c_int8),    # offset 216
        ("_pad1",              ctypes.c_uint8 * 3),
        ("rotor_sens_angle",   ctypes.c_float),   # offset 220
        ("rotor_sens_speed",   ctypes.c_float),   # offset 224
        ("on",                 ctypes.c_int8),    # offset 228
        ("manual",             ctypes.c_int8),    # offset 229
        ("block",              ctypes.c_int8),    # offset 230
        ("sync",               ctypes.c_int8),    # offset 231
        ("inv_octw",           ctypes.c_int8),    # offset 232
        ("inv_fault",          ctypes.c_int8),    # offset 233
        ("en_gate",            ctypes.c_int8),    # offset 234
    ]

assert ctypes.sizeof(_DcuTelemetry) == 235, \
    f"_DcuTelemetry size = {ctypes.sizeof(_DcuTelemetry)}, expected 235"


class _BupDrvState(ctypes.LittleEndianStructure):
    """
    Layout из mjtypes.c bup_drv_state_type_check.
    """
    _pack_  = 1
    _fields_ = [
        ("mode",             ctypes.c_int8),    # offset 0
        ("error",            ctypes.c_int8),    # offset 1
        ("status",           ctypes.c_int8),    # offset 2
        ("limit",            ctypes.c_int8),    # offset 3
        ("angle",            ctypes.c_float),   # offset 4
        ("speed",            ctypes.c_float),   # offset 8
        ("stator_temp",      ctypes.c_float),   # offset 12
        ("brake_state",      ctypes.c_int8),    # offset 16
        ("_pad0",            ctypes.c_uint8 * 3),
        ("power_voltage",    ctypes.c_float),   # offset 20
        ("id",               ctypes.c_float),   # offset 24
        ("iq",               ctypes.c_float),   # offset 28
        ("electrical_power", ctypes.c_float),   # offset 32
        ("load_sens_state",  ctypes.c_int8),    # offset 36
        ("_pad1",            ctypes.c_uint8 * 3),
        ("load_sens_angle",  ctypes.c_float),   # offset 40
        ("load_sens_speed",  ctypes.c_float),   # offset 44
        ("rotor_sens_state", ctypes.c_int8),    # offset 48
        ("_pad2",            ctypes.c_uint8 * 3),
        ("rotor_sens_angle", ctypes.c_float),   # offset 52
        ("rotor_sens_speed", ctypes.c_float),   # offset 56
    ]

assert ctypes.sizeof(_BupDrvState) == 60, \
    f"_BupDrvState size = {ctypes.sizeof(_BupDrvState)}, expected 60"


# ── Public API ───────────────────────────────────────────────

def parse_tlv_header(data: bytes) -> dict | None:
    """Распарсить TLV-заголовок (15 байт). Вернуть dict или None."""
    if len(data) < TLV_HEADER_SIZE:
        return None
    try:
        msg_type, src_id, dst_id, ts_s, ts_ns, length = struct.unpack_from(
            TLV_HEADER_FMT, data, 0
        )
        return {
            "msg_type": msg_type,
            "src_id":   src_id,
            "dst_id":   dst_id,
            "ts_s":     ts_s,
            "ts_ns":    ts_ns,
            "length":   length,
        }
    except struct.error as e:
        logger.error("TLV header parse error: %s", e)
        return None


def _device_info(dev_id: int) -> dict:
    key, short, desc = DEVICE_NAMES.get(dev_id, (f"dev_{dev_id:#06x}", "???", "Неизвестно"))
    return {"device_id": key, "shortname": short, "description": desc, "raw_id": dev_id}


def decode_dcu_telemetry(raw: bytes, src_id: int, dst_id: int,
                          ts_s: int, ts_ns: int) -> dict:
    if len(raw) < ctypes.sizeof(_DcuTelemetry):
        logger.warning("dcu_tel too short: %d", len(raw))
        return {}

    s = _DcuTelemetry.from_buffer_copy(raw[:ctypes.sizeof(_DcuTelemetry)])
    ts = datetime.fromtimestamp(ts_s + ts_ns * 1e-9, tz=timezone.utc).isoformat()

    d = {
        "msg_type":          "dcu_telemetry",
        "timestamp":         ts,
        "ts_s":              ts_s,
        "ts_ns":             ts_ns,
        **_device_info(src_id),
        "dst_id":            dst_id,

        # Состояние
        "mode":              DRV_CTRL_MODE.get(s.mode, s.mode),
        "status":            DCU_STATUS.get(s.status, s.status),
        "error":             DRV_CTRL_ERROR.get(s.err, s.err),
        "brake_state":       BRAKE_STATE.get(s.brake_state, s.brake_state),

        # Механические величины
        "load_sens_angle":   round(s.load_sens_angle, 5),
        "load_sens_speed":   round(s.load_sens_speed, 5),
        "rotor_sens_angle":  round(s.rotor_sens_angle, 5),
        "rotor_sens_speed":  round(s.rotor_sens_speed, 5),
        "load_sens_state":   SENS_STATUS.get(s.load_sens_state, s.load_sens_state),
        "rotor_sens_state":  SENS_STATUS.get(s.rotor_sens_state, s.rotor_sens_state),

        # Электрические величины
        "power_vdc":         round(s.power_vdc, 3),
        "power_voltage":     round(s.power_voltage, 3),
        "electrical_power":  round(s.electrical_power, 3),
        "ibrk":              round(s.ibrk, 4),
        "stator_temp":       round(s.stator_temp, 2),

        # Токи
        "current_a":         round(s.current_a, 4),
        "current_b":         round(s.current_b, 4),
        "current_c":         round(s.current_c, 4),
        "pwm_a":             round(s.pwm_a, 4),
        "pwm_b":             round(s.pwm_b, 4),
        "pwm_c":             round(s.pwm_c, 4),
        "park_voltage_d":    round(s.park_voltage_d, 4),
        "park_voltage_q":    round(s.park_voltage_q, 4),
        "park_current_d":    round(s.park_current_d, 4),
        "park_current_q":    round(s.park_current_q, 4),

        # Реле
        "on":        RELAY_STATE.get(s.on, s.on),
        "manual":    RELAY_STATE.get(s.manual, s.manual),
        "block":     RELAY_STATE.get(s.block, s.block),
        "sync":      RELAY_STATE.get(s.sync, s.sync),
        "inv_octw":  RELAY_STATE.get(s.inv_octw, s.inv_octw),
        "inv_fault": RELAY_STATE.get(s.inv_fault, s.inv_fault),
        "en_gate":   RELAY_STATE.get(s.en_gate, s.en_gate),

        # PID (компактно: только err и out)
        "ang_pid":        s.ang_pid.to_dict(),
        "rotor_spd_pid":  s.rotor_spd_pid.to_dict(),
        "load_spd_pid":   s.load_spd_pid.to_dict(),
        "curr_q_pid":     s.curr_q_pid.to_dict(),
        "curr_d_pid":     s.curr_d_pid.to_dict(),
    }
    return d


def decode_bup_state(raw: bytes, src_id: int, dst_id: int,
                      ts_s: int, ts_ns: int) -> dict:
    if len(raw) < ctypes.sizeof(_BupDrvState):
        logger.warning("bup_state too short: %d", len(raw))
        return {}

    s = _BupDrvState.from_buffer_copy(raw[:ctypes.sizeof(_BupDrvState)])
    ts = datetime.fromtimestamp(ts_s + ts_ns * 1e-9, tz=timezone.utc).isoformat()

    d = {
        "msg_type":          "bup_state",
        "timestamp":         ts,
        "ts_s":              ts_s,
        "ts_ns":             ts_ns,
        **_device_info(src_id),
        "dst_id":            dst_id,

        # Состояние
        "mode":              DRV_CTRL_MODE.get(s.mode, s.mode),
        "status":            DCU_STATUS.get(s.status, s.status),
        "error":             DRV_CTRL_ERROR.get(s.error, s.error),
        "limit":             MOVE_LIMIT.get(s.limit, s.limit),
        "brake_state":       BRAKE_STATE.get(s.brake_state, s.brake_state),
        "load_sens_state":   SENS_STATUS.get(s.load_sens_state, s.load_sens_state),
        "rotor_sens_state":  SENS_STATUS.get(s.rotor_sens_state, s.rotor_sens_state),

        # Механические
        "angle":             round(s.angle, 5),
        "speed":             round(s.speed, 5),
        "stator_temp":       round(s.stator_temp, 2),
        "load_sens_angle":   round(s.load_sens_angle, 5),
        "load_sens_speed":   round(s.load_sens_speed, 5),
        "rotor_sens_angle":  round(s.rotor_sens_angle, 5),
        "rotor_sens_speed":  round(s.rotor_sens_speed, 5),

        # Электрические
        "power_voltage":     round(s.power_voltage, 3),
        "id":                round(s.id, 4),
        "iq":                round(s.iq, 4),
        "electrical_power":  round(s.electrical_power, 3),
    }
    return d


def decode(tlv_header: dict, payload: bytes) -> dict | None:
    """Главная функция декодирования. Принимает распарсенный заголовок и payload."""
    t = tlv_header["msg_type"]
    src, dst = tlv_header["src_id"], tlv_header["dst_id"]
    ts_s, ts_ns = tlv_header["ts_s"], tlv_header["ts_ns"]

    if t == TLV_DCU_TEL:
        return decode_dcu_telemetry(payload, src, dst, ts_s, ts_ns)
    elif t == TLV_BUP_STATE:
        return decode_bup_state(payload, src, dst, ts_s, ts_ns)
    else:
        logger.debug("Unknown TLV type: 0x%02X", t)
        return None
