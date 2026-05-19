#pragma once
#include <string>
#include <cstdint>
#include <cstddef>
#include <mutex>
#include <atomic>
#include <functional>

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
    void forward(const uint8_t* data, size_t len);

private:
    std::string  host_;
    int          port_;
    int          sock_fd_{-1};
    std::mutex   sock_mutex_;
    std::atomic<bool> running_{false};
    std::atomic<bool> connected_{false};

    void connect_loop();
    bool try_connect();
    void disconnect();
};
