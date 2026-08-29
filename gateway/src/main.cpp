#include "config.hpp"
#include "tcp_forwarder.hpp"
#include "sim_receiver.hpp"
#ifdef REAL_MODE_ENABLED
#include "real_receiver.hpp"
#endif
#include <cstdio>

int main() {
    fprintf(stderr, "==============================================\n");
    fprintf(stderr, "  MicroJet Gateway (C++)\n");
    fprintf(stderr, "  Mode     : %s\n", GATEWAY_MODE.c_str());
    fprintf(stderr, "  Backend  : %s:%d\n", BACKEND_ADDR.c_str(), BACKEND_PORT);
    if (GATEWAY_MODE == "sim") {
        fprintf(stderr, "  Listen   : %s:%d (UDP sim)\n",
                SIM_LISTEN_ADDR.c_str(), SIM_LISTEN_PORT);
    } else {
        fprintf(stderr, "  Interface: %s (raw socket)\n", IFACE_NAME.c_str());
    }
    fprintf(stderr, "==============================================\n");

    // Start TCP forwarder (background reconnect thread)
    TcpForwarder fwd(BACKEND_ADDR, BACKEND_PORT);
    fwd.start();

    if (GATEWAY_MODE == "real") {
        #ifdef REAL_MODE_ENABLED
        RealReceiver rx(IFACE_NAME, fwd);
        rx.run();
        // Stop the callback-producing TCP thread while rx is still alive.
        fwd.stop();
        #else
        fprintf(stderr, "ERROR: Gateway compiled without REAL_MODE support!\n");
        return 1;
        #endif
    } else {
        // Default: sim mode
        SimReceiver rx(SIM_LISTEN_ADDR, SIM_LISTEN_PORT, fwd);
        rx.run();
        fwd.stop();
    }

    fwd.stop();
    return 0;
}
