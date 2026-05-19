import dash
import os
from dash import dcc, html
from dash_extensions import WebSocket

from tabs.graph import graph_layout, register_graph_callbacks
from tabs.editor import editor_layout, register_editor_callbacks
from tabs.devices import devices_layout, register_devices_callbacks

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "BRO30 Dashboard"

WEBSOCKET_URL = os.environ.get("WEBSOCKET_URL", "ws://localhost:8765")

# Глобальные шрифты через Google Fonts
app.index_string = """
<!DOCTYPE html>
<html>
<head>
    {%metas%}
    <title>{%title%}</title>
    {%favicon%}
    {%css%}
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #020617; color: #e2e8f0; font-family: 'Inter', sans-serif; }
        /* Стили для вкладок Dash */
        .dash-tab { background: #0f172a !important; color: #64748b !important;
                    border: 1px solid #1e293b !important; border-bottom: none !important;
                    border-radius: 8px 8px 0 0 !important;
                    padding: 10px 24px !important; font-family: 'Inter', sans-serif !important;
                    font-size: 14px !important; font-weight: 500 !important; }
        .dash-tab--selected { background: #1e293b !important; color: #f1f5f9 !important;
                               border-color: #334155 !important; }
        .dash-tabs { border-bottom: 1px solid #1e293b !important; margin-bottom: 0 !important; }
        /* Dropdown тёмная тема */
        .Select-control { background: #1e293b !important; border-color: #334155 !important; }
        .Select-value-label { color: #f1f5f9 !important; }
        .Select-menu-outer { background: #1e293b !important; border-color: #334155 !important; }
        .Select-option { background: #1e293b !important; color: #f1f5f9 !important; }
        .Select-option:hover { background: #334155 !important; }
        .Select-option.is-selected { background: #3b82f6 !important; }
        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: #0f172a; }
        ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
    </style>
</head>
<body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>
"""

app.layout = html.Div(
    [
        # WS подключение
        WebSocket(id="ws", url=WEBSOCKET_URL),

        # Заголовок
        html.Div(
            [
                html.Span("🤖", style={"fontSize": "24px", "marginRight": "10px"}),
                html.Span("BRO30 Dashboard",
                          style={"fontSize": "20px", "fontWeight": "700",
                                 "color": "#f1f5f9", "letterSpacing": "-0.3px"}),
                html.Span("MicroJet протокол",
                          style={"fontSize": "12px", "color": "#475569",
                                 "marginLeft": "16px", "alignSelf": "center"}),
            ],
            style={
                "display":     "flex",
                "alignItems":  "center",
                "padding":     "16px 28px",
                "borderBottom":"1px solid #1e293b",
                "background":  "#0a0f1e",
            },
        ),

        # Вкладки
        dcc.Tabs(
            [
                dcc.Tab(label="📈 График",    children=[graph_layout],   className="dash-tab"),
                dcc.Tab(label="🤖 Устройства",children=[devices_layout], className="dash-tab"),
                dcc.Tab(label="📝 Скрипты",   children=[editor_layout],  className="dash-tab"),
            ],
            colors={"border": "#1e293b", "primary": "#3b82f6", "background": "#020617"},
        ),
    ],
    style={"background": "#020617", "minHeight": "100vh"},
)

register_graph_callbacks(app)
register_devices_callbacks(app)
register_editor_callbacks(app)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=True, use_reloader=False)