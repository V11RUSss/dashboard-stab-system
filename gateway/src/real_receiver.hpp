#pragma once
#include "control_protocol.hpp"
#include "tcp_forwarder.hpp"
#include <atomic>
#include <chrono>
#include <deque>
#include <mutex>
#include <string>
#include <thread>

struct mj_mon;

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
    ~RealReceiver();

    // Blocking event loop
    void run();

    // Generated MicroJet IRQ hook; public because the generated handle stores
    // a plain C function pointer.
    static void forward_generated_message(void* data);

private:
    std::string   iface_;
    TcpForwarder& fwd_;
    mj_mon*       mon_{nullptr};
    std::atomic<bool> protocol_running_{false};
    std::thread protocol_thread_;
    std::mutex command_mutex_;
    std::deque<ControlFrame> command_queue_;

    bool service_active_{false};
    bool service_stopping_{false};
    uint16_t service_target_{0};
    uint32_t service_request_id_{0};
    std::chrono::steady_clock::time_point service_deadline_{};
    std::chrono::steady_clock::time_point service_disable_at_{};

    void enqueue_control(ControlFrame frame);
    void on_backend_connection(bool connected);
    void protocol_loop();
    void process_control(ControlFrame frame);
    void send_ack(uint32_t request_id, ControlOpcode opcode, uint16_t target,
                  int8_t status, uint16_t counter = 0);
    void begin_safe_stop(uint32_t request_id, int8_t status);
};
