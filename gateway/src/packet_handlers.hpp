#pragma once
#include "tcp_forwarder.hpp"
#include <cstdint>
#include <cstddef>

// ============================================================
//  Обработчики MicroJet-пакетов
//
//  Оба принимают raw payload (без MJ-заголовка) + заголовочные поля
//  и упаковывают в TLV для отправки на backend.
// ============================================================

enum class DispatchResult {
    Forwarded,
    BackendUnavailable,
    RejectHeader,
    RejectId,
    RejectSize,
    RejectSource,
    RejectTimestamp,
};

struct PacketStatsSnapshot {
    uint64_t received;
    uint64_t forwarded;
    uint64_t backend_unavailable;
    uint64_t rejected_header;
    uint64_t rejected_id;
    uint64_t rejected_size;
    uint64_t rejected_source;
    uint64_t rejected_timestamp;
};

// Диспетчер: разбирает MJ-заголовок из raw payload (header+data)
// и вызывает нужный обработчик. payload — начиная с MJ-заголовка.
DispatchResult dispatch_mj_packet(TcpForwarder& fwd,
                                  const uint8_t* payload, size_t total_size);

PacketStatsSnapshot packet_stats_snapshot();
void log_packet_stats();
