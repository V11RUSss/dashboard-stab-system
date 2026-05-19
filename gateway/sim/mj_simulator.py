#!/usr/bin/env python3
"""
mj_simulator.py — Симулятор MicroJet BRO30

Генерирует реалистичные MJ-пакеты (dcu_telemetry + bup_drv_state)
и отправляет их по UDP на шлюз (sim-режим).

MJ-пакет = MJ-заголовок (16 байт) + данные структуры.
Заголовок упакован так же, как mj_msg_header_t из microjet_def.h.
"""

import os
import math
import struct
import socket
import time
import random
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("sim")

GATEWAY_ADDR = os.environ.get("GATEWAY_ADDR", "gateway")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", 9999))
SEND_HZ      = float(os.environ.get("SIM_HZ", "4"))       # отправок/сек на устройство

# ---------- Device IDs (из mjsysspec.h) ----------------------
DEV_DCU      = 0xB8FE
DEV_MON      = 0x6C99
DEV_BUP_LFD  = 0xFA82
DEV_BUP_RFD  = 0xD334
DEV_BUP_LRD  = 0x57F6
DEV_BUP_RRD  = 0x7E40
DEV_BUP_LST  = 0x7659
DEV_BUP_RST  = 0x5FEF

# ---------- Message IDs (из mjsysspec.h) ---------------------
ID_DCU_TEL   = 0xD63C
ID_BUP_STATE = 0x0824

# ---------- Enum значения ------------------------------------
DCU_STATUS_ARM   = 5
DCU_STATUS_DISARM = 4
DRV_CTRL_MODE_ANGLE = 4
DRV_CTRL_ERROR_NONE = 1
LOAD_ANG_SENS_STATUS_NORMAL = 3
ROTOR_ANG_SENS_STATUS_NORMAL = 3
ROTOR_BRAKE_STATE_RELEASE = 2
RELAY_SIGNAL_STATE_HIGH = 5
RELAY_SIGNAL_STATE_LOW  = 1
MOVE_LIMIT_STATE_NONE   = 1

BUP_DEVICES = [DEV_BUP_LFD, DEV_BUP_RFD, DEV_BUP_LRD,
               DEV_BUP_RRD, DEV_BUP_LST, DEV_BUP_RST]


def make_mj_header(src: int, dst: int, msg_id: int, cnt: int,
                   ts_s: int, ts_ns: int) -> bytes:
    """
    mj_msg_header_t (16 bytes):
      uint16 src, uint16 dst, uint16 id, uint16 cnt,
      uint32 ts_s, uint32 ts_low (ts_ns:30 | ts_status:2)
    """
    ts_low = ts_ns & 0x3FFFFFFF  # lower 30 bits = ns, upper 2 = status=0
    return struct.pack("<HHHHIi", src, dst, msg_id, cnt & 0xFFFF, ts_s, ts_low)


def make_pid_state(err: float, out: float) -> bytes:
    """pid_state_t: err cor_err feed_forward track back int_val out (7×float)"""
    cor_err = err * 0.95
    ff = out * 0.1
    return struct.pack("<7f", err, cor_err, ff, 0.0, 0.0, out * 0.5, out)


def make_three_phase(a: float, b: float, c: float) -> bytes:
    return struct.pack("<3f", a, b, c)


def make_park_vec(d: float, q: float) -> bytes:
    return struct.pack("<2f", d, q)


def make_dcu_telemetry(t: float, dev_idx: int = 0) -> bytes:
    """
    dcu_telemetry_t — 235 bytes of data (without MJ header).

    Layout confirmed from mjtypes.c type_check offsets:
      offset 0:  int8  mode
      offset 1:  int8  status
      offset 2:  int8  err
      offset 3:  int8  load_sens_state
      offset 4:  float load_sens_angle      (4B aligned)
      offset 8:  float load_sens_speed
      offset 12: float power_vdc
      offset 16: float current.a
      offset 20: float current.b
      offset 24: float current.c
      offset 28: float pwm.a
      offset 32: float pwm.b
      offset 36: float pwm.c
      offset 40: float park_voltage.d
      offset 44: float park_voltage.q
      offset 48: float park_current.d
      offset 52: float park_current.q
      offset 56: float ibrk
      offset 60: float stator_temp
      offset 64: int8  brake_state
      [3 padding bytes]
      offset 68: float power_voltage
      offset 72: float electrical_power
      offset 76: pid_state ang_pid      (7×float = 28B)
      offset 104: pid_state rotor_spd   (28B)
      offset 132: pid_state load_spd    (28B)
      offset 160: pid_state curr_q      (28B)
      offset 188: pid_state curr_d      (28B)
      offset 216: int8  rotor_sens_state
      [3 padding bytes]
      offset 220: float rotor_sens_angle
      offset 224: float rotor_sens_speed
      offset 228: int8  on
      offset 229: int8  manual
      offset 230: int8  block
      offset 231: int8  sync
      offset 232: int8  inv_octw
      offset 233: int8  inv_fault
      offset 234: int8  en_gate

    Total = 235 bytes
    """
    phase = t + dev_idx * (math.pi / 3)
    angle = math.sin(phase * 0.5) * math.pi * 0.8
    speed = math.cos(phase * 0.5) * 2.0
    cur_a = math.sin(phase) * 15.0
    cur_b = math.sin(phase + 2.094) * 15.0
    cur_c = math.sin(phase + 4.189) * 15.0
    pwm_a = 0.5 + math.sin(phase) * 0.3
    pwm_b = 0.5 + math.sin(phase + 2.094) * 0.3
    pwm_c = 0.5 + math.sin(phase + 4.189) * 0.3
    temp  = 40.0 + math.sin(t * 0.1 + dev_idx) * 5.0
    vdc   = 48.0 + random.uniform(-0.5, 0.5)

    buf = bytearray()
    # offsets 0-3: 4 × int8
    buf += struct.pack("4b",
                       DRV_CTRL_MODE_ANGLE,    # mode
                       DCU_STATUS_ARM,          # status
                       DRV_CTRL_ERROR_NONE,     # err
                       LOAD_ANG_SENS_STATUS_NORMAL)  # load_sens_state
    # offsets 4-55: 9 floats (load_angle, load_speed, vdc, cur×3, pwm×3)
    buf += struct.pack("<9f",
                       angle,          # load_sens_angle   offset 4
                       speed,          # load_sens_speed   offset 8
                       vdc,            # power_vdc         offset 12
                       cur_a, cur_b, cur_c,  # current     offset 16-24
                       pwm_a, pwm_b, pwm_c,  # pwm         offset 28-36
                       )
    buf += make_park_vec(cur_a * 0.1, cur_b * 0.1)  # park_voltage d/q  offset40
    buf += make_park_vec(cur_a * 0.9, cur_b * 0.9)  # park_current d/q  offset48
    buf += struct.pack("<2f", 0.5, temp)              # ibrk, stator_temp  56,60
    # offset 64: brake_state (int8) + 3 padding + power_voltage(float) + el_power(float)
    buf += struct.pack("<b3xff",
                       ROTOR_BRAKE_STATE_RELEASE,  # brake_state
                       48.2,                        # power_voltage
                       cur_a * 48.0)                # electrical_power
    # offsets 76-215: 5 × pid_state (5 × 28 = 140 bytes)
    ang_err = angle - math.sin(phase * 0.5 + 0.1) * math.pi * 0.8
    buf += make_pid_state(ang_err, speed * 2.0)        # ang_pid
    buf += make_pid_state(speed * 0.1, cur_a * 0.5)   # rotor_spd_pid
    buf += make_pid_state(speed * 0.05, cur_b * 0.5)  # load_spd_pid
    buf += make_pid_state(cur_a - 14.0, pwm_a * 48.0) # curr_q_pid
    buf += make_pid_state(cur_b - 0.5,  pwm_b * 48.0) # curr_d_pid
    # offset 216: rotor_sens_state (int8) + 3 padding + angle + speed
    buf += struct.pack("<b3xff",
                       ROTOR_ANG_SENS_STATUS_NORMAL,
                       angle * 1.01,   # rotor_sens_angle (slight diff from load)
                       speed * 1.01)   # rotor_sens_speed
    # offsets 228-234: 7 × int8
    buf += struct.pack("7b",
                       RELAY_SIGNAL_STATE_HIGH,  # on
                       RELAY_SIGNAL_STATE_LOW,   # manual
                       RELAY_SIGNAL_STATE_LOW,   # block
                       RELAY_SIGNAL_STATE_HIGH,  # sync
                       RELAY_SIGNAL_STATE_HIGH,  # inv_octw (active low = fault off)
                       RELAY_SIGNAL_STATE_HIGH,  # inv_fault (active low = fault off)
                       RELAY_SIGNAL_STATE_HIGH,  # en_gate
                       )
    assert len(buf) == 235, f"dcu_telemetry size mismatch: {len(buf)}"
    return bytes(buf)


def make_bup_state(t: float, dev_idx: int = 0) -> bytes:
    """
    bup_drv_state_t — 60 bytes (without MJ header).

    Layout from mjtypes.c:
      offset 0:  int8  mode
      offset 1:  int8  error
      offset 2:  int8  status
      offset 3:  int8  limit
      offset 4:  float angle
      offset 8:  float speed
      offset 12: float stator_temp
      offset 16: int8  brake_state
      [3 padding]
      offset 20: float power_voltage
      offset 24: float id
      offset 28: float iq
      offset 32: float electrical_power
      offset 36: int8  load_sens_state
      [3 padding]
      offset 40: float load_sens_angle
      offset 44: float load_sens_speed
      offset 48: int8  rotor_sens_state
      [3 padding]
      offset 52: float rotor_sens_angle
      offset 56: float rotor_sens_speed
    Total = 60 bytes
    """
    phase = t + dev_idx * (math.pi / 3)
    angle = math.sin(phase * 0.5) * math.pi * 0.8
    speed = math.cos(phase * 0.5) * 2.0
    temp  = 38.0 + math.sin(t * 0.1 + dev_idx) * 4.0
    id_curr = math.sin(phase) * 2.0
    iq_curr = math.cos(phase) * 15.0
    el_power = id_curr * 48.0

    buf = bytearray()
    # offsets 0-3
    buf += struct.pack("4b",
                       DRV_CTRL_MODE_ANGLE,
                       DRV_CTRL_ERROR_NONE,
                       DCU_STATUS_ARM,
                       MOVE_LIMIT_STATE_NONE)
    # offset 4-12
    buf += struct.pack("<3f", angle, speed, temp)
    # offset 16: brake (int8) + 3 pad + power_voltage + id + iq + el_power
    buf += struct.pack("<b3x4f",
                       ROTOR_BRAKE_STATE_RELEASE,
                       48.1, id_curr, iq_curr, el_power)
    # offset 36: load_sens_state (int8) + 3 pad + load_angle + load_speed
    buf += struct.pack("<b3x2f",
                       LOAD_ANG_SENS_STATUS_NORMAL,
                       angle * 1.005, speed * 1.005)
    # offset 48: rotor_sens_state (int8) + 3 pad + rotor_angle + rotor_speed
    buf += struct.pack("<b3x2f",
                       ROTOR_ANG_SENS_STATUS_NORMAL,
                       angle * 50.0,  # rotor angle +-512pi
                       speed * 50.0)
    assert len(buf) == 60, f"bup_state size mismatch: {len(buf)}"
    return bytes(buf)


def resolve_addr(host: str, port: int):
    """Retry DNS resolution (для Docker — ждём пока backend/gateway не поднимется)."""
    while True:
        try:
            info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
            return info[0][4]
        except socket.gaierror:
            log.warning("DNS не разрешился для %s, повтор через 3с...", host)
            time.sleep(3)


def main():
    log.info("MJ Simulator запущен. Цель: %s:%d, %.1f Гц",
             GATEWAY_ADDR, GATEWAY_PORT, SEND_HZ)
    target = resolve_addr(GATEWAY_ADDR, GATEWAY_PORT)
    log.info("Цель разрешена: %s", target)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cnt_dcu = 0
    cnt_bup = [0] * len(BUP_DEVICES)

    interval = 1.0 / SEND_HZ

    while True:
        t = time.time()
        ts_s  = int(t)
        ts_ns = int((t - ts_s) * 1e9) & 0x3FFFFFFF

        # ---------- dcu_telemetry от DCU → MON ----------
        hdr = make_mj_header(DEV_DCU, DEV_MON, ID_DCU_TEL, cnt_dcu, ts_s, ts_ns)
        data = make_dcu_telemetry(t, 0)
        pkt  = hdr + data
        sock.sendto(pkt, target)
        cnt_dcu = (cnt_dcu + 1) & 0xFFFF

        # ---------- bup_drv_state от каждого БУП ----------
        for i, dev_id in enumerate(BUP_DEVICES):
            hdr  = make_mj_header(dev_id, DEV_DCU, ID_BUP_STATE,
                                   cnt_bup[i], ts_s, ts_ns)
            data = make_bup_state(t, i)
            pkt  = hdr + data
            sock.sendto(pkt, target)
            cnt_bup[i] = (cnt_bup[i] + 1) & 0xFFFF

        time.sleep(interval)


if __name__ == "__main__":
    main()
