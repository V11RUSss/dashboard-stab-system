"""Low-overhead live telemetry chart backed by client-side buffers."""

from dash import ClientsideFunction, dcc, html
from dash.dependencies import Input, Output, State

# Labels retain the wire message name; message_type is the Backend event name.
PARAMETERS = []
for source, message_type, fields in [
    ("dcu_tel", "dcu_telemetry", [
        ("load_sens_angle", "Угол датчика нагрузки", "рад"),
        ("load_sens_speed", "Скорость датчика нагрузки", "рад/с"),
        ("rotor_sens_angle", "Угол датчика ротора", "рад"),
        ("rotor_sens_speed", "Скорость датчика ротора", "рад/с"),
        ("power_vdc", "Напряжение силовой цепи DC", "В"),
        ("power_voltage", "Напряжение питания модуля", "В"),
        ("electrical_power", "Электрическая мощность", "Вт"),
        ("stator_temp", "Температура статора", "°C"),
        ("ibrk", "Ток тормоза", "А"),
        ("phase_currents", "Фазные токи A/B/C", "А"),
    ]),
    ("bup_state", "bup_state", [
        ("angle", "Угол привода в локальной системе", "рад"),
        ("speed", "Скорость привода в локальной системе", "рад/с"),
        ("id", "Ток по оси d", "А"),
        ("iq", "Ток по оси q", "А"),
        ("load_sens_angle", "Угол датчика нагрузки", "рад"),
        ("load_sens_speed", "Скорость датчика нагрузки", "рад/с"),
        ("rotor_sens_angle", "Многооборотный угол ротора", "рад"),
        ("rotor_sens_speed", "Скорость датчика ротора", "рад/с"),
        ("power_voltage", "Напряжение питания модуля", "В"),
        ("electrical_power", "Электрическая мощность", "Вт"),
        ("stator_temp", "Температура статора", "°C"),
    ]),
]:
    for field, label, unit in fields:
        PARAMETERS.append({
            "key": f"{message_type}:{field}", "source": source,
            "message_type": message_type, "field": field, "unit": unit,
            "label": f"{label}, {unit} ({source})",
            "description": f"{source}.{field} — {label}. Единица: {unit}.",
            "fields": ["current_a", "current_b", "current_c"]
            if field == "phase_currents" else [field],
        })

DEVICE_LABELS = {
    "bup_dcu_lfd": "МУП.ТЛП — Лев. передний",
    "bup_dcu_rfd": "МУП.ТПП — Прав. передний",
    "bup_dcu_lrd": "МУП.ТЛЗ — Лев. задний",
    "bup_dcu_rrd": "МУП.ТПЗ — Прав. задний",
    "bup_dcu_lst": "МУП.РЛ — Лев. рулевой",
    "bup_dcu_rst": "МУП.РП — Прав. рулевой",
}

GRAPH_CONFIG = {
    "parameters": PARAMETERS,
    "device_labels": DEVICE_LABELS,
    "max_buffer_points": 216_000,
}


graph_layout = html.Div(
    [
        dcc.Store(id="graph-config-store", data=GRAPH_CONFIG, storage_type="memory"),
        html.Div(
            [
                html.Div([
                    html.Label("Устройство", className="control-label"),
                    dcc.Dropdown(
                        id="graph-device-select",
                        options=[{"label": v, "value": k} for k, v in DEVICE_LABELS.items()],
                        value="bup_dcu_lfd", clearable=False, className="app-select",
                    ),
                ], className="surface scope-device"),
                html.Div(id="recording-controls", className="recording-control"),
            ],
            className="page-heading scope-topbar",
        ),
        html.Div(id="recording-library", className="surface recording-library"),
        # These empty containers are owned by graph_clientside.js. Controls
        # stay in the browser and never send telemetry through Python callbacks.
        html.Div(id="scope-toolbar"),
        html.Div([
            dcc.Graph(
                id="live-graph", figure={"data": [], "layout": {}},
                config={"displaylogo": False, "responsive": True, "scrollZoom": True,
                        "doubleClick": False, "modeBarButtonsToRemove": ["autoScale2d", "resetScale2d"]},
                className="plot-card scope-plot", style={"height": "560px"},
            ),
            html.Aside(id="scope-signals", className="surface scope-signals"),
        ], className="scope-workspace"),
        html.Div(id="graph-status", className="graph-status", role="status"),
        html.Div("Пауза не останавливает приём и запись. "
                 "Время устройства показано в UTC. История хранится в памяти вкладки.",
                 className="scope-note"),
        dcc.Store(id="scope-ingress"),
        dcc.Interval(id="scope-render-clock", interval=250, n_intervals=0),
    ],
    className="page-section telemetry-page",
)


def register_graph_callbacks(app):
    """Register browser-side callbacks; live points do not visit Dash/Python."""
    app.clientside_callback(
        ClientsideFunction(namespace="bro30Graph", function_name="ingest"),
        Output("scope-ingress", "data"),
        Input("ws", "message"), State("graph-config-store", "data"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction(namespace="bro30Graph", function_name="render"),
        Output("live-graph", "figure"), Output("graph-status", "children"),
        Input("scope-render-clock", "n_intervals"),
        Input("graph-device-select", "value"), Input("theme-store", "data"),
        Input("live-graph", "relayoutData"), State("graph-config-store", "data"),
    )
