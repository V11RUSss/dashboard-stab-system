"""Safe Backend → Gateway → MicroJet control channel."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import struct
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from core.state import utc_now_iso

logger = logging.getLogger(__name__)

CONTROL_MAGIC = 0x31434A4D  # bytes on the wire: MJC1
CONTROL_VERSION = 1
CONTROL_HEADER_FMT = "<IBBHIH"
CONTROL_MAX_BODY = 64

OP_COMMAND = 1
OP_MESSAGE = 2
OP_PARAMETER = 3
OP_SERVICE_START = 4
OP_SERVICE_UPDATE = 5
OP_SERVICE_STOP = 6
OP_SERVICE_KEEPALIVE = 7

DEVICE_IDS = {
    "bup_dcu_lfd": 0xFA82,
    "bup_dcu_rfd": 0xD334,
    "bup_dcu_lrd": 0x57F6,
    "bup_dcu_rrd": 0x7E40,
    "bup_dcu_lst": 0x7659,
    "bup_dcu_rst": 0x5FEF,
}
DEVICE_NAMES_BY_ID = {value: key for key, value in DEVICE_IDS.items()}

COMMANDS = [
    {"value": 0, "name": "SWRESET", "label": "Перезагрузка", "dangerous": True},
    {"value": 1, "name": "SAVEPRM", "label": "Сохранить параметры", "dangerous": True},
    {"value": 2, "name": "SET_HOME_POSITION", "label": "Задать домашнюю позицию", "dangerous": True},
    {"value": 3, "name": "SET_UP_LIMIT", "label": "Записать верхний предел", "dangerous": True},
    {"value": 4, "name": "SET_DOWN_LIMIT", "label": "Записать нижний предел", "dangerous": True},
    {"value": 5, "name": "SET_EL_ZERO", "label": "Записать электрический ноль", "dangerous": True},
    {"value": 6, "name": "OPERATION_TIME_RESET", "label": "Сбросить наработку", "dangerous": True},
    {"value": 7, "name": "INTEGRATOR_RESET", "label": "Сбросить интеграторы", "dangerous": False},
    {"value": 8, "name": "FIRMWARE_UPGRADE", "label": "Обновить ПО", "dangerous": True},
    {"value": 9, "name": "FIRMWARE_VERIFY", "label": "Проверить ПО", "dangerous": False},
    {"value": 10, "name": "CURRENT_SENSOR_CALIB", "label": "Калибровать датчики тока", "dangerous": True},
]

MESSAGES = [
    {"id": 0x0824, "name": "bup_state", "label": "Состояние привода"},
    {"id": 0x10AD, "name": "formular", "label": "Электронный формуляр"},
]

SERVICE_MODES = [
    {
        "value": 1,
        "name": "OFF",
        "label": "OFF — нулевое задание",
        "description": "Поля задания не используются; в поток отправляются нулевые значения.",
        "fields": [],
    },
    {
        "value": 2,
        "name": "FREEMOVE",
        "label": "FREEMOVE — свободное движение",
        "description": "Режим без числового задания со стороны Dashboard.",
        "fields": [],
    },
    {
        "value": 3,
        "name": "HOLD",
        "label": "HOLD — удержание",
        "description": "Команда удержания без отдельного числового задания.",
        "fields": [],
    },
    {
        "value": 4,
        "name": "ANGLE",
        "label": "ANGLE — заданный угол",
        "description": "Используется поле «Угол» в радианах.",
        "fields": ["angle"],
    },
    {
        "value": 5,
        "name": "SPEED",
        "label": "SPEED — заданная скорость",
        "description": "Используется поле «Скорость» в радианах в секунду.",
        "fields": ["speed"],
    },
    {
        "value": 6,
        "name": "RSPEED",
        "label": "RSPEED — скорость ротора",
        "description": "Используется поле «Скорость» в радианах в секунду.",
        "fields": ["speed"],
    },
    {
        "value": 7,
        "name": "VOLTAGE",
        "label": "VOLTAGE — заданное напряжение",
        "description": "Используется поле «Напряжение» в вольтах.",
        "fields": ["voltage"],
    },
    {
        "value": 8,
        "name": "SYNCHRO",
        "label": "SYNCHRO — синхронный режим",
        "description": "Передаются все четыре поля; их трактовку определяет прошивка контроллера.",
        "fields": ["angle", "speed", "voltage", "current"],
    },
    {
        "value": 9,
        "name": "CURRENT",
        "label": "CURRENT — заданный ток",
        "description": "Используется поле «Ток» в амперах.",
        "fields": ["current"],
    },
    {
        "value": 10,
        "name": "VOLTAGE_A",
        "label": "VOLTAGE_A — напряжение фазы A",
        "description": "Используется поле «Напряжение» в вольтах.",
        "fields": ["voltage"],
    },
    {
        "value": 11,
        "name": "VOLTAGE_B",
        "label": "VOLTAGE_B — напряжение фазы B",
        "description": "Используется поле «Напряжение» в вольтах.",
        "fields": ["voltage"],
    },
    {
        "value": 12,
        "name": "VOLTAGE_C",
        "label": "VOLTAGE_C — напряжение фазы C",
        "description": "Используется поле «Напряжение» в вольтах.",
        "fields": ["voltage"],
    },
]

Sender = Callable[[bytes], Awaitable[None]]
Broadcaster = Callable[[str, dict], Awaitable[None]]


def build_control_frame(opcode: int, dst_id: int, request_id: int, body: bytes) -> bytes:
    """Build one versioned reverse-direction frame."""
    if opcode not in range(OP_COMMAND, OP_SERVICE_KEEPALIVE + 1):
        raise ValueError("Неизвестная операция управления")
    if dst_id not in DEVICE_NAMES_BY_ID:
        raise ValueError("Команды можно отправлять только одному из шести МУП")
    if len(body) > CONTROL_MAX_BODY:
        raise ValueError("Слишком большое тело команды")
    return struct.pack(
        CONTROL_HEADER_FMT,
        CONTROL_MAGIC,
        CONTROL_VERSION,
        opcode,
        dst_id,
        request_id,
        len(body),
    ) + body


def _candidate_path(env_name: str, relative: str) -> Path:
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured)
    container_path = Path("/app/protocol") / Path(relative).name
    if container_path.exists():
        return container_path
    return Path(__file__).resolve().parents[2] / relative


def _load_parameter_catalog() -> list[dict]:
    """Join human metadata from bro_spec with generated numeric IDs."""
    spec_path = _candidate_path("BRO_SPEC_PATH", "gateway/bro_spec.json")
    header_path = _candidate_path(
        "MJ_SYS_SPEC_PATH", "gateway/microjetlib/inc/mjsysspec.h"
    )
    device_spec_path = _candidate_path(
        "MJ_DCU_SPEC_PATH",
        "gateway/microjetlib/bup_dcu_lfd/mj_bup_dcu_lfd_spec.c",
    )
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        header = header_path.read_text(encoding="utf-8")
        device_spec = device_spec_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        logger.error("Не удалось загрузить каталог параметров: %s", exc)
        return []

    id_pattern = re.compile(
        r"PRM_(BOOL|FLOAT|INT|STRING)_([A-Z0-9_]+)\s*=\s*"
        r"\(\(int\)0x([0-9A-Fa-f]+)\)"
    )
    generated = {
        name.lower(): {"type": kind.lower(), "id": int(hex_value, 16)}
        for kind, name, hex_value in id_pattern.findall(header)
    }
    definitions = spec.get("parameters", {})
    defaults = spec.get("devices", {}).get("dcu", {}).get("parameters", {})

    list_match = re.search(
        r"static const uint32_t _prm_ids\[\d+\]\s*=\s*\{(.*?)\};",
        device_spec,
        re.DOTALL,
    )
    if not list_match:
        logger.error("В generated DCU spec не найден список _prm_ids")
        return []
    parameter_names = [
        name.lower()
        for name in re.findall(
            r"PRM_(?:BOOL|FLOAT|INT|STRING)_([A-Z0-9_]+)",
            list_match.group(1),
        )
    ]

    result = []
    for name in parameter_names:
        default = defaults.get(name)
        definition = definitions.get(name, {})
        numeric = generated.get(name.lower())
        if not numeric:
            logger.warning("Для параметра %s не найден generated ID", name)
            continue
        result.append(
            {
                "name": name,
                "id": numeric["id"],
                "id_hex": f"0x{numeric['id']:08X}",
                "type": numeric["type"],
                "unit": definition.get("unit", "-"),
                "default": default,
                "system": name not in defaults,
            }
        )
    return result


def _as_finite_float(value, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}: требуется число") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field}: требуется конечное число")
    return result


class ControlService:
    """Validate UI actions, serialize frames and track control responses."""

    LEASE_MS = 1200
    KEEPALIVE_INTERVAL_S = 0.4
    EVENT_LIMIT = 100

    def __init__(self, shared_state: dict) -> None:
        self.shared_state = shared_state
        self.parameters = _load_parameter_catalog()
        self.parameters_by_name = {item["name"]: item for item in self.parameters}
        self.parameters_by_id = {item["id"]: item for item in self.parameters}
        self.sender: Sender | None = None
        self.broadcaster: Broadcaster | None = None
        self._request_id = 0
        self._pending: dict[int, dict] = {}
        self._response_context: dict[tuple, dict] = {}
        self._send_lock = asyncio.Lock()
        self._service_owner = None
        self._keepalive_task: asyncio.Task | None = None
        self._gateway_connected = False
        self.shared_state.setdefault(
            "control_state",
            {
                "service": {"active": False, "device_id": None},
                "parameters": {},
                "events": deque(maxlen=self.EVENT_LIMIT),
            },
        )

    def set_sender(self, sender: Sender) -> None:
        self.sender = sender

    def set_broadcaster(self, broadcaster: Broadcaster) -> None:
        self.broadcaster = broadcaster

    def catalog(self) -> dict:
        return {
            "devices": [
                {"device_id": name, "raw_id": raw_id}
                for name, raw_id in DEVICE_IDS.items()
            ],
            "parameters": self.parameters,
            "commands": COMMANDS,
            "messages": MESSAGES,
            "service_modes": SERVICE_MODES,
        }

    def snapshot(self) -> dict:
        state = self.shared_state["control_state"]
        return {
            "gateway_connected": self._gateway_connected,
            "service": dict(state["service"]),
            "parameters": {
                device: dict(values)
                for device, values in state["parameters"].items()
            },
            "events": list(state["events"]),
        }

    async def set_gateway_connected(self, connected: bool) -> None:
        self._gateway_connected = connected
        if not connected:
            self._clear_service_state("gateway_disconnected")
        await self._broadcast("control_snapshot", self.snapshot())

    async def _broadcast(self, kind: str, payload: dict) -> None:
        if self.broadcaster is not None:
            await self.broadcaster(kind, payload)

    def _next_request_id(self) -> int:
        self._request_id = (self._request_id % 0xFFFFFFFF) + 1
        return self._request_id

    def _target(self, payload: dict) -> tuple[str, int]:
        device_id = payload.get("device_id")
        if device_id not in DEVICE_IDS:
            raise ValueError("Выберите один из шести контроллеров")
        return device_id, DEVICE_IDS[device_id]

    async def _send(
        self,
        opcode: int,
        device_id: str,
        body: bytes,
        *,
        client_request_id: str | None = None,
        remember: dict | None = None,
        request_id: int | None = None,
    ) -> int:
        if self.sender is None or not self._gateway_connected:
            raise RuntimeError("Gateway не подключён")
        request_id = self._next_request_id() if request_id is None else request_id
        frame = build_control_frame(opcode, DEVICE_IDS[device_id], request_id, body)
        async with self._send_lock:
            await self.sender(frame)
        if request_id:
            context = {
                "client_request_id": client_request_id,
                "device_id": device_id,
                "requested_opcode": opcode,
                "sent_at": utc_now_iso(),
                **(remember or {}),
            }
            self._pending[request_id] = context
            if len(self._pending) > 512:
                self._pending.pop(next(iter(self._pending)))
        return request_id

    def _parameter(self, payload: dict) -> dict:
        name = payload.get("parameter")
        parameter = self.parameters_by_name.get(name)
        if not parameter:
            raise ValueError("Неизвестный параметр")
        return parameter

    @staticmethod
    def _encode_parameter(parameter: dict, value) -> bytes:
        kind = parameter["type"]
        encoded = bytearray(32)
        if kind == "bool":
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered not in {"0", "1", "false", "true", "нет", "да"}:
                    raise ValueError("bool: используйте 0/1 или true/false")
                value = lowered in {"1", "true", "да"}
            encoded[0] = int(bool(value))
        elif kind == "float":
            struct.pack_into("<f", encoded, 0, _as_finite_float(value, parameter["name"]))
        elif kind == "int":
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("int: требуется целое число") from exc
            if not -(2**31) <= number < 2**31:
                raise ValueError("int: значение вне диапазона int32")
            struct.pack_into("<i", encoded, 0, number)
        elif kind == "string":
            raw = str(value).encode("cp866")
            if len(raw) > 31:
                raise ValueError("Строка занимает больше 31 байта в CP866")
            encoded[: len(raw)] = raw
        else:
            raise ValueError(f"Неподдерживаемый тип параметра: {kind}")
        return bytes(encoded)

    @staticmethod
    def _decode_parameter(parameter: dict, value_hex: str):
        raw = bytes.fromhex(value_hex)
        kind = parameter["type"]
        if kind == "bool":
            return bool(raw[0])
        if kind == "float":
            value = struct.unpack_from("<f", raw)[0]
            return round(value, 7) if math.isfinite(value) else None
        if kind == "int":
            return struct.unpack_from("<i", raw)[0]
        if kind == "string":
            return raw.split(b"\0", 1)[0].decode("cp866", errors="replace")
        return value_hex

    async def handle_action(
        self, action: str, payload: dict, owner, client_request_id: str | None
    ) -> dict:
        device_id, _ = self._target(payload)

        if action == "execute_command":
            command = int(payload.get("command"))
            if command not in {item["value"] for item in COMMANDS}:
                raise ValueError("Неизвестная команда")
            request_id = await self._send(
                OP_COMMAND,
                device_id,
                struct.pack("<i", command),
                client_request_id=client_request_id,
                remember={"action": action, "command": command},
            )
        elif action == "request_message":
            message_id = int(payload.get("message_id"))
            if message_id not in {item["id"] for item in MESSAGES}:
                raise ValueError("Это сообщение нельзя запрашивать вручную")
            request_id = await self._send(
                OP_MESSAGE,
                device_id,
                struct.pack("<H", message_id),
                client_request_id=client_request_id,
                remember={"action": action, "message_id": message_id},
            )
        elif action in {"read_parameter", "write_parameter"}:
            parameter = self._parameter(payload)
            value = (
                bytes(32)
                if action == "read_parameter"
                else self._encode_parameter(parameter, payload.get("value"))
            )
            body = struct.pack(
                "<iI32s", int(action == "write_parameter"), parameter["id"], value
            )
            request_id = await self._send(
                OP_PARAMETER,
                device_id,
                body,
                client_request_id=client_request_id,
                remember={
                    "action": action,
                    "parameter": parameter["name"],
                    "parameter_id": parameter["id"],
                },
            )
            self._response_context[(device_id, "parameter", parameter["id"])] = {
                "gateway_request_id": request_id,
                "client_request_id": client_request_id,
            }
        elif action == "read_all_parameters":
            request_ids = []
            for parameter in self.parameters:
                body = struct.pack("<iI32s", 0, parameter["id"], bytes(32))
                current = await self._send(
                    OP_PARAMETER,
                    device_id,
                    body,
                    client_request_id=client_request_id,
                    remember={
                        "action": "read_parameter",
                        "parameter": parameter["name"],
                        "parameter_id": parameter["id"],
                    },
                )
                self._response_context[(device_id, "parameter", parameter["id"])] = {
                    "gateway_request_id": current,
                    "client_request_id": client_request_id,
                }
                request_ids.append(current)
                await asyncio.sleep(0.01)
            return {"action": action, "gateway_request_ids": request_ids}
        elif action in {"service_start", "service_update"}:
            if action == "service_start" and self._service_owner not in {None, owner}:
                raise RuntimeError("Сервисное управление занято другим браузером")
            state = self.shared_state["control_state"]["service"]
            if action == "service_start" and state.get("active"):
                raise RuntimeError("Сервисное управление уже запущено")
            if action == "service_update" and (
                not state.get("active")
                or state.get("device_id") != device_id
                or self._service_owner != owner
            ):
                raise RuntimeError("Сначала запустите управление этим устройством")
            mode = int(payload.get("mode", 1))
            if mode not in {item["value"] for item in SERVICE_MODES}:
                raise ValueError("Неизвестный режим управления")
            values = {
                key: _as_finite_float(payload.get(key, 0), key)
                for key in ("angle", "speed", "voltage", "current")
            }
            body = struct.pack(
                "<iffffI", mode, values["angle"], values["speed"],
                values["voltage"], values["current"], self.LEASE_MS
            )
            opcode = OP_SERVICE_START if action == "service_start" else OP_SERVICE_UPDATE
            request_id = await self._send(
                opcode,
                device_id,
                body,
                client_request_id=client_request_id,
                remember={"action": action},
            )
            self._service_owner = owner
            state.update(
                {
                    "active": True,
                    "device_id": device_id,
                    "mode": mode,
                    **values,
                    "updated_at": utc_now_iso(),
                }
            )
            self._ensure_keepalive()
            await self._broadcast("control_snapshot", self.snapshot())
        elif action == "service_stop":
            state = self.shared_state["control_state"]["service"]
            target = state.get("device_id")
            if not state.get("active"):
                raise RuntimeError("Сервисное управление не запущено")
            if target != device_id:
                raise RuntimeError("Остановите управление в карточке активного устройства")
            if self._service_owner not in {None, owner}:
                raise RuntimeError("Управление запущено в другом браузере")
            request_id = await self._send(
                OP_SERVICE_STOP,
                target,
                b"",
                client_request_id=client_request_id,
                remember={"action": action},
            )
            self._clear_service_state("manual_stop")
            await self._broadcast("control_snapshot", self.snapshot())
        else:
            raise ValueError(f"Неизвестное действие управления: {action}")

        return {"action": action, "gateway_request_id": request_id}

    def _ensure_keepalive(self) -> None:
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        while self.shared_state["control_state"]["service"].get("active"):
            await asyncio.sleep(self.KEEPALIVE_INTERVAL_S)
            state = self.shared_state["control_state"]["service"]
            device_id = state.get("device_id")
            if not state.get("active") or device_id not in DEVICE_IDS:
                break
            try:
                await self._send(
                    OP_SERVICE_KEEPALIVE, device_id, b"", request_id=0
                )
            except Exception:
                logger.exception("Не удалось продлить lease сервисного управления")
                self._clear_service_state("keepalive_failed")
                await self._broadcast("control_snapshot", self.snapshot())
                break

    def _clear_service_state(self, reason: str) -> None:
        state = self.shared_state["control_state"]["service"]
        state.clear()
        state.update(
            {
                "active": False,
                "device_id": None,
                "reason": reason,
                "updated_at": utc_now_iso(),
            }
        )
        self._service_owner = None

    async def release_owner(self, owner) -> None:
        state = self.shared_state["control_state"]["service"]
        if self._service_owner != owner or not state.get("active"):
            return
        target = state.get("device_id")
        try:
            await self._send(OP_SERVICE_STOP, target, b"", remember={"action": "owner_disconnect"})
        except Exception:
            logger.exception("Не удалось отправить STOP при закрытии браузера")
        self._clear_service_state("owner_disconnected")
        await self._broadcast("control_snapshot", self.snapshot())

    async def handle_gateway_message(self, message: dict) -> None:
        enriched = dict(message)
        kind = enriched.get("msg_type")
        device_id = enriched.get("device_id")

        if kind == "control_ack":
            request_id = enriched.get("gateway_request_id")
            context = self._pending.pop(request_id, {})
            target_device_id = context.get("device_id") or DEVICE_NAMES_BY_ID.get(
                enriched.get("dst_id")
            )
            enriched.update(
                {
                    key: value
                    for key, value in context.items()
                    if value is not None and key != "device_id"
                }
            )
            enriched["target_device_id"] = target_device_id
            counter = enriched.get("microjet_counter")
            if counter is not None and context.get("action") in {"execute_command", "request_message"}:
                response_kind = "command" if context["action"] == "execute_command" else "message"
                self._response_context[(context.get("device_id"), response_kind, counter)] = {
                    "gateway_request_id": request_id,
                    "client_request_id": context.get("client_request_id"),
                }
            if enriched.get("status_raw") in {3, 4}:
                self._clear_service_state(
                    "service_stopped"
                    if enriched.get("status_raw") == 3
                    else "lease_expired"
                )
            elif enriched.get("status_raw", 0) < 0 and context.get("action", "").startswith("service_"):
                self._clear_service_state("gateway_rejected")
        elif kind == "parameter_answer":
            parameter = self.parameters_by_id.get(enriched.get("parameter_id"))
            if parameter:
                enriched["parameter"] = parameter["name"]
                enriched["parameter_type"] = parameter["type"]
                enriched["unit"] = parameter["unit"]
                if enriched.get("status_raw") == 0:
                    enriched["value"] = self._decode_parameter(parameter, enriched["value_hex"])
                    values = self.shared_state["control_state"]["parameters"].setdefault(device_id, {})
                    values[parameter["name"]] = {
                        "value": enriched["value"],
                        "unit": parameter["unit"],
                        "received_at": enriched.get("timestamp"),
                    }
                enriched.update(
                    self._response_context.pop(
                        (device_id, "parameter", parameter["id"]), {}
                    )
                )
        elif kind in {"command_answer", "message_answer"}:
            response_kind = "command" if kind == "command_answer" else "message"
            enriched.update(
                self._response_context.pop(
                    (device_id, response_kind, enriched.get("request_counter")), {}
                )
            )

        enriched["received_at"] = utc_now_iso()
        events = self.shared_state["control_state"]["events"]
        events.append(enriched)
        await self._broadcast("control_response", enriched)
        if kind in {"parameter_answer", "control_ack"}:
            await self._broadcast("control_snapshot", self.snapshot())

    async def close(self) -> None:
        state = self.shared_state["control_state"]["service"]
        if state.get("active") and state.get("device_id") in DEVICE_IDS:
            try:
                await self._send(OP_SERVICE_STOP, state["device_id"], b"")
            except Exception:
                logger.exception("Не удалось отправить финальный STOP")
        self._clear_service_state("backend_stopped")
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
