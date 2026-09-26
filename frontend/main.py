import os

import dash
from dash import Input, Output, State, dcc, html
from dash_extensions import WebSocket
from tabs.devices import devices_layout, register_devices_callbacks
from tabs.editor import editor_layout, register_editor_callbacks
from tabs.graph import graph_layout, register_graph_callbacks

app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,
    update_title=None,
)
app.title = "BRO30 Dashboard"

from recording_proxy import register_recording_routes
register_recording_routes(app.server)

WEBSOCKET_URL = os.environ.get("WEBSOCKET_URL", "ws://localhost:8765")

# Apply the saved theme before Dash renders to avoid a light/dark flash.
app.index_string = """
<!DOCTYPE html>
<html>
<head>
    {%metas%}
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{%title%}</title>
    {%favicon%}
    <script>
        (function () {
            const requested = new URLSearchParams(window.location.search).get("theme");
            const saved = window.localStorage.getItem("bro30-theme");
            const preferred = window.matchMedia("(prefers-color-scheme: dark)").matches
                ? "dark" : "light";
            const selected = ["dark", "light"].includes(requested)
                ? requested : (saved || preferred);
            window.localStorage.setItem("bro30-theme", selected);
            document.documentElement.setAttribute("data-bs-theme", selected);
        }());
    </script>
    {%css%}
</head>
<body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>
"""


app.layout = html.Div(
    [
        WebSocket(id="ws", url=WEBSOCKET_URL),
        # Route the shared high-rate WebSocket stream in the browser first.
        # Server-side callbacks then receive only the event families they use;
        # telemetry intended for Plotly never creates Flask/Dash POST traffic.
        dcc.Store(id="devices-live-event", storage_type="memory"),
        dcc.Store(id="devices-ws-event", storage_type="memory"),
        dcc.Store(id="editor-ws-event", storage_type="memory"),
        dcc.Store(id="theme-store", storage_type="local"),
        html.Header(
            html.Div(
                [
                    html.Div(
                        [
                            html.Span("B30", className="brand-mark"),
                            html.Div(
                                [
                                    html.Div("BRO30 Dashboard", className="brand-title"),
                                    html.Div("MicroJet monitor", className="brand-subtitle"),
                                ]
                            ),
                        ],
                        className="brand",
                    ),
                    html.Button(
                        "Тема",
                        id="theme-toggle",
                        n_clicks=0,
                        className="btn btn-sm btn-outline-secondary theme-toggle",
                        title="Переключить цветовую тему",
                    ),
                ],
                className="app-container app-navbar-inner",
            ),
            className="app-navbar",
        ),
        html.Main(
            dcc.Tabs(
                [
                    dcc.Tab(
                        label="Телеметрия",
                        value="telemetry",
                        children=[graph_layout],
                        className="app-tab",
                        selected_className="app-tab app-tab-selected",
                    ),
                    dcc.Tab(
                        label="Устройства",
                        value="devices",
                        children=[devices_layout],
                        className="app-tab",
                        selected_className="app-tab app-tab-selected",
                    ),
                    dcc.Tab(
                        label="Скрипты",
                        value="scripts",
                        children=[editor_layout],
                        className="app-tab",
                        selected_className="app-tab app-tab-selected",
                    ),
                ],
                id="main-tabs",
                value="telemetry",
                className="app-tabs",
            ),
            className="app-container app-main",
        ),
    ],
    className="app-shell",
)


app.clientside_callback(
    """
    function (message) {
        const noUpdate = window.dash_clientside.no_update;
        if (!message || typeof message.data !== "string") {
            return [noUpdate, noUpdate, noUpdate];
        }

        let event;
        try {
            event = JSON.parse(message.data);
        } catch (_error) {
            return [noUpdate, noUpdate, noUpdate];
        }

        const eventType = event && event.type;
        const liveDeviceEvents = new Set([
            "devices_snapshot",
            "devices_batch",
            "device_telemetry",
            "system_status",
        ]);
        const deviceEvents = new Set([
            "devices_snapshot",
            "devices_batch",
            "device_telemetry",
            "control_catalog",
            "control_snapshot",
            "control_context",
            "control_response",
            "request_error",
            "request_ok",
        ]);
        const editorEvents = new Set([
            "file_list",
            "file_content",
            "editor_console",
            "script_result",
            "request_error",
        ]);

        // Full telemetry details are deliberately refreshed at 1 Hz.  The
        // separate live route above still refreshes ONLINE/OFFLINE as soon as
        // every 500 ms heartbeat arrives.
        const routeState = window.__bro30WsRouteState || {lastDeviceUiAt: 0};
        window.__bro30WsRouteState = routeState;
        let deviceMessage = deviceEvents.has(eventType) ? message : noUpdate;
        if (eventType === "devices_batch") {
            const now = Date.now();
            if (now - routeState.lastDeviceUiAt < 1000) {
                deviceMessage = noUpdate;
            } else {
                routeState.lastDeviceUiAt = now;
            }
        }

        return [
            liveDeviceEvents.has(eventType) ? message : noUpdate,
            deviceMessage,
            editorEvents.has(eventType) ? message : noUpdate,
        ];
    }
    """,
    Output("devices-live-event", "data"),
    Output("devices-ws-event", "data"),
    Output("editor-ws-event", "data"),
    Input("ws", "message"),
    prevent_initial_call=True,
)


app.clientside_callback(
    """
    function (clicks, currentTheme) {
        const stored = window.localStorage.getItem("bro30-theme");
        const preferred = window.matchMedia("(prefers-color-scheme: dark)").matches
            ? "dark" : "light";
        const active = currentTheme || stored || preferred;
        if (!clicks) {
            return active;
        }
        return active === "dark" ? "light" : "dark";
    }
    """,
    Output("theme-store", "data"),
    Input("theme-toggle", "n_clicks"),
    State("theme-store", "data"),
)

app.clientside_callback(
    """
    function (theme) {
        const resolved = theme || "dark";
        document.documentElement.setAttribute("data-bs-theme", resolved);
        window.localStorage.setItem("bro30-theme", resolved);
        return resolved === "dark" ? "Светлая тема" : "Тёмная тема";
    }
    """,
    Output("theme-toggle", "children"),
    Input("theme-store", "data"),
)


register_graph_callbacks(app)
register_devices_callbacks(app)
register_editor_callbacks(app)


if __name__ == "__main__":
    debug = os.environ.get("DASH_DEBUG", "false").lower() in {"1", "true", "yes"}
    app.run(host="0.0.0.0", port=8050, debug=debug, use_reloader=False)
