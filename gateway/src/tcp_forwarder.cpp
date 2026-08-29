#include "tcp_forwarder.hpp"
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <thread>
#include <chrono>
#include <utility>

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <netdb.h>
#include <netinet/tcp.h>
#include <poll.h>

TcpForwarder::TcpForwarder(const std::string& host, int port)
    : host_(host), port_(port) {}

TcpForwarder::~TcpForwarder() { stop(); }

void TcpForwarder::start() {
    bool expected = false;
    if (!running_.compare_exchange_strong(expected, true)) {
        return;
    }
    reconnect_thread_ = std::thread([this]{ connect_loop(); });
}

void TcpForwarder::stop() {
    if (!running_.exchange(false)) {
        return;
    }
    wait_cv_.notify_all();
    disconnect();
    if (reconnect_thread_.joinable()) {
        reconnect_thread_.join();
    }
}

void TcpForwarder::connect_loop() {
    while (running_) {
        if (!connected_) {
            if (try_connect()) {
                if (!running_) {
                    disconnect();
                    break;
                }
                fprintf(stderr, "[TCP] Connected to %s:%d\n", host_.c_str(), port_);
                connected_ = true;
                notify_connection(true);
            } else {
                fprintf(stderr, "[TCP] Connect failed, retry in 3s...\n");
                wait_or_stop(std::chrono::seconds(3));
            }
        } else {
            if (!receive_commands()) {
                fprintf(stderr, "[TCP] Backend connection closed — reconnecting\n");
                disconnect();
            } else {
                wait_or_stop(std::chrono::milliseconds(50));
            }
        }
    }
}

bool TcpForwarder::wait_or_stop(std::chrono::milliseconds duration) {
    std::unique_lock<std::mutex> lock(wait_mutex_);
    return wait_cv_.wait_for(lock, duration, [this]{ return !running_.load(); });
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
    receive_buffer_.clear();
    return true;
}

bool TcpForwarder::receive_commands() {
    int descriptor_fd = -1;
    {
        std::lock_guard<std::mutex> lock(sock_mutex_);
        if (sock_fd_ < 0 || !connected_) {
            return false;
        }
        descriptor_fd = sock_fd_;
    }

    struct pollfd descriptor{};
    descriptor.fd = descriptor_fd;
    descriptor.events = POLLIN;
    const int result = poll(&descriptor, 1, 0);
    if (result < 0) {
        return errno == EINTR;
    }
    if (result == 0) {
        return true;
    }
    if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
        return false;
    }

    if (descriptor.revents & POLLIN) {
        uint8_t buffer[1024];
        ssize_t received = -1;
        {
            std::lock_guard<std::mutex> lock(sock_mutex_);
            if (sock_fd_ != descriptor_fd) {
                return false;
            }
            received = recv(sock_fd_, buffer, sizeof(buffer), MSG_DONTWAIT);
        }
        if (received == 0) {
            return false;
        }
        if (received < 0) {
            return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR;
        }
        receive_buffer_.insert(
            receive_buffer_.end(), buffer, buffer + static_cast<size_t>(received)
        );
        return parse_receive_buffer();
    }
    return true;
}

bool TcpForwarder::parse_receive_buffer() {
    while (receive_buffer_.size() >= sizeof(control_header_t)) {
        control_header_t header{};
        memcpy(&header, receive_buffer_.data(), sizeof(header));
        if (header.magic != CONTROL_MAGIC ||
            header.version != CONTROL_VERSION ||
            header.length > CONTROL_MAX_BODY) {
            fprintf(stderr, "[TCP] Invalid control frame header\n");
            return false;
        }

        const size_t frame_size = sizeof(header) + header.length;
        if (receive_buffer_.size() < frame_size) {
            return true;
        }

        ControlFrame frame{
            static_cast<ControlOpcode>(header.opcode),
            header.dst_id,
            header.request_id,
            std::vector<uint8_t>(
                receive_buffer_.begin() + sizeof(header),
                receive_buffer_.begin() + frame_size
            ),
        };
        receive_buffer_.erase(
            receive_buffer_.begin(), receive_buffer_.begin() + frame_size
        );

        std::function<void(ControlFrame)> handler;
        {
            std::lock_guard<std::mutex> lock(callback_mutex_);
            handler = control_handler_;
        }
        if (handler) {
            handler(std::move(frame));
        }
    }
    return true;
}

void TcpForwarder::set_control_handler(
    std::function<void(ControlFrame)> handler
) {
    std::lock_guard<std::mutex> lock(callback_mutex_);
    control_handler_ = std::move(handler);
}

void TcpForwarder::set_connection_handler(
    std::function<void(bool)> handler
) {
    std::lock_guard<std::mutex> lock(callback_mutex_);
    connection_handler_ = std::move(handler);
}

void TcpForwarder::notify_connection(bool connected) {
    std::function<void(bool)> handler;
    {
        std::lock_guard<std::mutex> lock(callback_mutex_);
        handler = connection_handler_;
    }
    if (handler) {
        handler(connected);
    }
}

void TcpForwarder::disconnect() {
    const bool was_connected = connected_.exchange(false);
    {
        std::lock_guard<std::mutex> lock(sock_mutex_);
        if (sock_fd_ >= 0) {
            close(sock_fd_);
            sock_fd_ = -1;
        }
    }
    if (was_connected) {
        notify_connection(false);
    }
}

bool TcpForwarder::forward(const uint8_t* data, size_t len) {
    std::unique_lock<std::mutex> lock(sock_mutex_);
    if (sock_fd_ < 0 || !connected_) {
        ++dropped_packets_;
        return false;
    }

    size_t offset = 0;
    while (offset < len) {
        const ssize_t sent = send(sock_fd_, data + offset, len - offset, MSG_NOSIGNAL);
        if (sent > 0) {
            offset += static_cast<size_t>(sent);
            continue;
        }
        if (sent < 0 && errno == EINTR) {
            continue;
        }

        const int saved_errno = sent < 0 ? errno : ECONNRESET;
        ++send_errors_;
        ++dropped_packets_;
        fprintf(stderr, "[TCP] Send error: %s — disconnecting\n",
                strerror(saved_errno));
        close(sock_fd_);
        sock_fd_ = -1;
        const bool was_connected = connected_.exchange(false);
        lock.unlock();
        if (was_connected) {
            notify_connection(false);
        }
        return false;
    }
    ++sent_packets_;
    return true;
}
