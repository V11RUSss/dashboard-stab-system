#pragma once
#include <cstdint>
#include <string>
#include <cstdlib>

// ============================================================
//  Режим работы шлюза
//  Управляется переменной окружения GATEWAY_MODE:
//    "sim"  — приём от Python-симулятора (обычный UDP socket)
//    "real" — приём реальных Ethernet-фреймов через raw socket
//             (требует CAP_NET_RAW / --privileged)
// ============================================================

static inline std::string get_env(const char* name, const char* def) {
    const char* v = std::getenv(name);
    return v ? v : def;
}

static inline int get_env_int(const char* name, int def) {
    const char* v = std::getenv(name);
    return v ? std::atoi(v) : def;
}

// ---------- режим -------------------------------------------
// "sim" | "real"
static const std::string GATEWAY_MODE  = get_env("GATEWAY_MODE", "sim");

// ---------- backend -----------------------------------------
static const std::string BACKEND_ADDR  = get_env("BACKEND_ADDR", "backend");
static const int         BACKEND_PORT  = get_env_int("BACKEND_PORT", 8888);

// ---------- sim-режим (обычный UDP socket) ------------------
// Шлюз слушает входящие пакеты от mj_simulator.py
static const std::string SIM_LISTEN_ADDR = get_env("SIM_LISTEN_ADDR", "0.0.0.0");
static const int         SIM_LISTEN_PORT = get_env_int("SIM_LISTEN_PORT", 9999);

// ---------- real-режим (raw socket / microjetlinux) ---------
static const std::string IFACE_NAME    = get_env("IFACE_NAME", "eth0");

// ---------- TLV типы пакетов (gateway → backend) ------------
static const uint8_t TLV_DCU_TEL    = 0x01;  // dcu_telemetry
static const uint8_t TLV_BUP_STATE  = 0x02;  // bup_drv_state
