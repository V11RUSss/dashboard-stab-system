(function () {
    "use strict";

    function envelope(message) {
        if (!message) {
            return null;
        }
        try {
            return JSON.parse(message.data || message);
        } catch (_error) {
            return null;
        }
    }

    function duration(seconds) {
        const value = Math.max(0, Math.floor(Number(seconds) || 0));
        const hours = Math.floor(value / 3600);
        const minutes = Math.floor((value % 3600) / 60);
        const rest = value % 60;
        const pairs = hours > 0 ? [hours, minutes, rest] : [minutes, rest];
        return pairs.map(function (part) {
            return String(part).padStart(2, "0");
        }).join(":");
    }

    function megabytes(value) {
        const bytes = Number(value || 0);
        if (bytes >= 1024 * 1024 * 1024) {
            return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " ГБ";
        }
        return (bytes / (1024 * 1024)).toFixed(1) + " МБ";
    }

    function request(action, payload) {
        return JSON.stringify({
            action: action,
            payload: payload || {},
            request_id: "recording-" + Date.now(),
        });
    }

    window.dash_clientside = Object.assign({}, window.dash_clientside, {
        bro30Recording: {
            receiveStatus: function (message, currentStatus) {
                const noUpdate = window.dash_clientside.no_update;
                const event = envelope(message);
                if (!event) {
                    return noUpdate;
                }
                if (event.type === "recording_status") {
                    return event.payload || {state: "idle"};
                }
                if (
                    event.type === "request_error"
                    && event.request_id
                    && String(event.request_id).startsWith("recording-")
                ) {
                    const previous = currentStatus || {state: "idle"};
                    return Object.assign({}, previous, {
                        last_error: (event.payload || {}).message || "Ошибка записи",
                    });
                }
                return noUpdate;
            },

            renderStatus: function (status) {
                const current = status || {state: "idle"};
                const state = current.state || "idle";
                const base = "btn btn-sm recording-button ";
                let details = "";
                if (current.last_error) {
                    details = "Ошибка: " + current.last_error;
                }

                if (state === "recording") {
                    const started = Date.parse(current.started_at);
                    const planned = Date.parse(current.planned_end_at);
                    const now = Date.now();
                    const clock = Number.isFinite(planned)
                        ? "осталось " + duration((planned - now) / 1000)
                        : duration((now - started) / 1000);
                    details = "Пакетов: " + Number(current.packets_captured || 0).toLocaleString("ru-RU")
                        + " · записано: " + megabytes(current.file_size_bytes);
                    return [
                        "● Идёт запись · " + clock,
                        base + "recording-button-active",
                        "Нажмите, чтобы остановить и сохранить запись",
                        false,
                        details,
                    ];
                }
                if (state === "finalizing") {
                    details = "Дописывается очередь: "
                        + Number(current.queue_depth || 0).toLocaleString("ru-RU")
                        + " пакетов";
                    return [
                        "Сохранение…",
                        base + "recording-button-finalizing",
                        "Идёт безопасное завершение файла",
                        true,
                        details,
                    ];
                }
                return [
                    "Начать запись",
                    base + "btn-outline-primary",
                    current.last_error || "Записать сырую телеметрию в отдельный PKL",
                    false,
                    details,
                ];
            },

            handleControls: function (
                _toggleClicks,
                _startClicks,
                _cancelClicks,
                status,
                selectedDuration,
                label,
                panelHidden
            ) {
                const noUpdate = window.dash_clientside.no_update;
                const context = window.dash_clientside.callback_context;
                const triggered = context && context.triggered && context.triggered[0];
                const triggerId = triggered
                    ? triggered.prop_id.split(".")[0]
                    : null;
                const state = (status || {}).state || "idle";

                if (triggerId === "recording-status-store") {
                    return [noUpdate, state !== "idle" ? true : panelHidden, noUpdate];
                }
                if (triggerId === "recording-toggle-btn") {
                    if (state === "recording") {
                        return [request("stop_recording"), true, noUpdate];
                    }
                    if (state === "finalizing") {
                        return [noUpdate, true, noUpdate];
                    }
                    return [noUpdate, !panelHidden, noUpdate];
                }
                if (triggerId === "recording-start-btn") {
                    const seconds = selectedDuration === "manual"
                        ? null
                        : Number(selectedDuration);
                    return [
                        request("start_recording", {
                            duration_seconds: seconds,
                            label: String(label || "").trim(),
                        }),
                        true,
                        "",
                    ];
                }
                if (triggerId === "recording-cancel-btn") {
                    return [noUpdate, true, noUpdate];
                }
                return [noUpdate, panelHidden, noUpdate];
            },
        },
    });
}());
