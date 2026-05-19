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

void handle_dcu_telemetry(TcpForwarder& fwd,
                           uint16_t src_id, uint16_t dst_id,
                           uint32_t ts_s, uint32_t ts_ns,
                           const uint8_t* payload, size_t size);

void handle_bup_state(TcpForwarder& fwd,
                       uint16_t src_id, uint16_t dst_id,
                       uint32_t ts_s, uint32_t ts_ns,
                       const uint8_t* payload, size_t size);

// Диспетчер: разбирает MJ-заголовок из raw payload (header+data)
// и вызывает нужный обработчик. payload — начиная с MJ-заголовка.
void dispatch_mj_packet(TcpForwarder& fwd,
                          const uint8_t* payload, size_t total_size);
