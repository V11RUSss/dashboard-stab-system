#include "real_receiver.hpp"

#include "packet_handlers.hpp"
#include "protocol.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <csignal>
#include <ctime>
#include <unistd.h>

extern "C" {
#include "../microjetlinux/microjet_driver.h"
#include "../microjetlib/inc/mjdriver_interface.h"
#include "../microjetlib/inc/mjsysspec.h"
#include "../microjetlib/inc/mjtypes.h"
#include "../microjetlib/mon/mj_mon_spec.h"

extern rx_conf rx;
extern tx_conf tx;
}

TcpForwarder* RealReceiver::s_fwd_ = nullptr;

namespace {

volatile sig_atomic_t g_real_running = 1;

void real_signal_handler(int) {
    g_real_running = 0;
}

bool is_bup_target(uint16_t value) {
    switch (value) {
        case BRO30_BUP_DCU_LFD:
        case BRO30_BUP_DCU_RFD:
        case BRO30_BUP_DCU_LRD:
        case BRO30_BUP_DCU_RRD:
        case BRO30_BUP_DCU_LST:
        case BRO30_BUP_DCU_RST:
            return true;
        default:
            return false;
    }
}

bool valid_service_mode(int32_t mode) {
    return mode >= DRV_CTRL_MODE_OFF && mode <= DRV_CTRL_MODE_VOLTAGE_C;
}

bool finite_service_values(const service_control_body_t& body) {
    return std::isfinite(body.angle) && std::isfinite(body.speed) &&
           std::isfinite(body.voltage) && std::isfinite(body.current);
}

template <typename T>
bool read_body(const ControlFrame& frame, T& result) {
    if (frame.body.size() != sizeof(T)) {
        return false;
    }
    memcpy(&result, frame.body.data(), sizeof(T));
    return true;
}

size_t generated_message_size(uint16_t id) {
    switch (id) {
        case ID_DCU_TEL: return sizeof(dcu_telemetry_msg_t);
        case ID_BUP_STATE: return sizeof(bup_drv_state_msg_t);
        case ID_CMD_ANS: return sizeof(command_answer_msg_t);
        case ID_MSG_ANS: return sizeof(msg_answer_msg_t);
        case ID_PRM_ANS: return sizeof(parameter_answer_msg_t);
        case ID_FORMULAR: return sizeof(formular_info_msg_t);
        default: return 0;
    }
}

void attach_device_irqs(mj_mon_t* mon) {
#define ATTACH_IRQS(device)                                                     \
    mon->in.device.dcu_tel.irq = RealReceiver::forward_generated_message;       \
    mon->in.device.bup_state.irq = RealReceiver::forward_generated_message;     \
    mon->in.device.cmd_ans.irq = RealReceiver::forward_generated_message;       \
    mon->in.device.msg_ans.irq = RealReceiver::forward_generated_message;       \
    mon->in.device.prm_ans.irq = RealReceiver::forward_generated_message;       \
    mon->in.device.formular.irq = RealReceiver::forward_generated_message

    ATTACH_IRQS(bup_dcu_lfd);
    ATTACH_IRQS(bup_dcu_rfd);
    ATTACH_IRQS(bup_dcu_lrd);
    ATTACH_IRQS(bup_dcu_rrd);
    ATTACH_IRQS(bup_dcu_lst);
    ATTACH_IRQS(bup_dcu_rst);
#undef ATTACH_IRQS
}

} // namespace

static_assert(sizeof(dcu_telemetry_msg_t) - sizeof(mj_header_t) ==
              MJ_DCU_TEL_PAYLOAD_SIZE, "generated dcu_tel layout changed");
static_assert(sizeof(bup_drv_state_msg_t) - sizeof(mj_header_t) ==
              MJ_BUP_STATE_PAYLOAD_SIZE, "generated bup_state layout changed");
static_assert(sizeof(command_answer_msg_t) - sizeof(mj_header_t) ==
              MJ_CMD_ANS_PAYLOAD_SIZE, "generated cmd_ans layout changed");
static_assert(sizeof(msg_answer_msg_t) - sizeof(mj_header_t) ==
              MJ_MSG_ANS_PAYLOAD_SIZE, "generated msg_ans layout changed");
static_assert(sizeof(parameter_answer_msg_t) - sizeof(mj_header_t) ==
              MJ_PRM_ANS_PAYLOAD_SIZE, "generated prm_ans layout changed");
static_assert(sizeof(formular_info_msg_t) - sizeof(mj_header_t) ==
              MJ_FORMULAR_PAYLOAD_SIZE, "generated formular layout changed");

RealReceiver::RealReceiver(const std::string& iface_name, TcpForwarder& fwd)
    : iface_(iface_name), fwd_(fwd) {
    s_fwd_ = &fwd_;
    fwd_.set_control_handler(
        [this](ControlFrame frame) { enqueue_control(std::move(frame)); }
    );
    fwd_.set_connection_handler(
        [this](bool connected) { on_backend_connection(connected); }
    );
}

RealReceiver::~RealReceiver() {
    fwd_.set_control_handler({});
    fwd_.set_connection_handler({});
    if (s_fwd_ == &fwd_) {
        s_fwd_ = nullptr;
    }
}

void RealReceiver::forward_generated_message(void* data) {
    if (!data || !s_fwd_) {
        return;
    }
    const auto* header = static_cast<const mj_header_t*>(data);
    const size_t size = generated_message_size(header->id);
    if (size != 0) {
        dispatch_mj_packet(*s_fwd_, static_cast<const uint8_t*>(data), size);
    }
}

void RealReceiver::enqueue_control(ControlFrame frame) {
    std::lock_guard<std::mutex> lock(command_mutex_);
    // STOP is a safety action. It must never wait behind a full queue of
    // ordinary commands and must never be dropped on disconnect/shutdown.
    if (frame.opcode == ControlOpcode::ServiceStop) {
        if (command_queue_.size() >= 256) {
            command_queue_.pop_back();
        }
        command_queue_.push_front(std::move(frame));
        return;
    }
    if (command_queue_.size() >= 256) {
        send_ack(frame.request_id, frame.opcode, frame.dst_id,
                 static_cast<int8_t>(ControlAckStatus::NotReady));
        return;
    }
    command_queue_.push_back(std::move(frame));
}

void RealReceiver::on_backend_connection(bool connected) {
    if (!connected) {
        enqueue_control(ControlFrame{
            ControlOpcode::ServiceStop, BRO30_NONE, 0, {}
        });
    }
}

void RealReceiver::send_ack(uint32_t request_id, ControlOpcode opcode,
                            uint16_t target, int8_t status, uint16_t counter) {
    if (request_id == 0) {
        return;
    }

    timespec now{};
    timespec_get(&now, TIME_UTC);
    control_ack_payload_t payload{
        request_id, static_cast<uint8_t>(opcode), status, counter
    };
    uint8_t frame[sizeof(tlv_header_t) + sizeof(payload)]{};
    const size_t size = build_tlv(
        frame, TLV_CONTROL_ACK, DEV_MON, target,
        static_cast<uint32_t>(now.tv_sec), static_cast<uint32_t>(now.tv_nsec),
        reinterpret_cast<const uint8_t*>(&payload), sizeof(payload)
    );
    fwd_.forward(frame, size);
}

void RealReceiver::begin_safe_stop(uint32_t request_id, int8_t status) {
    if (!mon_) {
        return;
    }

    const uint16_t target = service_target_;
    mon_->out.bup_srv_ctrl.lock(MJ_MSG_LOCK);
    mon_->out.bup_srv_ctrl.msg->hdr.dst =
        is_bup_target(target) ? target : static_cast<uint16_t>(BRO30_NONE);
    mon_->out.bup_srv_ctrl.msg->dr.mode = DRV_CTRL_MODE_OFF;
    mon_->out.bup_srv_ctrl.msg->dr.angle = 0.0f;
    mon_->out.bup_srv_ctrl.msg->dr.speed = 0.0f;
    mon_->out.bup_srv_ctrl.msg->dr.voltage = 0.0f;
    mon_->out.bup_srv_ctrl.msg->dr.current = 0.0f;
    mon_->out.bup_srv_ctrl.lock(MJ_MSG_UNLOCK);

    if (is_bup_target(target)) {
        mon_->out.bup_srv_ctrl.tx_enable = true;
        service_stopping_ = true;
        service_disable_at_ = std::chrono::steady_clock::now() +
                              std::chrono::milliseconds(50);
    } else {
        mon_->out.bup_srv_ctrl.tx_enable = false;
        service_stopping_ = false;
    }
    service_active_ = false;
    send_ack(request_id, ControlOpcode::ServiceStop, target, status,
             mon_->out.bup_srv_ctrl.msg->hdr.cnt);
}

void RealReceiver::process_control(ControlFrame frame) {
    if (!mon_) {
        send_ack(frame.request_id, frame.opcode, frame.dst_id,
                 static_cast<int8_t>(ControlAckStatus::NotReady));
        return;
    }
    // A disconnect-triggered STOP has no destination in its internal frame;
    // the protocol thread safely uses the currently active target.
    if (frame.opcode == ControlOpcode::ServiceStop) {
        if (!frame.body.empty()) {
            send_ack(frame.request_id, frame.opcode, frame.dst_id,
                     static_cast<int8_t>(ControlAckStatus::InvalidFrame));
            return;
        }
        begin_safe_stop(
            frame.request_id,
            static_cast<int8_t>(ControlAckStatus::ServiceStopped)
        );
        return;
    }
    if (!is_bup_target(frame.dst_id)) {
        send_ack(frame.request_id, frame.opcode, frame.dst_id,
                 static_cast<int8_t>(ControlAckStatus::InvalidTarget));
        return;
    }

    switch (frame.opcode) {
        case ControlOpcode::CommandRequest: {
            command_control_body_t body{};
            if (!read_body(frame, body) ||
                body.command < COMMAND_TYPE_SWRESET ||
                body.command > COMMAND_TYPE_CURRENT_SENSOR_CALIB) {
                send_ack(frame.request_id, frame.opcode, frame.dst_id,
                         static_cast<int8_t>(ControlAckStatus::InvalidValue));
                return;
            }
            mon_->out.cmd_req.msg->type = static_cast<command_type_k>(body.command);
            mon_->out.cmd_req.tx(frame.dst_id);
            send_ack(frame.request_id, frame.opcode, frame.dst_id,
                     static_cast<int8_t>(ControlAckStatus::Sent),
                     mon_->out.cmd_req.msg->hdr.cnt);
            return;
        }
        case ControlOpcode::MessageRequest: {
            message_control_body_t body{};
            if (!read_body(frame, body) || body.message_id == 0) {
                send_ack(frame.request_id, frame.opcode, frame.dst_id,
                         static_cast<int8_t>(ControlAckStatus::InvalidValue));
                return;
            }
            mon_->out.msg_req.msg->id = body.message_id;
            mon_->out.msg_req.tx(frame.dst_id);
            send_ack(frame.request_id, frame.opcode, frame.dst_id,
                     static_cast<int8_t>(ControlAckStatus::Sent),
                     mon_->out.msg_req.msg->hdr.cnt);
            return;
        }
        case ControlOpcode::ParameterRequest: {
            parameter_control_body_t body{};
            if (!read_body(frame, body) ||
                (body.type != PARAMETER_REQUEST_TYPE_READ &&
                 body.type != PARAMETER_REQUEST_TYPE_WRITE) ||
                body.parameter_id == 0) {
                send_ack(frame.request_id, frame.opcode, frame.dst_id,
                         static_cast<int8_t>(ControlAckStatus::InvalidValue));
                return;
            }
            mon_->out.prm_req.msg->type =
                static_cast<parameter_request_type_k>(body.type);
            mon_->out.prm_req.msg->id = body.parameter_id;
            memcpy(mon_->out.prm_req.msg->val, body.value, sizeof(body.value));
            mon_->out.prm_req.tx(frame.dst_id);
            send_ack(frame.request_id, frame.opcode, frame.dst_id,
                     static_cast<int8_t>(ControlAckStatus::Sent),
                     mon_->out.prm_req.msg->hdr.cnt);
            return;
        }
        case ControlOpcode::ServiceStart:
        case ControlOpcode::ServiceUpdate: {
            service_control_body_t body{};
            if (!read_body(frame, body) || !valid_service_mode(body.mode) ||
                !finite_service_values(body)) {
                send_ack(frame.request_id, frame.opcode, frame.dst_id,
                         static_cast<int8_t>(ControlAckStatus::InvalidValue));
                return;
            }
            if (frame.opcode == ControlOpcode::ServiceStart &&
                (service_active_ || service_stopping_)) {
                send_ack(frame.request_id, frame.opcode, frame.dst_id,
                         static_cast<int8_t>(ControlAckStatus::ServiceConflict));
                return;
            }
            if (frame.opcode == ControlOpcode::ServiceUpdate &&
                (!service_active_ || service_target_ != frame.dst_id)) {
                send_ack(frame.request_id, frame.opcode, frame.dst_id,
                         static_cast<int8_t>(ControlAckStatus::ServiceConflict));
                return;
            }

            mon_->out.bup_srv_ctrl.lock(MJ_MSG_LOCK);
            mon_->out.bup_srv_ctrl.msg->hdr.dst = frame.dst_id;
            mon_->out.bup_srv_ctrl.msg->dr.mode =
                static_cast<drv_ctrl_mode_k>(body.mode);
            mon_->out.bup_srv_ctrl.msg->dr.angle = body.angle;
            mon_->out.bup_srv_ctrl.msg->dr.speed = body.speed;
            mon_->out.bup_srv_ctrl.msg->dr.voltage = body.voltage;
            mon_->out.bup_srv_ctrl.msg->dr.current = body.current;
            mon_->out.bup_srv_ctrl.lock(MJ_MSG_UNLOCK);

            service_active_ = true;
            service_stopping_ = false;
            service_target_ = frame.dst_id;
            service_request_id_ = frame.request_id;
            const uint32_t lease_ms = std::clamp(body.lease_ms, 250u, 5000u);
            service_deadline_ = std::chrono::steady_clock::now() +
                                std::chrono::milliseconds(lease_ms);
            mon_->out.bup_srv_ctrl.tx_enable = true;
            send_ack(
                frame.request_id, frame.opcode, frame.dst_id,
                static_cast<int8_t>(
                    frame.opcode == ControlOpcode::ServiceStart
                        ? ControlAckStatus::ServiceStarted
                        : ControlAckStatus::ServiceUpdated
                ),
                mon_->out.bup_srv_ctrl.msg->hdr.cnt
            );
            return;
        }
        case ControlOpcode::ServiceKeepalive:
            if (!frame.body.empty() || !service_active_ ||
                service_target_ != frame.dst_id) {
                send_ack(frame.request_id, frame.opcode, frame.dst_id,
                         static_cast<int8_t>(ControlAckStatus::ServiceConflict));
                return;
            }
            service_deadline_ = std::chrono::steady_clock::now() +
                                std::chrono::milliseconds(1000);
            return;
        case ControlOpcode::ServiceStop:
            return; // handled before destination validation
        default:
            send_ack(frame.request_id, frame.opcode, frame.dst_id,
                     static_cast<int8_t>(ControlAckStatus::InvalidFrame));
            return;
    }
}

void RealReceiver::protocol_loop() {
    using clock = std::chrono::steady_clock;
    auto next_tick = clock::now();

    while (protocol_running_) {
        for (;;) {
            ControlFrame frame{};
            {
                std::lock_guard<std::mutex> lock(command_mutex_);
                if (command_queue_.empty()) {
                    break;
                }
                frame = std::move(command_queue_.front());
                command_queue_.pop_front();
            }
            process_control(std::move(frame));
        }

        const auto now = clock::now();
        if (service_active_ && now >= service_deadline_) {
            begin_safe_stop(
                service_request_id_,
                static_cast<int8_t>(ControlAckStatus::ServiceLeaseExpired)
            );
        }
        if (service_stopping_ && now >= service_disable_at_) {
            mon_->out.bup_srv_ctrl.tx_enable = false;
            service_stopping_ = false;
            service_target_ = BRO30_NONE;
            service_request_id_ = 0;
        }

        mon_->loop();
        mon_->tick();

        next_tick += std::chrono::microseconds(250);
        if (next_tick < clock::now() - std::chrono::milliseconds(5)) {
            next_tick = clock::now();
        }
        std::this_thread::sleep_until(next_tick);
    }
}

void RealReceiver::run() {
    signal(SIGINT, real_signal_handler);
    signal(SIGTERM, real_signal_handler);

    fprintf(stderr, "[REAL] Starting MicroJet MON on interface: %s\n",
            iface_.c_str());
    setEthernetInterfaceName(const_cast<char*>(iface_.c_str()));

    mj_drv_interface_t driver{};
    init_mjdrv(&driver);
    if (rx.socket < 0 || tx.socket < 0) {
        fprintf(stderr, "[REAL] FATAL: raw sockets are unavailable for '%s'.\n",
                iface_.c_str());
        while (g_real_running) sleep(5);
        return;
    }

    mj_mon_t* generated = nullptr;
    const mj_status_t init_status = mj_mon_init(&driver, &generated);
    if (init_status != MJ_OK || !generated) {
        fprintf(stderr, "[REAL] FATAL: mj_mon_init failed: %d\n",
                static_cast<int>(init_status));
        deinit_mjdrv();
        return;
    }
    mon_ = generated;

    // Generated code defaults this periodic output to true. Never let that
    // default reach a real actuator; Backend must explicitly send START.
    mon_->out.bup_srv_ctrl.tx_enable = false;
    mon_->out.bup_srv_ctrl.msg->hdr.dst = BRO30_NONE;
    mon_->out.bup_srv_ctrl.msg->dr.mode = DRV_CTRL_MODE_OFF;
    attach_device_irqs(mon_);

    setRxStatus(true);
    setTxStatus(true);
    protocol_running_ = true;
    protocol_thread_ = std::thread([this] { protocol_loop(); });

    fprintf(stderr,
            "[REAL] RX/TX ready. MON commands are disabled until Backend START.\n");
    unsigned stats_tick = 0;
    while (g_real_running) {
        sleep(1);
        if (++stats_tick >= 5) {
            log_packet_stats();
            stats_tick = 0;
        }
    }

    // The protocol thread is the only owner of service state and generated
    // output fields. Route shutdown through the same queue to avoid a data
    // race and keep two final OFF frames on the wire before TX is disabled.
    enqueue_control(ControlFrame{
        ControlOpcode::ServiceStop, BRO30_NONE, 0, {}
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(60));
    protocol_running_ = false;
    if (protocol_thread_.joinable()) {
        protocol_thread_.join();
    }
    mon_->out.bup_srv_ctrl.tx_enable = false;
    mon_ = nullptr;
    deinit_mjdrv();
    fprintf(stderr, "[REAL] Receiver stopped.\n");
}
