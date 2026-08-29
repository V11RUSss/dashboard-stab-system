#pragma once
#include <cstdint>
#include <cstddef>

// ============================================================
//  TLV-пакет: gateway → backend
//
//  Формат (little-endian):
//  [uint8_t  type    ]  — тип: TLV_DCU_TEL / TLV_BUP_STATE
//  [uint16_t src_id  ]  — ID устройства-источника (bro30_dev_id_k)
//  [uint16_t dst_id  ]  — ID устройства-назначения
//  [uint32_t ts_s    ]  — timestamp секунды
//  [uint32_t ts_ns   ]  — timestamp наносекунды
//  [uint16_t length  ]  — размер payload (байт)
//  [uint8_t  payload ]  — бинарные данные структуры
//
//  Итого заголовок = 15 байт.
// ============================================================

static const uint8_t TLV_DCU_TEL      = 0x01;
static const uint8_t TLV_BUP_STATE    = 0x02;
static const uint8_t TLV_CMD_ANS      = 0x03;
static const uint8_t TLV_MSG_ANS      = 0x04;
static const uint8_t TLV_PRM_ANS      = 0x05;
static const uint8_t TLV_FORMULAR     = 0x06;
static const uint8_t TLV_CONTROL_ACK  = 0x10;

#pragma pack(push, 1)
struct tlv_header_t {
    uint8_t  type;
    uint16_t src_id;
    uint16_t dst_id;
    uint32_t ts_s;
    uint32_t ts_ns;
    uint16_t length;
};
#pragma pack(pop)

static_assert(sizeof(tlv_header_t) == 15, "TLV header must be 15 bytes");

// MicroJet message header (16 bytes, from microjet_def.h)
#pragma pack(push, 1)
struct mj_header_t {
    uint16_t src;
    uint16_t dst;
    uint16_t id;
    uint16_t cnt;
    uint32_t ts_s;
    uint32_t ts_low;  // bits[31:30] = ts_status, bits[29:0] = ts_ns
};
#pragma pack(pop)

static_assert(sizeof(mj_header_t) == 16, "MJ header must be 16 bytes");

// известные message IDs (из mjsysspec.h)
static const uint16_t MJ_ID_DCU_TEL   = 0xD63C;
static const uint16_t MJ_ID_BUP_STATE = 0x0824;
static const uint16_t MJ_ID_CMD_ANS   = 0xFB98;
static const uint16_t MJ_ID_MSG_ANS   = 0xD80F;
static const uint16_t MJ_ID_PRM_ANS   = 0xFCC8;
static const uint16_t MJ_ID_FORMULAR  = 0x10AD;

// Payload sizes from generated mjtypes.h. The MicroJet callbacks receive the
// 16-byte message header followed by exactly one of these structures.
// Generated sizeof(dcu_telemetry_t) is 236: 235 bytes of fields plus one byte
// of tail padding required by the native generated C message layout.
static const size_t MJ_DCU_TEL_PAYLOAD_SIZE   = 236;
static const size_t MJ_BUP_STATE_PAYLOAD_SIZE = 60;
static const size_t MJ_CMD_ANS_PAYLOAD_SIZE   = 44;
static const size_t MJ_MSG_ANS_PAYLOAD_SIZE   = 12;
static const size_t MJ_PRM_ANS_PAYLOAD_SIZE   = 40;
// formular_info_t contains 261 meaningful bytes and three bytes of native
// tail padding because the generated structure is aligned to uint32_t.
static const size_t MJ_FORMULAR_PAYLOAD_SIZE  = 264;

// известные device IDs (из mjsysspec.h)
static const uint16_t DEV_DCU         = 0xB8FE;
static const uint16_t DEV_BUP_LFD     = 0xFA82;
static const uint16_t DEV_BUP_RFD     = 0xD334;
static const uint16_t DEV_BUP_LRD     = 0x57F6;
static const uint16_t DEV_BUP_RRD     = 0x7E40;
static const uint16_t DEV_BUP_LST     = 0x7659;
static const uint16_t DEV_BUP_RST     = 0x5FEF;
static const uint16_t DEV_AUFD        = 0xF5B6;
static const uint16_t DEV_MON         = 0x6C99;

// Gateway acknowledgement for a backend → gateway control frame. This only
// confirms local validation/transmission. Device success is reported later by
// cmd_ans/msg_ans/prm_ans.
#pragma pack(push, 1)
struct control_ack_payload_t {
    uint32_t request_id;
    uint8_t  opcode;
    int8_t   status;
    uint16_t mj_counter;
};
#pragma pack(pop)

static_assert(sizeof(control_ack_payload_t) == 8,
              "control acknowledgement must be 8 bytes");

// build a TLV frame into buf (must be at least 15+payload_len bytes)
// returns total frame size
inline size_t build_tlv(uint8_t* buf, uint8_t type,
                         uint16_t src_id, uint16_t dst_id,
                         uint32_t ts_s, uint32_t ts_ns,
                         const uint8_t* payload, uint16_t payload_len)
{
    tlv_header_t hdr;
    hdr.type    = type;
    hdr.src_id  = src_id;
    hdr.dst_id  = dst_id;
    hdr.ts_s    = ts_s;
    hdr.ts_ns   = ts_ns;
    hdr.length  = payload_len;
    __builtin_memcpy(buf, &hdr, sizeof(hdr));
    __builtin_memcpy(buf + sizeof(hdr), payload, payload_len);
    return sizeof(hdr) + payload_len;
}
