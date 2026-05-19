#include "tcp_forwarder.hpp"
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <thread>
#include <chrono>

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <netdb.h>
#include <netinet/tcp.h>

TcpForwarder::TcpForwarder(const std::string& host, int port)
    : host_(host), port_(port) {}

TcpForwarder::~TcpForwarder() { stop(); }

void TcpForwarder::start() {
    running_ = true;
    std::thread([this]{ connect_loop(); }).detach();
}

void TcpForwarder::stop() {
    running_ = false;
    disconnect();
}

void TcpForwarder::connect_loop() {
    while (running_) {
        if (!connected_) {
            if (try_connect()) {
                fprintf(stderr, "[TCP] Connected to %s:%d\n", host_.c_str(), port_);
                connected_ = true;
            } else {
                fprintf(stderr, "[TCP] Connect failed, retry in 3s...\n");
                std::this_thread::sleep_for(std::chrono::seconds(3));
            }
        } else {
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
        }
    }
}

bool TcpForwarder::try_connect() {
    // Resolve host (handles both IPs and hostnames like "backend")
    struct addrinfo hints{}, *res = nullptr;
    hints.ai_family   = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[8];
    snprintf(port_str, sizeof(port_str), "%d", port_);
    if (getaddrinfo(host_.c_str(), port_str, &hints, &res) != 0 || !res) {
        return false;
    }

    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { freeaddrinfo(res); return false; }

    // Set connect timeout (5s)
    struct timeval tv{ 5, 0 };
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    if (connect(fd, res->ai_addr, res->ai_addrlen) < 0) {
        close(fd);
        freeaddrinfo(res);
        return false;
    }
    freeaddrinfo(res);

    // Disable Nagle for lower latency
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

    std::lock_guard<std::mutex> lock(sock_mutex_);
    if (sock_fd_ >= 0) close(sock_fd_);
    sock_fd_ = fd;
    return true;
}

void TcpForwarder::disconnect() {
    std::lock_guard<std::mutex> lock(sock_mutex_);
    if (sock_fd_ >= 0) {
        close(sock_fd_);
        sock_fd_ = -1;
    }
    connected_ = false;
}

void TcpForwarder::forward(const uint8_t* data, size_t len) {
    std::lock_guard<std::mutex> lock(sock_mutex_);
    if (sock_fd_ < 0 || !connected_) {
        fprintf(stderr, "[TCP] Packet dropped (not connected)\n");
        return;
    }
    ssize_t sent = send(sock_fd_, data, len, MSG_NOSIGNAL);
    if (sent < 0 || (size_t)sent != len) {
        fprintf(stderr, "[TCP] Send error: %s — disconnecting\n", strerror(errno));
        close(sock_fd_);
        sock_fd_ = -1;
        connected_ = false;
    }
}
