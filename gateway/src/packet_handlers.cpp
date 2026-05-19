#include "packet_handlers.hpp"
#include "protocol.hpp"
#include <cstdio>
#include <cstring>

// Expected minimum payload sizes (data only, without MJ header)
static const size_t MIN_DCU_TEL_SIZE   = 235; // dcu_telemetry_t
static const size_t MIN_BUP_STATE_SIZE = 60;  // bup_drv_state_t

void handle_dcu_telemetry(TcpForwarder& fwd,
                           uint16_t src_id, uint16_t dst_id,
                           uint32_t ts_s, uint32_t ts_ns,
                           const uint8_t* payload, size_t size)
{
    if (size < MIN_DCU_TEL_SIZE) {
        fprintf(stderr, "[PKT] dcu_tel too small: %zu < %zu\n", size, MIN_DCU_TEL_SIZE);
        return;
    }
    // Build TLV frame: 15 header bytes + payload
    const size_t frame_len = sizeof(tlv_header_t) + size;
    uint8_t* frame = new uint8_t[frame_len];
    build_tlv(frame, TLV_DCU_TEL, src_id, dst_id, ts_s, ts_ns,
              payload, (uint16_t)size);
    fwd.forward(frame, frame_len);
    delete[] frame;
    fprintf(stderr, "[PKT] dcu_tel forwarded: src=0x%04X size=%zu\n", src_id, size);
}

void handle_bup_state(TcpForwarder& fwd,
                       uint16_t src_id, uint16_t dst_id,
                       uint32_t ts_s, uint32_t ts_ns,
                       const uint8_t* payload, size_t size)
{
    if (size < MIN_BUP_STATE_SIZE) {
        fprintf(stderr, "[PKT] bup_state too small: %zu < %zu\n", size, MIN_BUP_STATE_SIZE);
        return;
    }
    const size_t frame_len = sizeof(tlv_header_t) + size;
    uint8_t* frame = new uint8_t[frame_len];
    build_tlv(frame, TLV_BUP_STATE, src_id, dst_id, ts_s, ts_ns,
              payload, (uint16_t)size);
    fwd.forward(frame, frame_len);
    delete[] frame;
    fprintf(stderr, "[PKT] bup_state forwarded: src=0x%04X size=%zu\n", src_id, size);
}

void dispatch_mj_packet(TcpForwarder& fwd,
                          const uint8_t* data, size_t total_size)
{
    if (total_size < sizeof(mj_header_t)) {
        fprintf(stderr, "[PKT] Too short for MJ header: %zu\n", total_size);
        return;
    }

    mj_header_t hdr;
    memcpy(&hdr, data, sizeof(hdr));

    const uint8_t* payload = data + sizeof(mj_header_t);
    const size_t   payload_size = total_size - sizeof(mj_header_t);
    const uint32_t ts_ns = hdr.ts_low & 0x3FFFFFFF; // lower 30 bits

    switch (hdr.id) {
        case MJ_ID_DCU_TEL:
            handle_dcu_telemetry(fwd, hdr.src, hdr.dst,
                                  hdr.ts_s, ts_ns, payload, payload_size);
            break;
        case MJ_ID_BUP_STATE:
            handle_bup_state(fwd, hdr.src, hdr.dst,
                              hdr.ts_s, ts_ns, payload, payload_size);
            break;
        default:
            // silently drop unknown message types
            break;
    }
}
