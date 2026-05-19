#include "real_receiver.hpp"
#include "packet_handlers.hpp"
#include "protocol.hpp"
#include <cstdio>
#include <cstring>
#include <csignal>
#include <cstdlib>
#include <unistd.h>
#include <arpa/inet.h>

// microjetlinux headers
extern "C" {
#include "../microjetlinux/microjet_driver.h"
#include "../microjetlib/inc/mjdriver_interface.h"
#include "../microjetlib/inc/mjsysspec.h"
#include "../microjetlib/inc/mjtypes.h"

// rx/tx globals defined in microjet_driver.c — expose for socket validity check
extern rx_conf rx;
extern tx_conf tx;
}


// ─── Static forwarder pointer (доступен глобально внутри этого файла) ───────
TcpForwarder* RealReceiver::s_fwd_ = nullptr;

// ─── Signal handler ──────────────────────────────────────────────────────────
static volatile int g_real_running = 1;
static void real_signal_handler(int) { g_real_running = 0; }

// ─── MicroJet callback: dcu_telemetry ────────────────────────────────────────
// Сигнатура: mj_msg_proc_fcn = mj_msg_proc_res_t(*)(uint8_t*, uint16_t, uint32_t, uint32_t)
static mj_msg_proc_res_t cb_dcu_tel(uint8_t* payload, uint16_t size,
                                     uint32_t ts_s, uint32_t ts_ns)
{
    if (!payload || size < (uint16_t)sizeof(mj_header_t))
        return MJ_REJECT_SIZE;

    mj_header_t hdr;
    memcpy(&hdr, payload, sizeof(hdr));

    const uint8_t* data      = payload + sizeof(hdr);
    const uint16_t data_size = size    - (uint16_t)sizeof(hdr);

    if (RealReceiver::s_fwd_)
        handle_dcu_telemetry(*RealReceiver::s_fwd_,
                              hdr.src, hdr.dst,
                              hdr.ts_s, hdr.ts_low & 0x3FFFFFFFu,
                              data, data_size);
    free(payload);
    return MJ_ACCEPT;
}

// ─── MicroJet callback: bup_drv_state ────────────────────────────────────────
static mj_msg_proc_res_t cb_bup_state(uint8_t* payload, uint16_t size,
                                       uint32_t ts_s, uint32_t ts_ns)
{
    if (!payload || size < (uint16_t)sizeof(mj_header_t))
        return MJ_REJECT_SIZE;

    mj_header_t hdr;
    memcpy(&hdr, payload, sizeof(hdr));

    const uint8_t* data      = payload + sizeof(hdr);
    const uint16_t data_size = size    - (uint16_t)sizeof(hdr);

    if (RealReceiver::s_fwd_)
        handle_bup_state(*RealReceiver::s_fwd_,
                          hdr.src, hdr.dst,
                          hdr.ts_s, hdr.ts_low & 0x3FFFFFFFu,
                          data, data_size);
    free(payload);
    return MJ_ACCEPT;
}

// ─── RealReceiver implementation ─────────────────────────────────────────────
RealReceiver::RealReceiver(const std::string& iface_name, TcpForwarder& fwd)
    : iface_(iface_name), fwd_(fwd)
{
    s_fwd_ = &fwd;
}

void RealReceiver::run()
{
    signal(SIGINT,  real_signal_handler);
    signal(SIGTERM, real_signal_handler);

    fprintf(stderr, "[REAL] Starting on interface: %s\n", iface_.c_str());

    // Установить имя интерфейса (используется внутри mj_drvInit)
    setEthernetInterfaceName(const_cast<char*>(iface_.c_str()));

    // init_mjdrv заполняет vtable своими реализациями
    mj_drv_interface_t idrv{};
    init_mjdrv(&idrv);

    // Проверить что RX-сокет открылся (>0)
    // Если нет — интерфейс не найден, не стартуем поток
    if (rx.socket <= 0) {
        fprintf(stderr, "[REAL] FATAL: RX socket invalid (socket=%d).\n"
                        "[REAL]   Check that interface '%s' exists: ip link show\n"
                        "[REAL]   Set correct interface: export IFACE=<name>\n",
                        rx.socket, iface_.c_str());
        // Ждём вместо busy-loop
        while (g_real_running) sleep(5);
        return;
    }

    // Активировать RX-поток (TX не нужен — только принимаем)
    setRxStatus(true);
    setTxStatus(false);

    // Зарегистрировать callback для dcu_telemetry
    // Multicast BRO_BUP_SRV_MCAST = 239.200.100.1 → port 49001
    mj_status_t st1 = idrv.udp4.register_callback(
        BRO_BUP_SRV_MCAST_IP, htons(49001), cb_dcu_tel);
    fprintf(stderr, "[REAL] register dcu_tel callback: %s\n",
            st1 == MJ_OK ? "OK" : "FAIL");

    // Зарегистрировать callback для bup_drv_state
    // Multicast BRO_BUP_DATA_MCAST = 239.200.10.1 → port 49002
    mj_status_t st2 = idrv.udp4.register_callback(
        BRO_BUP_DATA_MCAST_IP, htons(49002), cb_bup_state);
    fprintf(stderr, "[REAL] register bup_state callback: %s\n",
            st2 == MJ_OK ? "OK" : "FAIL");

    // Создать Ethernet-интерфейс и вступить в multicast-группы
    void* eth_if = idrv.udp4.create_interface(nullptr, 0);
    if (eth_if) {
        idrv.udp4.join_group(eth_if, BRO_BUP_SRV_MCAST_IP);
        idrv.udp4.join_group(eth_if, BRO_BUP_DATA_MCAST_IP);
        fprintf(stderr, "[REAL] Joined multicast groups.\n");
    } else {
        fprintf(stderr, "[REAL] WARNING: create_interface returned NULL "
                        "(no MAC/IP needed for RX-only)\n");
    }

    fprintf(stderr, "[REAL] Listening for MicroJet packets...\n");

    // RX-поток запущен внутри microjetlinux — просто ждём
    while (g_real_running) {
        sleep(1);
    }

    deinit_mjdrv();
    fprintf(stderr, "[REAL] Receiver stopped.\n");
}
