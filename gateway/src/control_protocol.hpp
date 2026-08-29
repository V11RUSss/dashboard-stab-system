#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

// Backend → Gateway control protocol. It runs in the reverse direction of the
// existing TLV stream on the same TCP connection. All fields are little-endian.
static const uint32_t CONTROL_MAGIC = 0x31434A4Du; // bytes: "MJC1"
static const uint8_t CONTROL_VERSION = 1;
static const size_t CONTROL_MAX_BODY = 64;

enum class ControlOpcode : uint8_t {
    CommandRequest = 0x01,
    MessageRequest = 0x02,
    ParameterRequest = 0x03,
    ServiceStart = 0x04,
    ServiceUpdate = 0x05,
    ServiceStop = 0x06,
    ServiceKeepalive = 0x07,
};

enum class ControlAckStatus : int8_t {
    Sent = 0,
    ServiceStarted = 1,
    ServiceUpdated = 2,
    ServiceStopped = 3,
    ServiceLeaseExpired = 4,
    InvalidFrame = -1,
    InvalidTarget = -2,
    InvalidValue = -3,
    NotReady = -4,
    ServiceConflict = -5,
};

#pragma pack(push, 1)
struct control_header_t {
    uint32_t magic;
    uint8_t version;
    uint8_t opcode;
    uint16_t dst_id;
    uint32_t request_id;
    uint16_t length;
};

struct command_control_body_t {
    int32_t command;
};

struct message_control_body_t {
    uint16_t message_id;
};

struct parameter_control_body_t {
    int32_t type;
    uint32_t parameter_id;
    uint8_t value[32];
};

struct service_control_body_t {
    int32_t mode;
    float angle;
    float speed;
    float voltage;
    float current;
    uint32_t lease_ms;
};
#pragma pack(pop)

static_assert(sizeof(control_header_t) == 14, "control header must be 14 bytes");
static_assert(sizeof(command_control_body_t) == 4, "command body must be 4 bytes");
static_assert(sizeof(message_control_body_t) == 2, "message body must be 2 bytes");
static_assert(sizeof(parameter_control_body_t) == 40, "parameter body must be 40 bytes");
static_assert(sizeof(service_control_body_t) == 24, "service body must be 24 bytes");

struct ControlFrame {
    ControlOpcode opcode;
    uint16_t dst_id;
    uint32_t request_id;
    std::vector<uint8_t> body;
};
