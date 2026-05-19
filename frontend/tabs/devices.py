"""
tabs/devices.py — Живые карточки устройств BRO30.

Данные приходят через WebSocket:
  - devices_snapshot: {device_id: {...}} — снимок при подключении
  - device_telemetry: {...}              — обновления в реальном времени
"""

import json
from dash import html, dcc, callback_context as ctx, no_update, ALL, MATCH
from dash.dependencies import Input, Output, State


# ─── Конфигурация устройств  ─────────────────────────────────

DEVICE_ORDER = [
    "dcu",
    "bup_dcu_lfd", "bup_dcu_rfd",
    "bup_dcu_lrd", "bup_dcu_rrd",
    "bup_dcu_lst", "bup_dcu_rst",
]

DEVICE_META = {
    "dcu":         ("МУП",     "Модуль управления приводом",            "🔧"),
    "bup_dcu_lfd": ("МУП.ТЛП", "Лев. передний тяговый привод",          "↖"),
    "bup_dcu_rfd": ("МУП.ТПП", "Прав. передний тяговый привод",         "↗"),
    "bup_dcu_lrd": ("МУП.ТЛЗ", "Лев. задний тяговый привод",            "↙"),
    "bup_dcu_rrd": ("МУП.ТПЗ", "Прав. задний тяговый привод",           "↘"),
    "bup_dcu_lst": ("МУП.РЛ",  "Лев. рулевой привод",                   "◀"),
    "bup_dcu_rst": ("МУП.РП",  "Прав. рулевой привод",                  "▶"),
}

# Ключевые поля для краткого отображения
BRIEF_FIELDS = {
    "dcu":  ["status", "mode", "load_sens_angle", "load_sens_speed",
             "power_vdc", "stator_temp"],
    "bup":  ["status", "mode", "angle", "speed", "power_voltage", "stator_temp"],
}

FIELD_LABELS = {
    "status":           "Статус",
    "mode":             "Режим",
    "error":            "Ошибка",
    "angle":            "Угол (рад)",
    "speed":            "Скорость (рад/с)",
    "load_sens_angle":  "Угол нагрузки (рад)",
    "load_sens_speed":  "Скорость нагрузки (рад/с)",
    "rotor_sens_angle": "Угол ротора (рад)",
    "rotor_sens_speed": "Скорость ротора (рад/с)",
    "power_vdc":        "Напряжение DC (В)",
    "power_voltage":    "Напряжение питания (В)",
    "electrical_power": "Мощность (Вт)",
    "stator_temp":      "Температура статора (°C)",
    "ibrk":             "Ток тормоза (А)",
    "current_a":        "Ток фазы A (А)",
    "current_b":        "Ток фазы B (А)",
    "current_c":        "Ток фазы C (А)",
    "brake_state":      "Состояние тормоза",
    "limit":            "Ограничение хода",
    "id":               "Ток id (А)",
    "iq":               "Ток iq (А)",
    "on":               "Сигнал ON",
    "sync":             "Синхронизация",
    "timestamp":        "Время",
}

STATUS_COLORS = {
    "ARM":      "#10b981",  # зелёный
    "DISARM":   "#f59e0b",  # жёлтый
    "PREPARE":  "#3b82f6",  # синий
    "FAULT":    "#ef4444",  # красный
    "OFF":      "#6b7280",  # серый
    "UNKNOWN":  "#6b7280",
}


# ─── Вспомогательные компоненты ──────────────────────────────

def _status_badge(status_str: str):
    color = STATUS_COLORS.get(status_str, "#6b7280")
    return html.Span(
        status_str,
        style={
            "background":   color,
            "color":        "#fff",
            "borderRadius": "6px",
            "padding":      "2px 10px",
            "fontSize":     "12px",
            "fontWeight":   "600",
            "letterSpacing": "0.5px",
        },
    )


def _field_row(label: str, value, highlight: bool = False):
    val_str = f"{value:.4f}" if isinstance(value, float) else str(value)
    return html.Div(
        [
            html.Span(label, style={"color": "#94a3b8", "minWidth": "190px",
                                     "display": "inline-block", "fontSize": "13px"}),
            html.Span(val_str, style={
                "color":      "#10b981" if highlight else "#e2e8f0",
                "fontWeight": "600" if highlight else "400",
                "fontFamily": "monospace",
                "fontSize":   "13px",
            }),
        ],
        style={"padding": "3px 0", "display": "flex", "alignItems": "center"},
    )


def _make_card(device_id: str, data: dict | None, expanded: bool) -> html.Div:
    short, desc, icon = DEVICE_META.get(
        device_id, (device_id.upper(), device_id, "📡")
    )

    is_dcu   = device_id == "dcu"
    online   = data is not None
    status   = data.get("status", "UNKNOWN") if data else "OFFLINE"
    badge_color = STATUS_COLORS.get(status, "#6b7280") if online else "#374151"

    # ── Заголовок карточки
    header = html.Div(
        [
            html.Div(
                [
                    html.Span(icon, style={"fontSize": "22px", "marginRight": "10px"}),
                    html.Div(
                        [
                            html.Div(short, style={"fontWeight": "700", "fontSize": "15px",
                                                    "color": "#f1f5f9"}),
                            html.Div(desc, style={"fontSize": "11px", "color": "#64748b",
                                                   "marginTop": "1px"}),
                        ]
                    ),
                ],
                style={"display": "flex", "alignItems": "center"},
            ),
            html.Div(
                [
                    _status_badge(status),
                    html.Span(
                        "▾" if expanded else "▸",
                        style={"marginLeft": "12px", "color": "#64748b",
                               "fontSize": "18px", "lineHeight": "1"},
                    ),
                ],
                style={"display": "flex", "alignItems": "center", "gap": "8px"},
            ),
        ],
        id={"type": "device-header", "index": device_id},
        n_clicks=0,
        style={
            "display":       "flex",
            "justifyContent":"space-between",
            "alignItems":    "center",
            "padding":       "14px 18px",
            "cursor":        "pointer",
            "borderBottom":  "1px solid #1e293b",
            "userSelect":    "none",
        },
    )

    # ── Тело карточки
    if data:
        brief_keys  = BRIEF_FIELDS["dcu" if is_dcu else "bup"]
        brief_rows  = []
        for k in brief_keys:
            if k in data:
                lbl = FIELD_LABELS.get(k, k)
                brief_rows.append(_field_row(lbl, data[k], highlight=(k in ("status", "angle"))))

        # Детальный раздел
        skip = {"msg_type", "device_id", "shortname", "description", "raw_id",
                "dst_id", "ts_s", "ts_ns"}
        detail_rows = []
        for k, v in data.items():
            if k in skip or k in brief_keys:
                continue
            if isinstance(v, dict):  # PID state
                detail_rows.append(
                    html.Div(
                        [
                            html.Div(FIELD_LABELS.get(k, k),
                                     style={"color": "#475569", "fontSize": "11px",
                                            "fontWeight": "600", "marginTop": "8px",
                                            "marginBottom": "2px",
                                            "textTransform": "uppercase",
                                            "letterSpacing": "0.5px"}),
                            *[_field_row(FIELD_LABELS.get(kk, kk), vv)
                              for kk, vv in v.items()],
                        ]
                    )
                )
            else:
                detail_rows.append(_field_row(FIELD_LABELS.get(k, k), v))

        ts = data.get("timestamp", "")
        ts_short = ts[11:19] if len(ts) >= 19 else ts

        body_content = html.Div(
            [
                # Краткие данные
                html.Div(brief_rows, style={"marginBottom": "12px"}),
                # Время пакета
                html.Div(
                    f"Последнее обновление: {ts_short} UTC",
                    style={"fontSize": "11px", "color": "#475569", "marginBottom": "10px"},
                ),
                # Кнопка "показать детали"
                html.Div(
                    [
                        html.Div(
                            "Детальные параметры",
                            style={"color": "#64748b", "fontWeight": "bold",
                                   "fontSize": "11px", "textTransform": "uppercase",
                                   "marginBottom": "8px", "marginTop": "12px"},
                        ),
                        html.Div(detail_rows, style={"paddingLeft": "8px"}),
                    ]
                ),
            ],
            style={"padding": "14px 18px"},
        )
    else:
        body_content = html.Div(
            "Нет данных от устройства",
            style={"padding": "14px 18px", "color": "#475569", "fontSize": "13px"},
        )

    body = html.Div(
        body_content,
        id={"type": "device-body", "index": device_id},
        style={"display": "block" if expanded else "none"},
    )

    return html.Div(
        [header, body],
        style={
            "background":   "#0f172a",
            "border":       f"1px solid {badge_color}33",
            "borderRadius": "12px",
            "marginBottom": "12px",
            "overflow":     "hidden",
            "transition":   "border-color 0.3s",
        },
    )


# ─── Layout ──────────────────────────────────────────────────

devices_layout = html.Div(
    [
        # Хранилище состояний устройств
        dcc.Store(id="devices-store", data={}, storage_type="memory"),
        # Хранилище развёрнутых карточек
        dcc.Store(id="devices-expanded", data={"dcu": True}, storage_type="memory"),

        html.Div(
            [
                html.Div(
                    "🤖 Устройства BRO30",
                    style={
                        "fontSize":     "20px",
                        "fontWeight":   "700",
                        "color":        "#f1f5f9",
                        "marginBottom": "4px",
                    },
                ),
                html.Div(
                    id="devices-connection-status",
                    style={"fontSize": "12px", "color": "#64748b"},
                ),
            ],
            style={"marginBottom": "20px"},
        ),

        html.Div(id="devices-list"),
    ],
    style={
        "padding":          "24px",
        "background":       "#020617",
        "minHeight":        "100vh",
        "fontFamily":       "'Inter', 'Segoe UI', sans-serif",
    },
)


# ─── Callbacks ───────────────────────────────────────────────

def register_devices_callbacks(app):

    # 1. Обновляем devices-store при приходе WS-сообщений
    @app.callback(
        Output("devices-store", "data"),
        Input("ws", "message"),
        State("devices-store", "data"),
        prevent_initial_call=True,
    )
    def update_devices_store(message, store):
        if not message:
            return no_update
        try:
            msg      = json.loads(message["data"])
            msg_type = msg.get("type")

            if msg_type == "devices_snapshot":
                # Полный снимок всех устройств
                return msg.get("payload", {})

            elif msg_type == "device_telemetry":
                payload = msg.get("payload", {})
                dev_id  = payload.get("device_id")
                if dev_id:
                    store = dict(store)
                    store[dev_id] = payload
                    return store

        except Exception:
            pass
        return no_update

    # 2. Переключение expanded при клике на заголовок
    @app.callback(
        Output("devices-expanded", "data"),
        Input({"type": "device-header", "index": ALL}, "n_clicks"),
        State("devices-expanded", "data"),
        prevent_initial_call=True,
    )
    def toggle_expand(n_clicks_list, expanded):
        triggered = ctx.triggered_id
        if not triggered or not isinstance(triggered, dict):
            return no_update
        
        # Prevent toggling on DOM recreate when n_clicks is 0 or None
        t_val = ctx.triggered[0].get("value")
        if not t_val:
            return no_update

        dev_id   = triggered["index"]
        expanded = dict(expanded)
        expanded[dev_id] = not expanded.get(dev_id, False)
        return expanded

    # 3. Рендер карточек
    @app.callback(
        Output("devices-list", "children"),
        Output("devices-connection-status", "children"),
        Input("devices-store", "data"),
        State("devices-expanded", "data"),
        prevent_initial_call=False,
    )
    def render_devices(store, expanded):
        cards = []
        active = 0
        for dev_id in DEVICE_ORDER:
            data   = store.get(dev_id) if store else None
            is_exp = expanded.get(dev_id, False) if expanded else False
            cards.append(_make_card(dev_id, data, is_exp))
            if data:
                active += 1

        total   = len(DEVICE_ORDER)
        status  = (
            f"Онлайн: {active}/{total} устройств"
            if active > 0
            else "Ожидание данных от шлюза..."
        )
        return cards, status