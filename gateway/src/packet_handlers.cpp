#include "packet_handlers.hpp"
#include "protocol.hpp"
#include <atomic>
#include <cstdio>
#include <cstring>
#include <vector>

namespace {

struct PacketStats {
    std::atomic<uint64_t> received{0};
    std::atomic<uint64_t> forwarded{0};
    std::atomic<uint64_t> backend_unavailable{0};
    std::atomic<uint64_t> rejected_header{0};
    std::atomic<uint64_t> rejected_id{0};
    std::atomic<uint64_t> rejected_size{0};
    std::atomic<uint64_t> rejected_source{0};
    std::atomic<uint64_t> rejected_timestamp{0};
};

PacketStats g_stats;

bool is_supported_source(uint16_t src_id)
{
    switch (src_id) {
        case DEV_DCU:
        case DEV_BUP_LFD:
        case DEV_BUP_RFD:
        case DEV_BUP_LRD:
        case DEV_BUP_RRD:
        case DEV_BUP_LST:
        case DEV_BUP_RST:
            return true;
        default:
            return false;
    }
}

bool forward_payload(TcpForwarder& fwd, uint8_t type,
                     uint16_t src_id, uint16_t dst_id,
                     uint32_t ts_s, uint32_t ts_ns,
                     const uint8_t* payload, size_t size)
{
    const size_t frame_len = sizeof(tlv_header_t) + size;
    std::vector<uint8_t> frame(frame_len);
    build_tlv(frame.data(), type, src_id, dst_id, ts_s, ts_ns,
              payload, static_cast<uint16_t>(size));
    return fwd.forward(frame.data(), frame.size());
}

DispatchResult record(DispatchResult result)
{
    switch (result) {
        case DispatchResult::Forwarded:          ++g_stats.forwarded; break;
        case DispatchResult::BackendUnavailable: ++g_stats.backend_unavailable; break;
        case DispatchResult::RejectHeader:       ++g_stats.rejected_header; break;
        case DispatchResult::RejectId:           ++g_stats.rejected_id; break;
        case DispatchResult::RejectSize:         ++g_stats.rejected_size; break;
        case DispatchResult::RejectSource:       ++g_stats.rejected_source; break;
        case DispatchResult::RejectTimestamp:    ++g_stats.rejected_timestamp; break;
    }
    return result;
}

} // namespace

DispatchResult dispatch_mj_packet(TcpForwarder& fwd,
                                  const uint8_t* data, size_t total_size)
{
    ++g_stats.received;
    if (!data || total_size < sizeof(mj_header_t)) {
        return record(DispatchResult::RejectHeader);
    }

    mj_header_t hdr;
    memcpy(&hdr, data, sizeof(hdr));
    const uint8_t* payload = data + sizeof(mj_header_t);
    const size_t payload_size = total_size - sizeof(mj_header_t);
    const uint32_t ts_ns = hdr.ts_low & 0x3FFFFFFFu;

    if (!is_supported_source(hdr.src)) {
        return record(DispatchResult::RejectSource);
    }
    if (ts_ns >= 1000000000u) {
        return record(DispatchResult::RejectTimestamp);
    }

    switch (hdr.id) {
        case MJ_ID_DCU_TEL:
            if (payload_size != MJ_DCU_TEL_PAYLOAD_SIZE) {
                return record(DispatchResult::RejectSize);
            }
            return record(forward_payload(
                fwd, TLV_DCU_TEL, hdr.src, hdr.dst, hdr.ts_s, ts_ns,
                payload, payload_size
            ) ? DispatchResult::Forwarded : DispatchResult::BackendUnavailable);
        case MJ_ID_BUP_STATE:
            if (payload_size != MJ_BUP_STATE_PAYLOAD_SIZE) {
                return record(DispatchResult::RejectSize);
            }
            return record(forward_payload(
                fwd, TLV_BUP_STATE, hdr.src, hdr.dst, hdr.ts_s, ts_ns,
                payload, payload_size
            ) ? DispatchResult::Forwarded : DispatchResult::BackendUnavailable);
        case MJ_ID_CMD_ANS:
            if (payload_size != MJ_CMD_ANS_PAYLOAD_SIZE) {
                return record(DispatchResult::RejectSize);
            }
            return record(forward_payload(
                fwd, TLV_CMD_ANS, hdr.src, hdr.dst, hdr.ts_s, ts_ns,
                payload, payload_size
            ) ? DispatchResult::Forwarded : DispatchResult::BackendUnavailable);
        case MJ_ID_MSG_ANS:
            if (payload_size != MJ_MSG_ANS_PAYLOAD_SIZE) {
                return record(DispatchResult::RejectSize);
            }
            return record(forward_payload(
                fwd, TLV_MSG_ANS, hdr.src, hdr.dst, hdr.ts_s, ts_ns,
                payload, payload_size
            ) ? DispatchResult::Forwarded : DispatchResult::BackendUnavailable);
        case MJ_ID_PRM_ANS:
            if (payload_size != MJ_PRM_ANS_PAYLOAD_SIZE) {
                return record(DispatchResult::RejectSize);
            }
            return record(forward_payload(
                fwd, TLV_PRM_ANS, hdr.src, hdr.dst, hdr.ts_s, ts_ns,
                payload, payload_size
            ) ? DispatchResult::Forwarded : DispatchResult::BackendUnavailable);
        case MJ_ID_FORMULAR:
            if (payload_size != MJ_FORMULAR_PAYLOAD_SIZE) {
                return record(DispatchResult::RejectSize);
            }
            return record(forward_payload(
                fwd, TLV_FORMULAR, hdr.src, hdr.dst, hdr.ts_s, ts_ns,
                payload, payload_size
            ) ? DispatchResult::Forwarded : DispatchResult::BackendUnavailable);
        default:
            return record(DispatchResult::RejectId);
    }
}

PacketStatsSnapshot packet_stats_snapshot()
{
    return {
        g_stats.received.load(),
        g_stats.forwarded.load(),
        g_stats.backend_unavailable.load(),
        g_stats.rejected_header.load(),
        g_stats.rejected_id.load(),
        g_stats.rejected_size.load(),
        g_stats.rejected_source.load(),
        g_stats.rejected_timestamp.load(),
    };
}

void log_packet_stats()
{
    const auto s = packet_stats_snapshot();
    static PacketStatsSnapshot previous{};
    static bool have_previous = false;
    if (have_previous &&
        s.received == previous.received &&
        s.forwarded == previous.forwarded &&
        s.backend_unavailable == previous.backend_unavailable &&
        s.rejected_header == previous.rejected_header &&
        s.rejected_id == previous.rejected_id &&
        s.rejected_size == previous.rejected_size &&
        s.rejected_source == previous.rejected_source &&
        s.rejected_timestamp == previous.rejected_timestamp) {
        return;
    }
    previous = s;
    have_previous = true;
    fprintf(stderr,
            "[PKT] total rx=%llu fwd=%llu no_backend=%llu "
            "reject(header=%llu id=%llu size=%llu src=%llu ts=%llu)\n",
            static_cast<unsigned long long>(s.received),
            static_cast<unsigned long long>(s.forwarded),
            static_cast<unsigned long long>(s.backend_unavailable),
            static_cast<unsigned long long>(s.rejected_header),
            static_cast<unsigned long long>(s.rejected_id),
            static_cast<unsigned long long>(s.rejected_size),
            static_cast<unsigned long long>(s.rejected_source),
            static_cast<unsigned long long>(s.rejected_timestamp));
}
