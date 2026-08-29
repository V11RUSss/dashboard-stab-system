"""Low-overhead live telemetry chart backed by client-side buffers."""

from dash import ClientsideFunction, dcc, html
from dash.dependencies import Input, Output, State

GRAPHABLE_PARAMS = {
    "angle": "Угол нагрузки (рад)",
    "speed": "Скорость нагрузки (рад/с)",
    "load_sens_angle": "Угол нагрузки — DCU (рад)",
    "load_sens_speed": "Скорость нагрузки — DCU (рад/с)",
    "rotor_sens_angle": "Угол ротора (рад)",
    "rotor_sens_speed": "Скорость ротора (рад/с)",
    "power_vdc": "Напряжение DC (В)",
    "power_voltage": "Напряжение питания (В)",
    "electrical_power": "Мощность (Вт)",
    "stator_temp": "Температура статора (°C)",
    "ibrk": "Ток тормоза (А)",
    "phase_currents": "Токи фаз A/B/C (А)",
    "id": "Ток id (А)",
    "iq": "Ток iq (А)",
}

PARAM_MESSAGE_TYPES = {
    "angle": "bup_state",
    "speed": "bup_state",
    "id": "bup_state",
    "iq": "bup_state",
    "load_sens_angle": "dcu_telemetry",
    "load_sens_speed": "dcu_telemetry",
    "rotor_sens_angle": "dcu_telemetry",
    "rotor_sens_speed": "dcu_telemetry",
    "power_vdc": "dcu_telemetry",
    "power_voltage": "dcu_telemetry",
    "electrical_power": "dcu_telemetry",
    "stator_temp": "dcu_telemetry",
    "ibrk": "dcu_telemetry",
    "phase_currents": "dcu_telemetry",
}

PARAMETER_SERIES = {
    "phase_currents": [
        {"field": "current_a", "label": "Фаза A", "color": "#ef4444"},
        {"field": "current_b", "label": "Фаза B", "color": "#f59e0b"},
        {"field": "current_c", "label": "Фаза C", "color": "#3b82f6"},
    ]
}

DEVICE_LABELS = {
    "bup_dcu_lfd": "МУП.ТЛП — Лев. передний",
    "bup_dcu_rfd": "МУП.ТПП — Прав. передний",
    "bup_dcu_lrd": "МУП.ТЛЗ — Лев. задний",
    "bup_dcu_rrd": "МУП.ТПЗ — Прав. задний",
    "bup_dcu_lst": "МУП.РЛ — Лев. рулевой",
    "bup_dcu_rst": "МУП.РП — Прав. рулевой",
}

DEVICE_COLOR_LIST = [
    "#10b981",
    "#3b82f6",
    "#f59e0b",
    "#ef4444",
    "#8b5cf6",
    "#06b6d4",
    "#ec4899",
]
DEVICE_COLORS = {
    device_id: DEVICE_COLOR_LIST[index % len(DEVICE_COLOR_LIST)]
    for index, device_id in enumerate(DEVICE_LABELS)
}

# Six hours at the normal 10 Hz UI rate. This is only an in-browser safety
# ceiling for switching traces/themes; the currently visible session is not
# truncated unless the user explicitly selects a rolling window.
MAX_BUFFER_POINTS = 216_000

HISTORY_OPTIONS = [
    {"label": "Вся сессия", "value": 0},
    {"label": "Последние 5 минут", "value": 3_000},
    {"label": "Последние 15 минут", "value": 9_000},
    {"label": "Последний час", "value": 36_000},
]

GRAPH_CONFIG = {
    "parameter_labels": GRAPHABLE_PARAMS,
    "parameter_message_types": PARAM_MESSAGE_TYPES,
    "parameter_series": PARAMETER_SERIES,
    "device_labels": DEVICE_LABELS,
    "device_colors": DEVICE_COLORS,
    "max_buffer_points": MAX_BUFFER_POINTS,
}


def _initial_figure() -> dict:
    """Keep all three trace slots present before the first WS event arrives."""
    return {
        "data": [
            {
                "type": "scatter",
                "mode": "lines",
                "x": [],
                "y": [],
                "line": {"width": 0},
                "hoverinfo": "skip",
                "showlegend": False,
                "name": "Минимум",
            },
            {
                "type": "scatter",
                "mode": "lines",
                "x": [],
                "y": [],
                "line": {"width": 0},
                "fill": "tonexty",
                "hoverinfo": "skip",
                "name": "Диапазон min–max",
            },
            {
                "type": "scatter",
                "mode": "lines",
                "x": [],
                "y": [],
                "line": {"width": 2},
                "name": "Среднее",
            },
        ],
        "layout": {"margin": {"l": 60, "r": 20, "t": 50, "b": 50}},
    }


graph_layout = html.Div(
    [
        dcc.Store(id="graph-config-store", data=GRAPH_CONFIG, storage_type="memory"),
        dcc.Store(
            id="recording-status-store",
            data={"state": "idle"},
            storage_type="memory",
        ),
        dcc.Interval(id="recording-clock", interval=1_000, n_intervals=0),
        dcc.Interval(
            id="recording-status-request",
            interval=500,
            n_intervals=0,
            max_intervals=1,
        ),
        html.Div(
            [
                html.Div(
                    [
                        html.H1("Телеметрия", className="page-title"),
                        html.Div(
                            "Агрегированные параметры приводов в реальном времени",
                            className="page-description",
                        ),
                    ]
                ),
                html.Div(
                    [
                        html.Button(
                            "Начать запись",
                            id="recording-toggle-btn",
                            n_clicks=0,
                            className=(
                                "btn btn-sm btn-outline-primary recording-button"
                            ),
                            title="Записать сырую телеметрию в отдельный PKL",
                        ),
                        html.Div(
                            [
                                html.Div(
                                    "Новая запись",
                                    className="recording-panel-title",
                                ),
                                html.Label(
                                    "Продолжительность",
                                    className="control-label",
                                ),
                                dcc.Dropdown(
                                    id="recording-duration-select",
                                    options=[
                                        {
                                            "label": "До ручной остановки",
                                            "value": "manual",
                                        },
                                        {"label": "1 минута", "value": 60},
                                        {"label": "5 минут", "value": 300},
                                        {"label": "10 минут", "value": 600},
                                        {"label": "30 минут", "value": 1800},
                                        {"label": "60 минут", "value": 3600},
                                    ],
                                    value=300,
                                    clearable=False,
                                    className="app-select recording-duration",
                                ),
                                html.Label(
                                    "Название (необязательно)",
                                    className="control-label recording-label-caption",
                                ),
                                dcc.Input(
                                    id="recording-label-input",
                                    type="text",
                                    maxLength=80,
                                    placeholder="Например: тест углов",
                                    className="form-control form-control-sm",
                                ),
                                html.Div(
                                    "",
                                    id="recording-panel-status",
                                    className="recording-panel-status",
                                ),
                                html.Div(
                                    [
                                        html.Button(
                                            "Отмена",
                                            id="recording-cancel-btn",
                                            n_clicks=0,
                                            className="btn btn-sm btn-outline-secondary",
                                        ),
                                        html.Button(
                                            "Начать",
                                            id="recording-start-btn",
                                            n_clicks=0,
                                            className="btn btn-sm btn-primary",
                                        ),
                                    ],
                                    className="recording-panel-actions",
                                ),
                            ],
                            id="recording-panel",
                            className="surface recording-panel",
                            hidden=True,
                        ),
                    ],
                    className="recording-control",
                ),
            ],
            className="page-heading",
        ),
        html.Div(
            [
                html.Div(
                    [
                        html.Label("Устройство", className="control-label"),
                        dcc.Dropdown(
                            id="graph-device-select",
                            options=[
                                {"label": value, "value": key}
                                for key, value in DEVICE_LABELS.items()
                            ],
                            value="bup_dcu_lfd",
                            clearable=False,
                            className="app-select",
                        ),
                    ],
                    className="control-group",
                ),
                html.Div(
                    [
                        html.Label("Параметр", className="control-label"),
                        dcc.Dropdown(
                            id="graph-param-select",
                            options=[
                                {"label": value, "value": key}
                                for key, value in GRAPHABLE_PARAMS.items()
                            ],
                            value="load_sens_angle",
                            clearable=False,
                            className="app-select",
                        ),
                    ],
                    className="control-group control-group-wide",
                ),
                html.Div(
                    [
                        html.Label("История", className="control-label"),
                        dcc.Dropdown(
                            id="graph-history-select",
                            options=HISTORY_OPTIONS,
                            value=0,
                            clearable=False,
                            className="app-select",
                        ),
                    ],
                    className="control-group",
                ),
                html.Button(
                    "Сбросить",
                    id="graph-reset-btn",
                    n_clicks=0,
                    className="btn btn-sm btn-outline-danger",
                ),
            ],
            className="surface toolbar",
        ),
        dcc.Graph(
            id="live-graph",
            figure=_initial_figure(),
            config={
                "displayModeBar": True,
                "displaylogo": False,
                "responsive": True,
                "scrollZoom": True,
            },
            className="plot-card",
            style={"height": "420px"},
        ),
        html.Div("", id="graph-status", className="graph-status"),
    ],
    className="page-section",
)


def register_graph_callbacks(app):
    """Register browser-side callbacks; live points do not visit Dash/Python."""
    app.clientside_callback(
        ClientsideFunction(namespace="bro30Graph", function_name="extendTelemetry"),
        Output("live-graph", "extendData"),
        Output("graph-status", "children"),
        Input("ws", "message"),
        Input("graph-reset-btn", "n_clicks"),
        State("graph-device-select", "value"),
        State("graph-param-select", "value"),
        State("graph-history-select", "value"),
        State("graph-config-store", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction(namespace="bro30Graph", function_name="buildFigure"),
        Output("live-graph", "figure"),
        Input("graph-device-select", "value"),
        Input("graph-param-select", "value"),
        Input("theme-store", "data"),
        Input("graph-reset-btn", "n_clicks"),
        Input("graph-history-select", "value"),
        State("graph-config-store", "data"),
        prevent_initial_call=False,
    )

    app.clientside_callback(
        ClientsideFunction(
            namespace="bro30Recording", function_name="receiveStatus"
        ),
        Output("recording-status-store", "data"),
        Input("ws", "message"),
        State("recording-status-store", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """
        function (_tick) {
            return JSON.stringify({
                action: "get_recording_status",
                payload: {},
                request_id: "recording-initial-status",
            });
        }
        """,
        Output("ws", "send", allow_duplicate=True),
        Input("recording-status-request", "n_intervals"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction(
            namespace="bro30Recording", function_name="renderStatus"
        ),
        Output("recording-toggle-btn", "children"),
        Output("recording-toggle-btn", "className"),
        Output("recording-toggle-btn", "title"),
        Output("recording-toggle-btn", "disabled"),
        Output("recording-panel-status", "children"),
        Input("recording-status-store", "data"),
        Input("recording-clock", "n_intervals"),
    )

    app.clientside_callback(
        ClientsideFunction(
            namespace="bro30Recording", function_name="handleControls"
        ),
        Output("ws", "send", allow_duplicate=True),
        Output("recording-panel", "hidden"),
        Output("recording-label-input", "value"),
        Input("recording-toggle-btn", "n_clicks"),
        Input("recording-start-btn", "n_clicks"),
        Input("recording-cancel-btn", "n_clicks"),
        Input("recording-status-store", "data"),
        State("recording-duration-select", "value"),
        State("recording-label-input", "value"),
        State("recording-panel", "hidden"),
        prevent_initial_call=True,
    )
