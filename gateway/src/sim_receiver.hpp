#pragma once
#include "tcp_forwarder.hpp"
#include <string>

// ============================================================
//  SIM receiver: обычный UDP-сокет, ждёт пакеты от mj_simulator.py
//  Каждый пакет — полный MJ-payload (заголовок 16B + данные).
//  Без Ethernet/IP обёртки — напрямую MJ-структура.
// ============================================================

class SimReceiver {
public:
    SimReceiver(const std::string& listen_addr, int listen_port,
                TcpForwarder& fwd);
    // Blocking event loop (runs until SIGINT)
    void run();

private:
    std::string   addr_;
    int           port_;
    TcpForwarder& fwd_;
};
