import json
import dash_ace
from dash import dcc, html, no_update, ctx
from dash.dependencies import Output, Input, State
from styles import BUTTON_STYLE, PANEL_STYLE, INPUT_STYLE

editor_layout = html.Div(
    [
        html.Div(
            [
                html.Div(
                    [
                        html.Button(
                            "Загрузить список",
                            id="btn-refresh",
                            n_clicks=0,
                            style={
                                **BUTTON_STYLE,
                                "width": "auto",
                                "padding": "6px 12px",
                            },
                        ),
                        dcc.Dropdown(
                            id="script-dropdown",
                            placeholder="Выберите файл...",
                            options=[],
                            style={
                                "width": "220px",
                                "display": "inline-block",
                                "verticalAlign": "middle",
                                "marginLeft": "10px",
                            },
                        ),
                        html.Div(
                            style={
                                "width": "1px",
                                "height": "30px",
                                "backgroundColor": "#ccc",
                                "margin": "0 10px",
                            }
                        ),
                        dcc.Input(
                            id="input-new-filename",
                            type="text",
                            placeholder="new_script.py",
                            style={
                                **INPUT_STYLE,
                                "width": "180px",
                                "display": "inline-block",
                            },
                        ),
                        html.Button(
                            "Создать",
                            id="btn-create",
                            n_clicks=0,
                            style={
                                **BUTTON_STYLE,
                                "width": "auto",
                                "marginLeft": "5px",
                                "backgroundColor": "#17a2b8",
                            },
                        ),
                        html.Button(
                            "Удалить",
                            id="btn-remove",
                            n_clicks=0,
                            style={
                                **BUTTON_STYLE,
                                "width": "auto",
                                "marginLeft": "5px",
                                "backgroundColor": "#990930",
                            },
                        ),
                        html.Button(
                            "Открыть",
                            id="btn-open",
                            n_clicks=0,
                            style={
                                **BUTTON_STYLE,
                                "width": "auto",
                                "marginLeft": "10px",
                                "padding": "6px 16px",
                            },
                        ),
                        html.Button(
                            "Сохранить и Отформатировать",
                            id="btn-save",
                            n_clicks=0,
                            style={
                                **BUTTON_STYLE,
                                "width": "auto",
                                "marginLeft": "10px",
                                "backgroundColor": "#28a745",
                            },
                        ),
                        html.Button(
                            "Тест",
                            id="btn-test",
                            n_clicks=0,
                            style={
                                **BUTTON_STYLE,
                                "width": "auto",
                                "marginLeft": "10px",
                                "backgroundColor": "#ffc107",
                                "color": "black",
                            },
                        ),
                        html.Div(
                            id="editor-status",
                            style={
                                "display": "inline-block",
                                "marginLeft": "15px",
                                "fontSize": "13px",
                                "fontWeight": "bold",
                            },
                        ),
                    ],
                    style={"marginBottom": "12px"},
                ),
                dash_ace.DashAceEditor(
                    id="script-editor",
                    value="",
                    theme="twilight",
                    mode="python",
                    tabSize=4,
                    focus=True,
                    enableBasicAutocompletion=True,
                    enableLiveAutocompletion=True,
                    width="100%",
                    height="450px",
                    style={
                        "borderRadius": "4px",
                        "border": "1px solid #dddddd",
                        "fontSize": "14px",
                        "marginTop": "10px",
                    },
                ),
                html.Div(
                    "Логи сервера:",
                    style={
                        "marginTop": "20px",
                        "fontWeight": "bold",
                        "fontSize": "14px",
                    },
                ),
                dcc.Textarea(
                    id="editor-console",
                    readOnly=True,
                    style={
                        "width": "100%",
                        "height": "200px",
                        "marginTop": "8px",
                        "fontFamily": "Courier New, monospace",
                        "fontSize": "13px",
                        "padding": "10px",
                        "border": "1px solid #333",
                        "borderRadius": "4px",
                        "boxSizing": "border-box",
                        "backgroundColor": "#1e1e1e",
                        "color": "#00ff00",
                        "resize": "vertical",
                    },
                ),
            ],
            style={
                **PANEL_STYLE,
                "marginTop": "10px",
                "display": "flex",
                "alignItems": "center",
                "flexWrap": "wrap",
            },
        ),
    ]
)


def register_editor_callbacks(app):
    @app.callback(
        Output("ws", "send", allow_duplicate=True),
        Input("btn-refresh", "n_clicks"),
        Input("btn-open", "n_clicks"),
        Input("btn-create", "n_clicks"),
        Input("btn-remove", "n_clicks"),
        Input("btn-save", "n_clicks"),
        Input("btn-test", "n_clicks"),
        State("input-new-filename", "value"),
        State("script-dropdown", "value"),
        State("script-editor", "value"),
        prevent_initial_call=True,
    )
    def dispatch_requests(
        n_ref,
        n_open,
        n_create,
        n_remove,
        n_save,
        n_test,
        new_filename,
        filename,
        content,
    ):
        trigger = ctx.triggered_id
        if trigger == "btn-refresh":
            return json.dumps({"action": "get_list", "payload": {}})
        if trigger == "btn-open" and filename:
            return json.dumps(
                {"action": "read_file", "payload": {"filename": filename}}
            )
        if trigger == "btn-save" and filename and content:
            return json.dumps(
                {
                    "action": "save_and_format",
                    "payload": {"filename": filename, "content": content},
                }
            )
        if trigger == "btn-test" and filename:
            return json.dumps(
                {"action": "test_script", "payload": {"filename": filename}}
            )
        if trigger == "btn-remove" and filename:
            return json.dumps(
                {"action": "remove_file", "payload": {"filename": filename}}
            )
        if trigger == "btn-create" and new_filename:
            return json.dumps(
                {
                    "action": "save_and_format",
                    "payload": {
                        "filename": new_filename.strip(),
                        "content": content or "# Новый скрипт\n",
                    },
                }
            )

        return no_update

    @app.callback(
        Output("script-dropdown", "options"),
        Output("script-dropdown", "value"),  # Добавлен фиксатор текущего имени файла
        Output("script-editor", "value"),
        Output("editor-console", "value"),
        Output("editor-status", "children"),
        Output("editor-status", "style"),
        Input("ws", "message"),
        State("editor-console", "value"),
        prevent_initial_call=True,
    )
    def handle_incoming(message, current_logs):
        if not message:
            return (no_update,) * 6
        try:
            msg = json.loads(message["data"])
            msg_type = msg.get("type")
            payload = msg.get("payload", {})

            if msg_type == "file_list":
                files = payload.get("files", [])
                options = [{"label": f, "value": f} for f in files]
                return (
                    options,
                    no_update,
                    no_update,
                    no_update,
                    "Список обновлен",
                    {"color": "#4a90e2"},
                )

            if msg_type == "file_content":
                content = payload.get("content", "")
                fname = payload.get("filename")
                return (
                    no_update,
                    fname or no_update,
                    content,
                    no_update,
                    "Код открыт",
                    {"color": "#28a745"},
                )

            if msg_type == "editor_console":
                new_logs = payload.get("msg", "")
                updated_logs = (current_logs or "") + new_logs + "\n"
                return (
                    no_update,
                    no_update,
                    no_update,
                    updated_logs[-50000:],
                    "Получены логи сервера",
                    {"color": "#ffc107"},
                )

        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        return (no_update,) * 6
