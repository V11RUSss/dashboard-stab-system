#include "packet_handlers.hpp"
#include "protocol.hpp"
#include "control_protocol.hpp"

#include <array>
#include <cstdio>
#include <cstring>
#include <utility>
#include <vector>

#define CHECK(expression)                                                        \
    do {                                                                         \
        if (!(expression)) {                                                     \
            fprintf(stderr, "CHECK failed at %s:%d: %s\n",                    \
                    __FILE__, __LINE__, #expression);                            \
            return 1;                                                            \
        }                                                                        \
    } while (0)

namespace {

std::vector<uint8_t> packet(uint16_t id, uint16_t source, size_t payload_size)
{
    mj_header_t header{};
    header.src = source;
    header.dst = DEV_MON;
    header.id = id;
    header.cnt = 42;
    header.ts_s = 123;
    header.ts_low = 456789;

    std::vector<uint8_t> result(sizeof(header) + payload_size, 0);
    memcpy(result.data(), &header, sizeof(header));
    return result;
}

} // namespace

int main()
{
    TcpForwarder disconnected("127.0.0.1", 1);

    auto dcu = packet(MJ_ID_DCU_TEL, DEV_BUP_LFD, MJ_DCU_TEL_PAYLOAD_SIZE);
    CHECK(dispatch_mj_packet(disconnected, dcu.data(), dcu.size()) ==
          DispatchResult::BackendUnavailable);

    auto bup = packet(MJ_ID_BUP_STATE, DEV_BUP_LFD, MJ_BUP_STATE_PAYLOAD_SIZE);
    CHECK(dispatch_mj_packet(disconnected, bup.data(), bup.size()) ==
          DispatchResult::BackendUnavailable);

    const std::array<std::pair<uint16_t, size_t>, 4> responses{{
        {MJ_ID_CMD_ANS, MJ_CMD_ANS_PAYLOAD_SIZE},
        {MJ_ID_MSG_ANS, MJ_MSG_ANS_PAYLOAD_SIZE},
        {MJ_ID_PRM_ANS, MJ_PRM_ANS_PAYLOAD_SIZE},
        {MJ_ID_FORMULAR, MJ_FORMULAR_PAYLOAD_SIZE},
    }};
    for (const auto& response : responses) {
        auto message = packet(response.first, DEV_BUP_LFD, response.second);
        CHECK(dispatch_mj_packet(disconnected, message.data(), message.size()) ==
              DispatchResult::BackendUnavailable);
    }

    auto wrong_size = packet(MJ_ID_DCU_TEL, DEV_BUP_LFD, MJ_DCU_TEL_PAYLOAD_SIZE - 1);
    CHECK(dispatch_mj_packet(disconnected, wrong_size.data(), wrong_size.size()) ==
          DispatchResult::RejectSize);

    auto wrong_id = packet(0x3894, DEV_BUP_LFD, 16);
    CHECK(dispatch_mj_packet(disconnected, wrong_id.data(), wrong_id.size()) ==
          DispatchResult::RejectId);

    auto wrong_source = packet(MJ_ID_DCU_TEL, 0x1234, MJ_DCU_TEL_PAYLOAD_SIZE);
    CHECK(dispatch_mj_packet(disconnected, wrong_source.data(), wrong_source.size()) ==
          DispatchResult::RejectSource);

    auto wrong_timestamp = packet(MJ_ID_DCU_TEL, DEV_BUP_LFD, MJ_DCU_TEL_PAYLOAD_SIZE);
    auto *header = reinterpret_cast<mj_header_t *>(wrong_timestamp.data());
    header->ts_low = 1'000'000'000u;
    CHECK(dispatch_mj_packet(disconnected, wrong_timestamp.data(), wrong_timestamp.size()) ==
          DispatchResult::RejectTimestamp);

    std::array<uint8_t, sizeof(tlv_header_t) + 4> tlv{};
    const std::array<uint8_t, 4> payload{{1, 2, 3, 4}};
    const size_t size = build_tlv(
        tlv.data(), TLV_DCU_TEL, DEV_BUP_LFD, DEV_MON, 7, 8,
        payload.data(), payload.size()
    );
    CHECK(size == tlv.size());
    tlv_header_t tlv_header{};
    memcpy(&tlv_header, tlv.data(), sizeof(tlv_header));
    CHECK(tlv_header.type == TLV_DCU_TEL);
    CHECK(tlv_header.src_id == DEV_BUP_LFD);
    CHECK(tlv_header.length == payload.size());
    CHECK(memcmp(tlv.data() + sizeof(tlv_header), payload.data(), payload.size()) == 0);

    control_header_t control{};
    control.magic = CONTROL_MAGIC;
    control.version = CONTROL_VERSION;
    control.opcode = static_cast<uint8_t>(ControlOpcode::ParameterRequest);
    control.dst_id = DEV_BUP_LFD;
    control.request_id = 77;
    control.length = sizeof(parameter_control_body_t);
    CHECK(sizeof(control) == 14);
    CHECK(control.dst_id != 0xFFFF);
    CHECK(control.length == 40);

    return 0;
}
