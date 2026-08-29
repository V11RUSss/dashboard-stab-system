import json

import dash_ace
from dash import ctx, dcc, html, no_update
from dash.dependencies import Input, Output, State

editor_panel = html.Div(
    [
        html.Div(
            [
                html.Button(
                    "Обновить",
                    id="btn-refresh",
                    n_clicks=0,
                    className="btn btn-sm btn-outline-secondary",
                ),
                dcc.Dropdown(
                    id="script-dropdown",
                    placeholder="Выберите файл...",
                    options=[],
                    className="app-select editor-file-select",
                ),
                html.Button(
                    "Открыть",
                    id="btn-open",
                    n_clicks=0,
                    className="btn btn-sm btn-outline-primary",
                ),
                html.Div(className="toolbar-divider"),
                dcc.Input(
                    id="input-new-filename",
                    type="text",
                    placeholder="new_script.py",
                    className="form-control form-control-sm editor-filename",
                ),
                html.Button(
                    "Создать",
                    id="btn-create",
                    n_clicks=0,
                    className="btn btn-sm btn-outline-primary",
                ),
                html.Button(
                    "Удалить",
                    id="btn-remove",
                    n_clicks=0,
                    className="btn btn-sm btn-outline-danger",
                ),
                html.Div(className="toolbar-divider"),
                html.Button(
                    "Сохранить",
                    id="btn-save",
                    n_clicks=0,
                    className="btn btn-sm btn-primary",
                    title="Сохранить и форматировать",
                ),
                html.Button(
                    "Запустить",
                    id="btn-run",
                    n_clicks=0,
                    className="btn btn-sm btn-success",
                ),
                html.Div(id="editor-status", className="editor-status"),
            ],
            className="editor-toolbar",
        ),
        html.Div(
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
                style={"fontSize": "14px"},
            ),
            className="ace-shell",
        ),
        html.Div("Результат выполнения", className="console-heading"),
        dcc.Textarea(
            id="editor-console",
            readOnly=True,
            className="editor-console",
        ),
    ],
    className="surface editor-shell",
)


editor_layout = html.Div(
    [
        html.Div(
            [
                html.Div(
                    [
                        html.H1("Скрипты", className="page-title"),
                        html.Div(
                            "Редактирование и запуск диагностических сценариев",
                            className="page-description",
                        ),
                    ]
                )
            ],
            className="page-heading",
        ),
        editor_panel,
    ],
    className="page-section",
)


def register_editor_callbacks(app):
    app.clientside_callback(
        """
        function (theme) {
            return theme === "light" ? "github" : "twilight";
        }
        """,
        Output("script-editor", "theme"),
        Input("theme-store", "data"),
    )

    @app.callback(
        Output("ws", "send", allow_duplicate=True),
        Input("btn-refresh", "n_clicks"),
        Input("btn-open", "n_clicks"),
        Input("btn-create", "n_clicks"),
        Input("btn-remove", "n_clicks"),
        Input("btn-save", "n_clicks"),
        Input("btn-run", "n_clicks"),
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
        n_run,
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
        if trigger == "btn-save" and filename:
            return json.dumps(
                {
                    "action": "save_and_format",
                    "payload": {"filename": filename, "content": content},
                }
            )
        if trigger == "btn-run":
            return json.dumps(
                {"action": "run_script", "payload": {"code": content or ""}}
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
        Output("script-dropdown", "value"),
        Output("script-editor", "value"),
        Output("editor-console", "value"),
        Output("editor-status", "children"),
        Output("editor-status", "style"),
        Input("editor-ws-event", "data"),
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
                options = [{"label": filename, "value": filename} for filename in files]
                return (
                    options,
                    no_update,
                    no_update,
                    no_update,
                    "Список обновлён",
                    {"color": "var(--app-accent)"},
                )

            if msg_type == "file_content":
                content = payload.get("content", "")
                filename = payload.get("filename")
                return (
                    no_update,
                    filename or no_update,
                    content,
                    no_update,
                    "Код открыт",
                    {"color": "var(--app-success)"},
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
                    {"color": "var(--app-warning)"},
                )

            if msg_type == "script_result":
                stdout = payload.get("stdout", "")
                stderr = payload.get("stderr", "")
                exit_code = payload.get("exit_code")
                timed_out = payload.get("timed_out", False)
                duration_ms = payload.get("duration_ms", 0)
                result_text = (
                    f"exit={exit_code} | time={duration_ms} ms | timeout={timed_out}\n\n"
                    f"STDOUT:\n{stdout or '[нет вывода]'}\n\n"
                    f"STDERR:\n{stderr or '[нет ошибок]'}\n"
                )
                return (
                    no_update,
                    no_update,
                    no_update,
                    result_text,
                    "Выполнение завершено" if not timed_out else "Превышен таймаут",
                    {
                        "color": (
                            "var(--app-success)"
                            if exit_code == 0
                            else "var(--app-danger)"
                        )
                    },
                )

            if msg_type == "request_error":
                error_text = payload.get("message", "Неизвестная ошибка")
                return (
                    no_update,
                    no_update,
                    no_update,
                    (current_logs or "") + f"ERROR: {error_text}\n",
                    "Ошибка запроса",
                    {"color": "var(--app-danger)"},
                )

        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        return (no_update,) * 6
