"""Six persistent device cards with telemetry and safe MicroJet controls."""

import json
import time
from datetime import datetime, timezone

from dash import ALL, ctx, dcc, html, no_update
from dash.dependencies import Input, Output, State

DEVICE_ORDER = [
    "bup_dcu_lfd",
    "bup_dcu_rfd",
    "bup_dcu_lrd",
    "bup_dcu_rrd",
    "bup_dcu_lst",
    "bup_dcu_rst",
]

DEVICE_META = {
    "bup_dcu_lfd": ("МУП.ТЛП", "Лев. передний тяговый привод", "↖"),
    "bup_dcu_rfd": ("МУП.ТПП", "Прав. передний тяговый привод", "↗"),
    "bup_dcu_lrd": ("МУП.ТЛЗ", "Лев. задний тяговый привод", "↙"),
    "bup_dcu_rrd": ("МУП.ТПЗ", "Прав. задний тяговый привод", "↘"),
    "bup_dcu_lst": ("МУП.РЛ", "Лев. рулевой привод", "◀"),
    "bup_dcu_rst": ("МУП.РП", "Прав. рулевой привод", "▶"),
}

BRIEF_FIELDS = {
    "dcu_telemetry": [
        "status", "mode", "load_sens_angle", "load_sens_speed",
        "power_vdc", "stator_temp",
    ],
    "bup_state": [
        "status", "mode", "angle", "speed", "power_voltage", "stator_temp",
    ],
}

MESSAGE_LABELS = {
    "dcu_telemetry": "DCU telemetry",
    "bup_state": "BUP state",
    "command_answer": "Ответ на команду",
    "message_answer": "Ответ на запрос сообщения",
    "parameter_answer": "Ответ на параметр",
    "formular": "Электронный формуляр",
    "control_ack": "Gateway подтвердил отправку",
}

FIELD_LABELS = {
    "status": "Статус", "mode": "Режим", "error": "Ошибка",
    "angle": "Угол (рад)", "speed": "Скорость (рад/с)",
    "load_sens_angle": "Угол нагрузки (рад)",
    "load_sens_speed": "Скорость нагрузки (рад/с)",
    "rotor_sens_angle": "Угол ротора (рад)",
    "rotor_sens_speed": "Скорость ротора (рад/с)",
    "power_vdc": "Напряжение DC (В)",
    "power_voltage": "Напряжение питания (В)",
    "electrical_power": "Мощность (Вт)",
    "stator_temp": "Температура статора (°C)", "ibrk": "Ток тормоза (А)",
    "current_a": "Ток фазы A (А)", "current_b": "Ток фазы B (А)",
    "current_c": "Ток фазы C (А)", "brake_state": "Состояние тормоза",
    "limit": "Ограничение хода", "id": "Ток id (А)", "iq": "Ток iq (А)",
    "on": "Сигнал ON", "manual": "Сигнал MANUAL", "block": "Сигнал BLOCK",
    "sync": "Синхронизация", "inv_octw": "Перегрузка/перегрев",
    "inv_fault": "Отказ преобразователя", "en_gate": "Драйверы затворов",
    "timestamp": "Время устройства", "received_at": "Время приёма Backend",
}

SECTION_LABELS = {
    "state": "Состояние",
    "controls": "Команды и параметры",
    "service": "Управление 50 Гц",
}


def _normalise_node(device_id: str, value: dict) -> dict:
    if isinstance(value, dict) and isinstance(value.get("messages"), dict):
        return value
    if not isinstance(value, dict):
        return {"device_id": device_id, "messages": {}}
    message_type = value.get("msg_type")
    return {
        "device_id": device_id,
        "shortname": value.get("shortname"),
        "description": value.get("description"),
        "raw_id": value.get("raw_id"),
        "last_seen": value.get("received_at") or value.get("timestamp"),
        "latest_message_type": message_type,
        "messages": {message_type: value} if message_type else {},
    }


def _apply_device_message(devices_store: dict, payload: dict) -> bool:
    device_id = payload.get("device_id")
    message_type = payload.get("msg_type")
    if device_id not in DEVICE_ORDER or not message_type:
        return False
    node = dict(_normalise_node(device_id, devices_store.get(device_id, {})))
    messages = dict(node.get("messages", {}))
    messages[message_type] = payload
    node.update(
        {
            "device_id": device_id,
            "shortname": payload.get("shortname", node.get("shortname")),
            "description": payload.get("description", node.get("description")),
            "raw_id": payload.get("raw_id", node.get("raw_id")),
            "last_seen": payload.get("received_at") or payload.get("timestamp"),
            "latest_message_type": message_type,
            "messages": messages,
        }
    )
    devices_store[device_id] = node
    return True


def _latest_message(node: dict) -> dict | None:
    messages = node.get("messages", {})
    if not messages:
        return None
    latest_type = node.get("latest_message_type")
    if latest_type in messages:
        return messages[latest_type]
    return max(
        messages.values(),
        key=lambda item: item.get("received_at") or item.get("timestamp") or "",
    )


def _field_row(label: str, value, highlight: bool = False):
    value_text = f"{value:.4f}" if isinstance(value, float) else str(value)
    return html.Div(
        [
            html.Span(label, className="field-label"),
            html.Span(
                value_text,
                className="field-value field-value-highlight" if highlight else "field-value",
            ),
        ],
        className="field-row",
    )


def _message_details(message_type: str, data: dict) -> html.Div:
    skip = {
        "msg_type", "device_id", "shortname", "description", "raw_id",
        "dst_id", "ts_s", "ts_ns",
    }
    rows = []
    for key, value in data.items():
        if key in skip or key.endswith("_raw"):
            continue
        if isinstance(value, dict):
            rows.append(
                html.Div(
                    [
                        html.Div(FIELD_LABELS.get(key, key), className="message-group-title"),
                        *[
                            _field_row(FIELD_LABELS.get(child_key, child_key), child_value)
                            for child_key, child_value in value.items()
                        ],
                    ]
                )
            )
        else:
            rows.append(_field_row(FIELD_LABELS.get(key, key), value))
    return html.Div(
        [html.Div(MESSAGE_LABELS.get(message_type, message_type), className="message-title"), *rows]
    )


def _state_content(node: dict | None) -> html.Div:
    data = _latest_message(node) if node else None
    if not data or not node:
        return html.Div("Нет данных от устройства", className="muted-copy")
    message_type = data.get("msg_type", "unknown")
    brief_rows = [
        _field_row(FIELD_LABELS.get(key, key), data[key], key in {"status", "angle"})
        for key in BRIEF_FIELDS.get(message_type, [])
        if key in data
    ]
    received = data.get("received_at", "")
    return html.Div(
        [
            html.Div(
                f"Последнее сообщение: {MESSAGE_LABELS.get(message_type, message_type)}",
                className="device-meta",
            ),
            html.Div(brief_rows, className="field-list"),
            html.Div(
                f"Backend получил: {received[11:23] if len(received) >= 23 else received} UTC",
                className="device-received mt-2",
            ),
            *[
                _message_details(kind, message)
                for kind, message in sorted(node.get("messages", {}).items())
            ],
        ]
    )


def _parameter_panel(device_id: str) -> html.Div:
    return html.Div(
        [
            html.Div(
                id={"type": "control-catalog-hint", "index": device_id},
                children="Загружаю список параметров…",
                className="catalog-hint catalog-hint-loading",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Параметр", className="control-label"),
                            dcc.Dropdown(
                                id={"type": "parameter-select", "index": device_id},
                                options=[],
                                placeholder="Нажмите для списка или введите название",
                                searchable=True,
                                className="app-select",
                            ),
                        ],
                        className="control-field control-field-wide",
                    ),
                    html.Div(
                        [
                            html.Label("Новое значение", className="control-label"),
                            dcc.Input(
                                id={"type": "parameter-value", "index": device_id},
                                type="text", debounce=True,
                                className="form-control form-control-sm",
                                placeholder="bool / число / строка",
                            ),
                        ],
                        className="control-field",
                    ),
                ],
                className="device-control-grid",
            ),
            html.Div(
                [
                    html.Button(
                        "Прочитать", id={"type": "parameter-read", "index": device_id},
                        n_clicks=0, className="btn btn-sm btn-outline-primary",
                    ),
                    dcc.ConfirmDialogProvider(
                        id={"type": "parameter-write", "index": device_id},
                        message="Записать новое значение в выбранный параметр контроллера?",
                        children=html.Button(
                            "Записать", className="btn btn-sm btn-outline-warning"
                        ),
                    ),
                    html.Button(
                        "Прочитать все", id={"type": "parameter-read-all", "index": device_id},
                        n_clicks=0, className="btn btn-sm btn-outline-secondary",
                    ),
                    dcc.ConfirmDialogProvider(
                        id={"type": "parameter-save", "index": device_id},
                        message="Сохранить параметры во flash выбранного контроллера?",
                        children=html.Button(
                            "Сохранить во flash", className="btn btn-sm btn-outline-danger"
                        ),
                    ),
                ],
                className="device-control-actions",
            ),
            html.Div(
                id={"type": "parameter-current", "index": device_id},
                className="control-current-value",
            ),
            html.Div(
                id={"type": "parameter-table", "index": device_id},
                className="parameter-table",
            ),
        ],
        className="device-control-block",
    )


def _commands_panel(device_id: str) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Команда", className="control-label"),
                            dcc.Dropdown(
                                id={"type": "command-select", "index": device_id},
                                options=[], value=7, clearable=False, searchable=False,
                                className="app-select",
                            ),
                            html.Div(
                                id={"type": "command-description", "index": device_id},
                                className="control-selection-description",
                            ),
                            dcc.ConfirmDialogProvider(
                                id={"type": "command-send", "index": device_id},
                                message="Отправить выбранную команду этому контроллеру?",
                                children=html.Button(
                                    "Отправить команду один раз",
                                    id={"type": "command-send-button", "index": device_id},
                                    disabled=True,
                                    className="btn btn-sm btn-outline-danger mt-2",
                                ),
                            ),
                        ],
                        className="control-field",
                    ),
                    html.Div(
                        [
                            html.Label("Что запросить у устройства", className="control-label"),
                            dcc.RadioItems(
                                id={"type": "message-select", "index": device_id},
                                options=[], value=0x10AD,
                                className="message-choice-list",
                            ),
                            html.Button(
                                "Запросить", id={"type": "message-send", "index": device_id},
                                n_clicks=0, disabled=True,
                                className="btn btn-sm btn-outline-primary mt-2",
                            ),
                        ],
                        className="control-field",
                    ),
                ],
                className="device-control-grid",
            ),
            html.Div(
                "Команда отправляется один раз. Запрос сообщения тоже отправляется один раз и ожидает ответ устройства.",
                className="control-help",
            ),
        ],
        className="device-control-block",
    )


def _controls_panel(device_id: str) -> html.Div:
    return html.Div(
        [
            html.H3("Разовые команды", className="device-control-heading"),
            html.Div(
                "Здесь находятся 11 команд cmd_req и два запроса msg_req. Все варианты видны в списках ниже.",
                className="control-explanation",
            ),
            _commands_panel(device_id),
            html.Div(className="control-divider"),
            html.H3("Параметры контроллера", className="device-control-heading"),
            html.Div(
                "Параметры — это 71 настройка prm_req, а не три дополнительные команды. Их можно прочитать, изменить и сохранить во flash.",
                className="control-explanation",
            ),
            _parameter_panel(device_id),
        ],
        id={"type": "device-section-panel", "index": device_id, "section": "controls"},
        style={"display": "none"},
        className="device-control-panel",
    )


def _service_panel(device_id: str) -> html.Div:
    numeric_fields = [
        ("angle", "Угол, рад"), ("speed", "Скорость, рад/с"),
        ("voltage", "Напряжение, В"), ("current", "Ток, А"),
    ]
    return html.Div(
        [
            html.Div(
                "Этот раздел запускает непрерывный поток bup_srv_ctrl: Gateway отправляет выбранное задание каждые 20 мс.",
                className="control-explanation",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Режим", className="control-label"),
                            dcc.Dropdown(
                                id={"type": "service-mode", "index": device_id},
                                options=[], value=1, clearable=False, searchable=False,
                                className="app-select",
                            ),
                            html.Div(
                                id={"type": "service-mode-description", "index": device_id},
                                className="control-selection-description",
                            ),
                        ],
                        className="control-field",
                    ),
                    *[
                        html.Div(
                            [
                                html.Label(label, className="control-label"),
                                dcc.Input(
                                    id={"type": f"service-{name}", "index": device_id},
                                    type="number", value=0, step="any",
                                    disabled=True,
                                    className="form-control form-control-sm",
                                ),
                            ],
                            className="control-field control-field-number",
                        )
                        for name, label in numeric_fields
                    ],
                ],
                className="service-control-grid",
            ),
            html.Div(
                [
                    dcc.ConfirmDialogProvider(
                        id={"type": "service-start", "index": device_id},
                        message=(
                            "Запустить периодическую передачу сервисного управления "
                            "на выбранный контроллер?"
                        ),
                        children=html.Button(
                            "Запустить поток 50 Гц",
                            id={"type": "service-start-button", "index": device_id},
                            disabled=True,
                            className="btn btn-sm btn-danger",
                        ),
                    ),
                    html.Button(
                        "Обновить активный поток", id={"type": "service-update", "index": device_id},
                        n_clicks=0, disabled=True,
                        className="btn btn-sm btn-outline-warning",
                    ),
                    html.Button(
                        "Остановить поток (OFF)", id={"type": "service-stop", "index": device_id},
                        n_clicks=0, disabled=True,
                        className="btn btn-sm btn-outline-secondary",
                    ),
                ],
                className="device-control-actions",
            ),
            html.Div(
                id={"type": "service-status", "index": device_id},
                className="service-status",
            ),
            html.Div(
                "«Обновить активный поток» меняет режим или задание без остановки. При потере Backend lease истечёт, и Gateway отправит OFF автоматически.",
                className="control-help",
            ),
        ],
        id={"type": "device-section-panel", "index": device_id, "section": "service"},
        style={"display": "none"},
        className="device-control-panel",
    )


def _static_card(device_id: str) -> html.Div:
    short, description, icon = DEVICE_META[device_id]
    header = html.Div(
        [
            html.Div(
                [
                    html.Span(icon, className="device-symbol"),
                    html.Div(
                        [html.Div(short, className="device-name"), html.Div(description, className="device-description")]
                    ),
                ],
                className="device-identity",
            ),
            html.Div(
                [
                    html.Span(
                        "OFFLINE", id={"type": "device-status", "index": device_id},
                        className="status-badge status-badge-offline",
                    ),
                    html.Span(
                        "+", id={"type": "device-chevron", "index": device_id},
                        className="device-chevron",
                    ),
                ],
                className="device-state",
            ),
        ],
        id={"type": "device-header", "index": device_id},
        n_clicks=0,
        className="device-card-header",
    )
    section_buttons = html.Div(
        [
            html.Button(
                label,
                id={"type": "device-section-button", "index": device_id, "section": section},
                n_clicks=0,
                className="device-section-button active" if section == "state" else "device-section-button",
            )
            for section, label in SECTION_LABELS.items()
        ],
        className="device-section-nav",
    )
    body = html.Div(
        [
            section_buttons,
            html.Div(
                id={"type": "device-section-panel", "index": device_id, "section": "state"},
                children=html.Div("Нет данных от устройства", className="muted-copy"),
                className="device-control-panel",
            ),
            _controls_panel(device_id),
            _service_panel(device_id),
            html.Div("Последние ответы", className="control-log-title"),
            html.Div(
                id={"type": "control-events", "index": device_id},
                className="control-event-list",
            ),
        ],
        id={"type": "device-body", "index": device_id},
        style={"display": "block" if device_id == DEVICE_ORDER[0] else "none"},
        className="device-card-body",
    )
    return html.Div(
        [header, body],
        id={"type": "device-card", "index": device_id},
        className="device-card status-border-offline",
    )


devices_layout = html.Div(
    [
        dcc.Store(id="devices-store", data={}, storage_type="memory"),
        dcc.Store(id="system-status-store", data={}, storage_type="memory"),
        dcc.Store(id="control-catalog-store", data={}, storage_type="memory"),
        dcc.Store(id="control-state-store", data={}, storage_type="memory"),
        dcc.Store(id="control-events-store", data=[], storage_type="memory"),
        dcc.Store(id="devices-expanded", data={DEVICE_ORDER[0]: True}, storage_type="memory"),
        dcc.Store(
            id="device-sections", data={device_id: "state" for device_id in DEVICE_ORDER},
            storage_type="memory",
        ),
        dcc.Interval(id="devices-online-tick", interval=1000, n_intervals=0),
        dcc.Interval(id="control-catalog-bootstrap", interval=500, n_intervals=0),
        html.Div(
            [
                html.Div(
                    [
                        html.H1("Устройства", className="page-title"),
                        html.Div(
                            "Телеметрия и управление шестью контроллерами МУП",
                            className="page-description",
                        ),
                    ]
                ),
                html.Div(id="devices-connection-status", className="connection-status"),
            ],
            className="page-heading",
        ),
        html.Div([_static_card(device_id) for device_id in DEVICE_ORDER], className="device-list"),
    ],
    className="page-section",
)


def _event_device(payload: dict, envelope: dict) -> str | None:
    device_id = payload.get("target_device_id") or payload.get("device_id")
    if device_id in DEVICE_ORDER:
        return device_id
    request_id = str(envelope.get("request_id") or "")
    for candidate in DEVICE_ORDER:
        if f":{candidate}:" in request_id:
            return candidate
    return None


def _event_view(event: dict):
    kind = event.get("msg_type") or event.get("event_type", "Ответ")
    stamp = event.get("received_at") or event.get("timestamp") or ""
    clock = stamp[11:19] if len(stamp) >= 19 else "--:--:--"
    if kind == "parameter_answer":
        detail = f"{event.get('parameter', hex(event.get('parameter_id', 0)))}: {event.get('value', event.get('status'))}"
    elif kind == "command_answer":
        detail = f"{event.get('command')}: {event.get('state')} {event.get('info', '')}".strip()
    elif kind == "message_answer":
        detail = f"message 0x{event.get('message_id', 0):04X}: {event.get('state')}"
    elif kind == "control_ack":
        detail = f"{event.get('action', event.get('opcode'))}: {event.get('status')}"
    elif kind == "formular":
        detail = f"Формуляр: {event.get('product_description') or event.get('factory_number') or 'получен'}"
    elif kind == "request_error":
        detail = event.get("message", "Ошибка запроса")
    elif kind == "request_ok":
        detail = f"Запрос принят: {event.get('action', '')}"
    else:
        detail = MESSAGE_LABELS.get(kind, kind)
    error = kind == "request_error" or event.get("status_raw", 0) < 0 or event.get("state_raw", 0) < 0
    return html.Div(
        [html.Span(clock, className="control-event-time"), html.Span(detail)],
        className="control-event control-event-error" if error else "control-event",
    )


def register_devices_callbacks(app):
    @app.callback(
        Output("devices-store", "data"),
        Output("system-status-store", "data"),
        Output("control-catalog-store", "data"),
        Output("control-state-store", "data"),
        Output("control-events-store", "data"),
        Input("devices-ws-event", "data"),
        State("devices-store", "data"),
        State("system-status-store", "data"),
        State("control-catalog-store", "data"),
        State("control-state-store", "data"),
        State("control-events-store", "data"),
        prevent_initial_call=True,
    )
    def update_stores(message, devices, system, catalog, control_state, events):
        if not message:
            return (no_update,) * 5
        try:
            envelope = json.loads(message["data"])
            event_type = envelope.get("type")
            payload = envelope.get("payload", {})
            if event_type == "devices_snapshot":
                return (
                    {
                        device_id: _normalise_node(device_id, value)
                        for device_id, value in payload.items()
                        if device_id in DEVICE_ORDER
                    },
                    no_update, no_update, no_update, no_update,
                )
            if event_type in {"devices_batch", "device_telemetry"}:
                devices = dict(devices or {})
                messages = payload.get("devices", []) if event_type == "devices_batch" else [payload]
                changed = False
                for item in messages:
                    if isinstance(item, dict):
                        changed |= _apply_device_message(devices, item)
                return (devices if changed else no_update, no_update, no_update, no_update, no_update)
            if event_type == "system_status":
                return no_update, payload, no_update, no_update, no_update
            if event_type == "control_catalog":
                return no_update, no_update, payload, no_update, no_update
            if event_type == "control_snapshot":
                snapshot_events = payload.get("events", [])
                return no_update, no_update, no_update, payload, snapshot_events[-100:]
            if event_type == "control_context":
                context_catalog = payload.get("catalog", {})
                context_state = payload.get("snapshot", {})
                context_events = context_state.get("events", [])
                return (
                    no_update,
                    no_update,
                    context_catalog,
                    context_state,
                    context_events[-100:],
                )
            if event_type in {"control_response", "request_error", "request_ok"}:
                device_id = _event_device(payload, envelope)
                if not device_id:
                    return (no_update,) * 5
                event = dict(payload)
                event["device_id"] = device_id
                event["event_type"] = event_type
                event.setdefault("received_at", datetime.now(timezone.utc).isoformat())
                updated = list(events or [])
                updated.append(event)
                return no_update, no_update, no_update, no_update, updated[-100:]
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError, ValueError):
            return (no_update,) * 5
        return (no_update,) * 5

    @app.callback(
        Output("ws", "send", allow_duplicate=True),
        Output("control-catalog-bootstrap", "interval"),
        Input("control-catalog-bootstrap", "n_intervals"),
        State("control-catalog-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_control_context(_tick, catalog):
        catalog = catalog or {}
        catalog_ready = all(
            catalog.get(key)
            for key in ("parameters", "commands", "messages", "service_modes")
        )
        action = "get_control_snapshot" if catalog_ready else "get_control_context"
        # Retry the initial atomic catalog every 500 ms.  Once it is present,
        # stretch this same timer to five seconds; control actions broadcast
        # their own snapshot immediately and do not wait for reconciliation.
        interval_ms = 5_000 if catalog_ready else 500
        return (
            json.dumps(
                {
                    "action": action,
                    "payload": {},
                    "request_id": f"control-refresh:{int(time.time() * 1000)}",
                }
            ),
            interval_ms,
        )

    @app.callback(
        Output("devices-expanded", "data"),
        Input({"type": "device-header", "index": ALL}, "n_clicks"),
        State("devices-expanded", "data"),
        prevent_initial_call=True,
    )
    def toggle_expand(_clicks, expanded):
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict) or not ctx.triggered[0].get("value"):
            return no_update
        device_id = triggered["index"]
        expanded = dict(expanded or {})
        expanded[device_id] = not expanded.get(device_id, False)
        return expanded

    @app.callback(
        Output({"type": "device-body", "index": ALL}, "style"),
        Output({"type": "device-chevron", "index": ALL}, "children"),
        Input("devices-expanded", "data"),
    )
    def show_expanded(expanded):
        expanded = expanded or {}
        return (
            [{"display": "block" if expanded.get(device_id) else "none"} for device_id in DEVICE_ORDER],
            ["−" if expanded.get(device_id) else "+" for device_id in DEVICE_ORDER],
        )

    @app.callback(
        Output("device-sections", "data"),
        Input({"type": "device-section-button", "index": ALL, "section": ALL}, "n_clicks"),
        State("device-sections", "data"),
        prevent_initial_call=True,
    )
    def choose_section(_clicks, selected):
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict) or not ctx.triggered[0].get("value"):
            return no_update
        selected = dict(selected or {})
        selected[triggered["index"]] = triggered["section"]
        return selected

    @app.callback(
        Output({"type": "device-section-panel", "index": ALL, "section": ALL}, "style"),
        Output({"type": "device-section-button", "index": ALL, "section": ALL}, "className"),
        Input("device-sections", "data"),
        State({"type": "device-section-panel", "index": ALL, "section": ALL}, "id"),
        State({"type": "device-section-button", "index": ALL, "section": ALL}, "id"),
    )
    def render_sections(selected, panel_ids, button_ids):
        selected = selected or {}
        panel_styles = [
            {"display": "block" if selected.get(item["index"], "state") == item["section"] else "none"}
            for item in panel_ids
        ]
        button_classes = [
            "device-section-button active"
            if selected.get(item["index"], "state") == item["section"]
            else "device-section-button"
            for item in button_ids
        ]
        return panel_styles, button_classes

    app.clientside_callback(
        """
        function (message, _tick) {
            const order = [
                "bup_dcu_lfd", "bup_dcu_rfd", "bup_dcu_lrd",
                "bup_dcu_rrd", "bup_dcu_lst", "bup_dcu_rst",
            ];
            const state = window.__bro30DeviceLiveState || {
                lastSeen: {},
                gateway: {},
            };
            window.__bro30DeviceLiveState = state;

            if (message && typeof message.data === "string") {
                try {
                    const event = JSON.parse(message.data);
                    const payload = event.payload || {};
                    if (event.type === "system_status") {
                        state.gateway = payload.gateway || {};
                    } else if (event.type === "devices_snapshot") {
                        order.forEach(function (deviceId) {
                            const node = payload[deviceId] || {};
                            const parsed = Date.parse(node.last_seen || "");
                            if (Number.isFinite(parsed)) {
                                state.lastSeen[deviceId] = parsed;
                            }
                        });
                    } else if (event.type === "devices_batch") {
                        (payload.devices || []).forEach(function (item) {
                            if (item && order.includes(item.device_id)) {
                                state.lastSeen[item.device_id] = Date.now();
                            }
                        });
                    } else if (
                        event.type === "device_telemetry"
                        && order.includes(payload.device_id)
                    ) {
                        state.lastSeen[payload.device_id] = Date.now();
                    }
                } catch (_error) {
                    // Ignore a malformed event and retain the last valid state.
                }
            }

            const now = Date.now();
            const statuses = order.map(function (deviceId) {
                return now - Number(state.lastSeen[deviceId] || 0) <= 2500
                    ? "ONLINE" : "OFFLINE";
            });
            const onlineCount = statuses.filter(function (value) {
                return value === "ONLINE";
            }).length;
            const badgeClasses = statuses.map(function (status) {
                return "status-badge status-badge-" + status.toLowerCase();
            });
            const cardClasses = statuses.map(function (status) {
                return "device-card status-border-" + status.toLowerCase();
            });

            const gateway = state.gateway || {};
            if (gateway.connected) {
                return [
                    statuses,
                    badgeClasses,
                    cardClasses,
                    "Gateway подключён · устройства " + onlineCount + "/" + order.length
                        + " · пакеты " + Number(gateway.packets_decoded || 0)
                        + " · команды " + Number(gateway.control_frames_sent || 0),
                    "connection-status connection-status-online",
                ];
            }
            return [
                statuses,
                badgeClasses,
                cardClasses,
                "Gateway не подключён",
                "connection-status connection-status-offline",
            ];
        }
        """,
        Output({"type": "device-status", "index": ALL}, "children"),
        Output({"type": "device-status", "index": ALL}, "className"),
        Output({"type": "device-card", "index": ALL}, "className"),
        Output("devices-connection-status", "children"),
        Output("devices-connection-status", "className"),
        Input("devices-live-event", "data"),
        Input("devices-online-tick", "n_intervals"),
    )

    @app.callback(
        Output({"type": "device-section-panel", "index": ALL, "section": "state"}, "children"),
        Input("devices-store", "data"),
    )
    def render_device_contents(store):
        store = store or {}
        contents = []
        for device_id in DEVICE_ORDER:
            node = store.get(device_id)
            contents.append(_state_content(node))
        return contents

    @app.callback(
        Output({"type": "parameter-select", "index": ALL}, "options"),
        Output({"type": "command-select", "index": ALL}, "options"),
        Output({"type": "message-select", "index": ALL}, "options"),
        Output({"type": "service-mode", "index": ALL}, "options"),
        Output({"type": "command-select", "index": ALL}, "value"),
        Output({"type": "message-select", "index": ALL}, "value"),
        Output({"type": "service-mode", "index": ALL}, "value"),
        Input("control-catalog-store", "data"),
        State({"type": "command-select", "index": ALL}, "value"),
        State({"type": "message-select", "index": ALL}, "value"),
        State({"type": "service-mode", "index": ALL}, "value"),
    )
    def catalog_options(catalog, selected_commands, selected_messages, selected_modes):
        catalog = catalog or {}
        parameters = [
            {
                "label": f"{item['name']} · {item['type']} · {item.get('unit', '-')}",
                "value": item["name"],
            }
            for item in catalog.get("parameters", [])
        ]
        commands = [
            {"label": f"{item['name']} — {item['label']}", "value": item["value"]}
            for item in catalog.get("commands", [])
        ]
        messages = [
            {"label": f"{item['label']} ({item['name']})", "value": item["id"]}
            for item in catalog.get("messages", [])
        ]
        modes = [
            {"label": item.get("label", item["name"]), "value": item["value"]}
            for item in catalog.get("service_modes", [])
        ]

        command_values = {item["value"] for item in catalog.get("commands", [])}
        message_values = {item["id"] for item in catalog.get("messages", [])}
        mode_values = {item["value"] for item in catalog.get("service_modes", [])}

        def keep_or_default(selected, valid, preferred):
            selected = selected or []
            fallback = preferred if preferred in valid else next(iter(valid), None)
            return [
                selected[index]
                if index < len(selected) and selected[index] in valid
                else fallback
                for index in range(len(DEVICE_ORDER))
            ]

        count = len(DEVICE_ORDER)
        return (
            [parameters] * count,
            [commands] * count,
            [messages] * count,
            [modes] * count,
            keep_or_default(selected_commands, command_values, 7),
            keep_or_default(selected_messages, message_values, 0x10AD),
            keep_or_default(selected_modes, mode_values, 1),
        )

    @app.callback(
        Output({"type": "control-catalog-hint", "index": ALL}, "children"),
        Output({"type": "control-catalog-hint", "index": ALL}, "className"),
        Output({"type": "command-description", "index": ALL}, "children"),
        Output({"type": "service-mode-description", "index": ALL}, "children"),
        Output({"type": "command-send-button", "index": ALL}, "disabled"),
        Output({"type": "message-send", "index": ALL}, "disabled"),
        Input("control-catalog-store", "data"),
        Input({"type": "command-select", "index": ALL}, "value"),
        Input({"type": "service-mode", "index": ALL}, "value"),
    )
    def explain_catalog(catalog, selected_commands, selected_modes):
        catalog = catalog or {}
        parameters = catalog.get("parameters", [])
        command_map = {item["value"]: item for item in catalog.get("commands", [])}
        mode_map = {item["value"]: item for item in catalog.get("service_modes", [])}
        message_ready = bool(catalog.get("messages"))
        command_ready = bool(command_map)
        if parameters:
            hint = f"Каталог загружен: {len(parameters)} параметр(ов). Откройте список или начните вводить название."
            hint_class = "catalog-hint catalog-hint-ready"
        else:
            hint = "Загружаю каталог параметров из Backend…"
            hint_class = "catalog-hint catalog-hint-loading"

        command_descriptions = []
        mode_descriptions = []
        for index in range(len(DEVICE_ORDER)):
            command_value = selected_commands[index] if index < len(selected_commands) else None
            command = command_map.get(command_value)
            if command:
                risk = " Требуется подтверждение: команда изменяет состояние устройства." if command.get("dangerous") else ""
                command_descriptions.append(
                    f"Код {command['value']}: {command['label']}.{risk}"
                )
            else:
                command_descriptions.append("Каталог команд загружается…")

            mode_value = selected_modes[index] if index < len(selected_modes) else None
            mode = mode_map.get(mode_value)
            mode_descriptions.append(
                mode.get("description", "Выберите режим потока 50 Гц.")
                if mode else "Каталог режимов загружается…"
            )

        count = len(DEVICE_ORDER)
        return (
            [hint] * count,
            [hint_class] * count,
            command_descriptions,
            mode_descriptions,
            [not command_ready] * count,
            [not message_ready] * count,
        )

    @app.callback(
        Output({"type": "service-start-button", "index": ALL}, "disabled"),
        Output({"type": "service-update", "index": ALL}, "disabled"),
        Output({"type": "service-stop", "index": ALL}, "disabled"),
        Output({"type": "service-angle", "index": ALL}, "disabled"),
        Output({"type": "service-speed", "index": ALL}, "disabled"),
        Output({"type": "service-voltage", "index": ALL}, "disabled"),
        Output({"type": "service-current", "index": ALL}, "disabled"),
        Input("control-state-store", "data"),
        Input("control-catalog-store", "data"),
        Input({"type": "service-mode", "index": ALL}, "value"),
    )
    def service_controls_state(control_state, catalog, selected_modes):
        state = control_state or {}
        service = state.get("service", {})
        active = bool(service.get("active"))
        active_device = service.get("device_id")
        gateway_connected = bool(state.get("gateway_connected"))
        mode_map = {
            item["value"]: set(item.get("fields", []))
            for item in (catalog or {}).get("service_modes", [])
        }

        start_disabled, update_disabled, stop_disabled = [], [], []
        angle_disabled, speed_disabled = [], []
        voltage_disabled, current_disabled = [], []
        for index, device_id in enumerate(DEVICE_ORDER):
            owns_stream = active and active_device == device_id
            start_disabled.append(not gateway_connected or active)
            update_disabled.append(not gateway_connected or not owns_stream)
            stop_disabled.append(not gateway_connected or not owns_stream)
            mode = selected_modes[index] if index < len(selected_modes) else None
            enabled_fields = mode_map.get(mode, set())
            angle_disabled.append("angle" not in enabled_fields)
            speed_disabled.append("speed" not in enabled_fields)
            voltage_disabled.append("voltage" not in enabled_fields)
            current_disabled.append("current" not in enabled_fields)

        return (
            start_disabled,
            update_disabled,
            stop_disabled,
            angle_disabled,
            speed_disabled,
            voltage_disabled,
            current_disabled,
        )

    @app.callback(
        Output({"type": "parameter-current", "index": ALL}, "children"),
        Output({"type": "parameter-table", "index": ALL}, "children"),
        Output({"type": "service-status", "index": ALL}, "children"),
        Output({"type": "service-status", "index": ALL}, "className"),
        Input("control-state-store", "data"),
        Input({"type": "parameter-select", "index": ALL}, "value"),
    )
    def render_control_state(control_state, selected_parameters):
        state = control_state or {}
        values_by_device = state.get("parameters", {})
        service = state.get("service", {})
        current_values, tables, service_texts, service_classes = [], [], [], []
        for index, device_id in enumerate(DEVICE_ORDER):
            values = values_by_device.get(device_id, {})
            selected = selected_parameters[index] if index < len(selected_parameters) else None
            current = values.get(selected) if selected else None
            if current:
                current_values.append(
                    f"Текущее значение: {current.get('value')} {current.get('unit', '')}".strip()
                )
            else:
                current_values.append("Значение ещё не прочитано")
            rows = [
                html.Div(
                    [
                        html.Span(name, className="parameter-name"),
                        html.Span(
                            f"{entry.get('value')} {entry.get('unit', '')}".strip(),
                            className="mono-value",
                        ),
                    ],
                    className="parameter-row",
                )
                for name, entry in sorted(values.items())
            ]
            tables.append(rows or html.Div("Прочитанных параметров пока нет", className="muted-copy"))
            if service.get("active") and service.get("device_id") == device_id:
                service_texts.append(
                    f"Идёт управление · режим {service.get('mode')} · lease активен"
                )
                service_classes.append("service-status service-status-active")
            elif service.get("active"):
                short = DEVICE_META.get(service.get("device_id"), (service.get("device_id"),))[0]
                service_texts.append(f"Управление занято устройством {short}")
                service_classes.append("service-status service-status-busy")
            else:
                service_texts.append("Сервисное управление остановлено")
                service_classes.append("service-status")
        return current_values, tables, service_texts, service_classes

    @app.callback(
        Output({"type": "control-events", "index": ALL}, "children"),
        Input("control-events-store", "data"),
    )
    def render_events(events):
        events = events or []
        return [
            [
                _event_view(event)
                for event in events
                if event.get("device_id") == device_id
            ][-12:]
            or [html.Div("Ответов пока нет", className="muted-copy")]
            for device_id in DEVICE_ORDER
        ]

    @app.callback(
        Output("ws", "send", allow_duplicate=True),
        Input({"type": "parameter-read", "index": ALL}, "n_clicks"),
        Input({"type": "parameter-write", "index": ALL}, "submit_n_clicks"),
        Input({"type": "parameter-read-all", "index": ALL}, "n_clicks"),
        Input({"type": "parameter-save", "index": ALL}, "submit_n_clicks"),
        Input({"type": "command-send", "index": ALL}, "submit_n_clicks"),
        Input({"type": "message-send", "index": ALL}, "n_clicks"),
        Input({"type": "service-start", "index": ALL}, "submit_n_clicks"),
        Input({"type": "service-update", "index": ALL}, "n_clicks"),
        Input({"type": "service-stop", "index": ALL}, "n_clicks"),
        State({"type": "parameter-select", "index": ALL}, "value"),
        State({"type": "parameter-value", "index": ALL}, "value"),
        State({"type": "command-select", "index": ALL}, "value"),
        State({"type": "message-select", "index": ALL}, "value"),
        State({"type": "service-mode", "index": ALL}, "value"),
        State({"type": "service-angle", "index": ALL}, "value"),
        State({"type": "service-speed", "index": ALL}, "value"),
        State({"type": "service-voltage", "index": ALL}, "value"),
        State({"type": "service-current", "index": ALL}, "value"),
        prevent_initial_call=True,
    )
    def dispatch_control(
        _read, _write, _read_all, _save, _command, _message,
        _start, _update, _stop,
        parameters, parameter_values, commands, messages, modes,
        angles, speeds, voltages, currents,
    ):
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict) or not ctx.triggered[0].get("value"):
            return no_update
        device_id = triggered["index"]
        index = DEVICE_ORDER.index(device_id)
        trigger_type = triggered["type"]
        payload = {"device_id": device_id}

        if trigger_type == "parameter-read":
            if not parameters[index]:
                return no_update
            action = "read_parameter"
            payload["parameter"] = parameters[index]
        elif trigger_type == "parameter-write":
            if not parameters[index] or parameter_values[index] is None:
                return no_update
            action = "write_parameter"
            payload.update({"parameter": parameters[index], "value": parameter_values[index]})
        elif trigger_type == "parameter-read-all":
            action = "read_all_parameters"
        elif trigger_type == "parameter-save":
            action = "execute_command"
            payload["command"] = 1
        elif trigger_type == "command-send":
            action = "execute_command"
            payload["command"] = commands[index]
        elif trigger_type == "message-send":
            action = "request_message"
            payload["message_id"] = messages[index]
        elif trigger_type in {"service-start", "service-update"}:
            action = "service_start" if trigger_type == "service-start" else "service_update"
            payload.update(
                {
                    "mode": modes[index],
                    "angle": angles[index] or 0,
                    "speed": speeds[index] or 0,
                    "voltage": voltages[index] or 0,
                    "current": currents[index] or 0,
                }
            )
        elif trigger_type == "service-stop":
            action = "service_stop"
        else:
            return no_update

        request_id = f"control:{device_id}:{int(time.time() * 1000)}"
        return json.dumps(
            {"action": action, "payload": payload, "request_id": request_id},
            ensure_ascii=False,
        )
