#pragma once
#include <string>
#include <cstdint>
#include <cstddef>
#include <mutex>
#include <atomic>
#include <condition_variable>
#include <chrono>
#include <functional>
#include <thread>
#include <vector>

#include "control_protocol.hpp"

// ============================================================
//  TCP Forwarder — держит соединение к backend с авто-реконнектом.
//  Thread-safe: forward() вызывают callback-потоки microjetlinux.
// ============================================================

class TcpForwarder {
public:
    TcpForwarder(const std::string& host, int port);
    ~TcpForwarder();

    // Запустить фоновый поток реконнекта
    void start();
    void stop();

    // Отправить данные. Если нет соединения — пакет дропается (с логом).
    // Thread-safe.
    bool forward(const uint8_t* data, size_t len);

    // Reverse direction: validated control frames received from Backend.
    void set_control_handler(std::function<void(ControlFrame)> handler);
    void set_connection_handler(std::function<void(bool)> handler);

    bool connected() const { return connected_.load(); }

private:
    std::string  host_;
    int          port_;
    int          sock_fd_{-1};
    std::mutex   sock_mutex_;
    std::atomic<bool> running_{false};
    std::atomic<bool> connected_{false};
    std::atomic<uint64_t> sent_packets_{0};
    std::atomic<uint64_t> dropped_packets_{0};
    std::atomic<uint64_t> send_errors_{0};
    std::thread reconnect_thread_;
    std::mutex wait_mutex_;
    std::condition_variable wait_cv_;
    std::mutex callback_mutex_;
    std::function<void(ControlFrame)> control_handler_;
    std::function<void(bool)> connection_handler_;
    std::vector<uint8_t> receive_buffer_;

    void connect_loop();
    bool try_connect();
    bool receive_commands();
    bool parse_receive_buffer();
    void notify_connection(bool connected);
    void disconnect();
    bool wait_or_stop(std::chrono::milliseconds duration);
};
