"""
tabs/graph.py — График телеметрии с выбором устройства и параметра.
"""

import json
from collections import deque
from dash import dcc, html, no_update, callback_context as ctx
from dash.dependencies import Output, Input, State
import plotly.graph_objs as go

from styles import PANEL_STYLE, INPUT_STYLE, BUTTON_STYLE

# ─── Параметры для построения графика ────────────────────────

GRAPHABLE_PARAMS = {
    "angle":            "Угол нагрузки (рад)",
    "speed":            "Скорость нагрузки (рад/с)",
    "load_sens_angle":  "Угол нагрузки — DCU (рад)",
    "load_sens_speed":  "Скорость нагрузки — DCU (рад/с)",
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
    "id":               "Ток id (А)",
    "iq":               "Ток iq (А)",
}

DEVICE_LABELS = {
    "dcu":         "МУП — Модуль управления",
    "bup_dcu_lfd": "МУП.ТЛП — Лев. передний",
    "bup_dcu_rfd": "МУП.ТПП — Прав. передний",
    "bup_dcu_lrd": "МУП.ТЛЗ — Лев. задний",
    "bup_dcu_rrd": "МУП.ТПЗ — Прав. задний",
    "bup_dcu_lst": "МУП.РЛ — Лев. рулевой",
    "bup_dcu_rst": "МУП.РП — Прав. рулевой",
}

DEVICE_COLORS = [
    "#10b981", "#3b82f6", "#f59e0b", "#ef4444",
    "#8b5cf6", "#06b6d4", "#ec4899",
]

MAX_POINTS = 300

def _hex_to_rgba(hex_str: str, alpha: float) -> str:
    h = hex_str.lstrip('#')
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"

def _label_style():
    return {
        "fontSize": "12px", "color": "#64748b",
        "marginBottom": "6px", "fontWeight": "500",
        "textTransform": "uppercase", "letterSpacing": "0.5px",
    }

def _dropdown_style():
    return {
        "background":   "#1e293b",
        "border":       "1px solid #334155",
        "borderRadius": "8px",
        "color":        "#f1f5f9",
    }

# ─── Layout ──────────────────────────────────────────────────

graph_layout = html.Div(
    [
        # Store: {device_id: {param: [values], ts: [timestamps]}}
        dcc.Store(id="graph-data-store", data={}, storage_type="memory"),

        html.Div(
            "📈 График телеметрии",
            style={
                "fontSize": "20px", "fontWeight": "700",
                "color": "#f1f5f9", "marginBottom": "20px",
                "fontFamily": "'Inter', sans-serif",
            },
        ),

        # ── Панель управления графиком ──
        html.Div(
            [
                html.Div(
                    [
                        html.Div("Устройство", style=_label_style()),
                        dcc.Dropdown(
                            id="graph-device-select",
                            options=[
                                {"label": v, "value": k}
                                for k, v in DEVICE_LABELS.items()
                            ],
                            value="bup_dcu_lfd",
                            clearable=False,
                            style=_dropdown_style(),
                        ),
                    ],
                    style={"flex": "1", "minWidth": "220px"},
                ),
                html.Div(
                    [
                        html.Div("Параметр", style=_label_style()),
                        dcc.Dropdown(
                            id="graph-param-select",
                            options=[
                                {"label": v, "value": k}
                                for k, v in GRAPHABLE_PARAMS.items()
                            ],
                            value="angle",
                            clearable=False,
                            style=_dropdown_style(),
                        ),
                    ],
                    style={"flex": "1", "minWidth": "240px"},
                ),
                html.Div(
                    [
                        html.Div("Точек", style=_label_style()),
                        dcc.Slider(
                            id="graph-points-slider",
                            min=50, max=300, step=50, value=150,
                            marks={50: "50", 150: "150", 300: "300"},
                            tooltip={"placement": "bottom"},
                        ),
                    ],
                    style={"flex": "1", "minWidth": "200px", "paddingTop": "4px"},
                ),
                html.Button(
                    "🗑 Сброс",
                    id="graph-reset-btn",
                    n_clicks=0,
                    style={
                        **BUTTON_STYLE,
                        "height":   "36px",
                        "alignSelf":"flex-end",
                        "marginTop":"18px",
                    },
                ),
            ],
            style={
                "display":    "flex",
                "gap":        "20px",
                "flexWrap":   "wrap",
                "alignItems": "flex-start",
                "marginBottom": "20px",
                "padding":    "16px 20px",
                "background": "#0f172a",
                "borderRadius": "12px",
                "border":     "1px solid #1e293b",
            },
        ),

        # ── Основной график ──
        dcc.Graph(
            id="live-graph",
            config={"displayModeBar": True, "scrollZoom": True},
            style={"height": "420px"},
        ),

        # ── Статусная строка ──
        html.Div(
            id="graph-status",
            style={
                "fontSize": "12px", "color": "#64748b",
                "marginTop": "8px", "fontFamily": "monospace",
            },
        ),

        # ── Мини-таблица последних значений ──
        html.Div(
            id="graph-last-values",
            style={
                "marginTop":    "20px",
                "padding":      "16px 20px",
                "background":   "#0f172a",
                "borderRadius": "12px",
                "border":       "1px solid #1e293b",
            },
        ),
    ],
    style={
        "padding":          "24px",
        "background":       "#020617",
        "minHeight":        "100vh",
        "fontFamily":       "'Inter', 'Segoe UI', sans-serif",
    },
)

# ─── Callbacks ───────────────────────────────────────────────

def register_graph_callbacks(app):

    # 1. Накопление данных в graph-data-store
    @app.callback(
        Output("graph-data-store", "data"),
        Input("ws", "message"),
        Input("graph-reset-btn", "n_clicks"),
        State("graph-data-store", "data"),
        State("graph-points-slider", "value"),
        prevent_initial_call=True,
    )
    def update_graph_store(message, reset_clicks, store, max_pts):
        triggered = ctx.triggered_id
        if triggered == "graph-reset-btn":
            return {}

        if not message:
            return no_update

        try:
            msg      = json.loads(message["data"])
            msg_type = msg.get("type")

            if msg_type == "device_telemetry":
                payload = msg.get("payload", {})
                dev_id  = payload.get("device_id")
                ts      = payload.get("ts_s", 0) + payload.get("ts_ns", 0) * 1e-9
                if not dev_id:
                    return no_update

                store = dict(store)
                if dev_id not in store:
                    store[dev_id] = {"ts": []}

                dev_buf = dict(store[dev_id])
                dev_buf.setdefault("ts", [])
                dev_buf["ts"].append(ts)

                # Сохраняем все числовые параметры
                for k, v in payload.items():
                    if isinstance(v, (int, float)) and k not in (
                        "ts_s", "ts_ns", "raw_id", "dst_id"
                    ):
                        dev_buf.setdefault(k, [])
                        dev_buf[k].append(v)

                # Обрезаем до max_pts
                limit = max_pts or MAX_POINTS
                if len(dev_buf["ts"]) > limit:
                    trim = -limit
                    for kk in dev_buf:
                        dev_buf[kk] = dev_buf[kk][trim:]

                store[dev_id] = dev_buf
                return store

            elif msg_type == "telemetry_history":
                # Инициализация историей при подключении
                store = {}
                for item in msg.get("payload", []):
                    dev_id = item.get("device_id")
                    ts     = item.get("ts_s", 0) + item.get("ts_ns", 0) * 1e-9
                    if not dev_id:
                        continue
                    store.setdefault(dev_id, {"ts": []})
                    store[dev_id]["ts"].append(ts)
                    for k, v in item.items():
                        if isinstance(v, (int, float)) and k not in (
                            "ts_s", "ts_ns", "raw_id", "dst_id"
                        ):
                            store[dev_id].setdefault(k, [])
                            store[dev_id][k].append(v)
                return store

        except Exception:
            pass
        return no_update

    # 2. Рендер графика
    @app.callback(
        Output("live-graph",        "figure"),
        Output("graph-status",      "children"),
        Output("graph-last-values", "children"),
        Input("graph-data-store",   "data"),
        Input("graph-device-select","value"),
        Input("graph-param-select", "value"),
        prevent_initial_call=False,
    )
    def render_graph(store, device, param):
        if not store or device not in store:
            return _empty_figure(f"Нет данных для {DEVICE_LABELS.get(device, device)}"), \
                   "Ожидание телеметрии...", _no_data_panel()

        dev_buf = store[device]
        ts_list = dev_buf.get("ts", [])
        vals    = dev_buf.get(param, [])

        if not vals or not ts_list:
            return _empty_figure(f"Параметр '{param}' недоступен"), \
                   f"Параметр '{param}' не найден в пакетах от {device}", _no_data_panel()

        n = min(len(ts_list), len(vals))
        ts_list = ts_list[-n:]
        vals    = vals[-n:]

        param_label = GRAPHABLE_PARAMS.get(param, param)
        dev_label   = DEVICE_LABELS.get(device, device)
        color_idx   = list(DEVICE_LABELS.keys()).index(device) if device in DEVICE_LABELS else 0
        color       = DEVICE_COLORS[color_idx % len(DEVICE_COLORS)]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=ts_list,
            y=vals,
            mode="lines",
            name=param_label,
            line=dict(color=color, width=2),
            fill="tozeroy",
            fillcolor=color.replace(")", ", 0.08)").replace("rgb", "rgba")
                if "rgb" in color else _hex_to_rgba(color, 0.08),
        ))

        fig.update_layout(
            paper_bgcolor="#0f172a",
            plot_bgcolor="#020617",
            font=dict(color="#94a3b8", family="Inter, monospace", size=12),
            title=dict(
                text=f"{dev_label} — {param_label}",
                font=dict(color="#f1f5f9", size=15),
                x=0.01,
            ),
            xaxis=dict(
                title="Время (unix)",
                gridcolor="#1e293b",
                linecolor="#334155",
                tickformat=".1f",
            ),
            yaxis=dict(
                title=param_label,
                gridcolor="#1e293b",
                linecolor="#334155",
            ),
            margin=dict(l=60, r=20, t=50, b=50),
            legend=dict(
                bgcolor="#0f172a",
                bordercolor="#334155",
            ),
            transition={"duration": 150},
        )

        status = (
            f"Устройство: {dev_label} | Параметр: {param_label} | "
            f"Точек: {n} | Последнее: {vals[-1]:.4f}"
        )
        last_panel = _last_values_panel(store, device, param)
        return fig, status, last_panel

    # 3. Обновление опций параметра при смене устройства
    @app.callback(
        Output("graph-param-select", "options"),
        Output("graph-param-select", "value"),
        Input("graph-device-select", "value"),
        Input("graph-data-store",    "data"),
        State("graph-param-select",  "value"),
        prevent_initial_call=True,
    )
    def update_param_options(device, store, current_param):
        # Берём только параметры, которые реально есть в данных
        if store and device in store:
            available = {
                k: GRAPHABLE_PARAMS[k]
                for k in GRAPHABLE_PARAMS
                if k in store[device] and store[device][k]
            }
        else:
            available = GRAPHABLE_PARAMS

        options = [{"label": v, "value": k} for k, v in available.items()]
        value   = current_param if current_param in available else (
            list(available.keys())[0] if available else "angle"
        )
        return options, value


# ─── Вспомогательные функции ─────────────────────────────────

def _empty_figure(msg: str = "Нет данных"):
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor="#0f172a",
        plot_bgcolor="#020617",
        font=dict(color="#475569"),
        annotations=[dict(
            text=msg,
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=16, color="#475569"),
        )],
        xaxis=dict(gridcolor="#1e293b", linecolor="#334155"),
        yaxis=dict(gridcolor="#1e293b", linecolor="#334155"),
        margin=dict(l=60, r=20, t=50, b=50),
    )
    return fig


def _no_data_panel():
    return html.Div(
        "Ожидание данных...",
        style={"color": "#475569", "fontSize": "13px"},
    )


def _last_values_panel(store: dict, selected_device: str, selected_param: str):
    """Таблица последних значений по каждому устройству для выбранного параметра."""
    rows = [
        html.Div(
            "Последние значения по устройствам",
            style={
                "color": "#64748b", "fontSize": "12px", "fontWeight": "600",
                "textTransform": "uppercase", "letterSpacing": "0.5px",
                "marginBottom": "10px",
            },
        )
    ]
    for i, (dev_id, label) in enumerate(DEVICE_LABELS.items()):
        if dev_id not in store:
            continue
        vals = store[dev_id].get(selected_param, [])
        if not vals:
            continue
        val   = vals[-1]
        color = DEVICE_COLORS[i % len(DEVICE_COLORS)]
        is_selected = dev_id == selected_device
        rows.append(
            html.Div(
                [
                    html.Span(
                        "●", style={"color": color, "marginRight": "8px"}
                    ),
                    html.Span(
                        label.split("—")[0].strip(),
                        style={
                            "minWidth":   "90px",
                            "display":    "inline-block",
                            "color":      "#f1f5f9" if is_selected else "#94a3b8",
                            "fontWeight": "700" if is_selected else "400",
                            "fontSize":   "13px",
                        },
                    ),
                    html.Span(
                        f"{val:.5f}",
                        style={
                            "fontFamily": "monospace",
                            "fontSize":   "13px",
                            "color":      color if is_selected else "#64748b",
                        },
                    ),
                ],
                style={"padding": "4px 0"},
            )
        )
    if len(rows) == 1:
        rows.append(_no_data_panel())
    return html.Div(rows)
