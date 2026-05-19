#pragma once
#include "tcp_forwarder.hpp"
#include <string>

// ============================================================
//  REAL receiver: использует microjetlinux (raw Ethernet socket)
//  Требует:
//    - CAP_NET_RAW (или --privileged в Docker)
//    - Корректный сетевой интерфейс IFACE_NAME
// ============================================================

class RealReceiver {
public:
    // Указатель на forwarder — публичный, нужен static callback-функциям
    static TcpForwarder* s_fwd_;

    RealReceiver(const std::string& iface_name, TcpForwarder& fwd);

    // Blocking event loop
    void run();

private:
    std::string   iface_;
    TcpForwarder& fwd_;
};
