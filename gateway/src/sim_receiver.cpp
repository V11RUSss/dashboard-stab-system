#include "sim_receiver.hpp"
#include "packet_handlers.hpp"
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <csignal>
#include <chrono>

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

static volatile int g_sim_running = 1;
static void sim_signal_handler(int) { g_sim_running = 0; }

SimReceiver::SimReceiver(const std::string& listen_addr, int listen_port,
                         TcpForwarder& fwd)
    : addr_(listen_addr), port_(listen_port), fwd_(fwd) {}

void SimReceiver::run() {
    signal(SIGINT,  sim_signal_handler);
    signal(SIGTERM, sim_signal_handler);

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        perror("[SIM] socket");
        return;
    }

    int reuse = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    // Timeout so we can check g_sim_running periodically
    struct timeval tv{ 1, 0 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_in sa{};
    sa.sin_family      = AF_INET;
    sa.sin_port        = htons((uint16_t)port_);
    sa.sin_addr.s_addr = inet_addr(addr_.c_str());

    if (bind(sock, (struct sockaddr*)&sa, sizeof(sa)) < 0) {
        perror("[SIM] bind");
        close(sock);
        return;
    }

    fprintf(stderr, "[SIM] Listening on %s:%d (sim mode)\n", addr_.c_str(), port_);

    static const size_t BUF = 4096;
    uint8_t buf[BUF];
    auto next_stats = std::chrono::steady_clock::now() + std::chrono::seconds(5);

    while (g_sim_running) {
        ssize_t n = recvfrom(sock, buf, BUF, 0, nullptr, nullptr);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) continue; // timeout
            perror("[SIM] recvfrom");
            break;
        }
        if (n == 0) continue;

        // Each UDP datagram is a raw MJ packet (header + payload, no ethernet)
        dispatch_mj_packet(fwd_, buf, (size_t)n);
        const auto now = std::chrono::steady_clock::now();
        if (now >= next_stats) {
            log_packet_stats();
            next_stats = now + std::chrono::seconds(5);
        }
    }

    close(sock);
    fprintf(stderr, "[SIM] Receiver stopped.\n");
}
