(function () {
    "use strict";

    const state = window.__bro30GraphState || {
        series: {},
        resetCount: 0,
    };
    window.__bro30GraphState = state;

    function finiteNumber(value) {
        return typeof value === "number" && Number.isFinite(value);
    }

    function seriesKey(deviceId, messageType, parameter) {
        return [deviceId, messageType, parameter].join("|");
    }

    function messageTypeFor(parameter, config) {
        const messageTypes = (config && config.parameter_message_types) || {};
        return messageTypes[parameter] || "dcu_telemetry";
    }

    function seriesDefinitions(parameter, config) {
        const groups = (config && config.parameter_series) || {};
        const configured = groups[parameter];
        if (Array.isArray(configured) && configured.length) {
            return configured;
        }
        return [{field: parameter}];
    }

    function pointsFor(device, parameter, definition, config) {
        const key = seriesKey(
            device,
            messageTypeFor(parameter, config),
            definition.field
        );
        return state.series[key] || [];
    }

    function applyReset(resetClicks) {
        const next = Number(resetClicks || 0);
        if (next <= state.resetCount) {
            return false;
        }
        state.series = {};
        state.resetCount = next;
        return true;
    }

    function snapshotBatch(event, config) {
        const payload = event.payload || {};
        const records = [];
        Object.keys(payload).forEach(function (deviceId) {
            const node = payload[deviceId] || {};
            const messages = node.messages || {};
            const message = messages.bup_state;
            if (!message) {
                return;
            }
            const timestamp = finiteNumber(message.ts_s)
                ? message.ts_s + Number(message.ts_ns || 0) * 1e-9
                : Date.parse(message.timestamp) / 1000;
            if (!finiteNumber(timestamp)) {
                return;
            }
            const values = {};
            const labels = (config && config.parameter_labels) || {};
            Object.keys(labels).forEach(function (parameter) {
                if (messageTypeFor(parameter, config) !== "bup_state") {
                    return;
                }
                seriesDefinitions(parameter, config).forEach(function (definition) {
                    const value = message[definition.field];
                    if (!finiteNumber(value)) {
                        return;
                    }
                    const singleTurnAngle = ["angle", "load_sens_angle"]
                        .includes(definition.field);
                    values[definition.field] = {
                        mean: value,
                        min: value,
                        max: value,
                        last: value,
                        count: 1,
                        out_of_range: singleTurnAngle && Math.abs(value) > Math.PI,
                    };
                });
            });
            if (Object.keys(values).length) {
                records.push({
                    device_id: deviceId,
                    msg_type: "bup_state",
                    timestamp: timestamp,
                    sample_count: 1,
                    values: values,
                });
            }
        });
        return records.length ? [{series: records}] : [];
    }

    function eventBatches(event, config) {
        if (!event || typeof event !== "object") {
            return [];
        }
        if (event.type === "telemetry_batch") {
            return [event.payload || {}];
        }
        if (event.type === "telemetry_history") {
            const payload = event.payload || {};
            return Array.isArray(payload.batches) ? payload.batches : [];
        }
        if (event.type === "devices_snapshot") {
            // bup_state is event-driven.  Its last sample must remain
            // graphable even when it has already fallen out of the rolling
            // high-rate telemetry history before this browser connected.
            return snapshotBatch(event, config);
        }
        return [];
    }

    function ingestBatch(batch, maxBufferPoints, added) {
        const records = Array.isArray(batch.series) ? batch.series : [];
        records.forEach(function (record) {
            if (!record || !record.device_id || !record.msg_type || !finiteNumber(record.timestamp)) {
                return;
            }
            const values = record.values || {};
            Object.keys(values).forEach(function (parameter) {
                const stats = values[parameter] || {};
                if (!finiteNumber(stats.mean)) {
                    return;
                }
                const key = seriesKey(record.device_id, record.msg_type, parameter);
                const point = {
                    // Plotly date axes use Unix milliseconds.  The source is
                    // the MicroJet ts_s + ts_ns device timestamp.
                    x: record.timestamp * 1000,
                    mean: stats.mean,
                    min: finiteNumber(stats.min) ? stats.min : null,
                    max: finiteNumber(stats.max) ? stats.max : null,
                    last: finiteNumber(stats.last) ? stats.last : stats.mean,
                    count: Number(stats.count || record.sample_count || 0),
                    outOfRange: stats.out_of_range === true,
                };
                const buffer = state.series[key] || [];
                const lastBuffered = buffer[buffer.length - 1];
                let appended = true;
                if (lastBuffered && point.x <= lastBuffered.x) {
                    appended = false;
                    const existingIndex = buffer.findIndex(function (item) {
                        return item.x === point.x;
                    });
                    if (existingIndex >= 0) {
                        buffer[existingIndex] = point;
                    } else {
                        const insertAt = buffer.findIndex(function (item) {
                            return item.x > point.x;
                        });
                        buffer.splice(insertAt >= 0 ? insertAt : buffer.length, 0, point);
                    }
                } else {
                    buffer.push(point);
                }
                if (buffer.length > maxBufferPoints) {
                    buffer.splice(0, buffer.length - maxBufferPoints);
                }
                state.series[key] = buffer;
                if (appended && !added[key]) {
                    added[key] = [];
                }
                if (appended) {
                    added[key].push(point);
                }
            });
        });
    }

    function parseMessage(message) {
        if (!message || typeof message.data !== "string") {
            return null;
        }
        try {
            return JSON.parse(message.data);
        } catch (_error) {
            return null;
        }
    }

    function fixed(value) {
        return finiteNumber(value) ? value.toFixed(5) : "—";
    }

    function rgba(hex, alpha) {
        const clean = String(hex || "#10b981").replace("#", "");
        const red = parseInt(clean.slice(0, 2), 16);
        const green = parseInt(clean.slice(2, 4), 16);
        const blue = parseInt(clean.slice(4, 6), 16);
        return "rgba(" + red + ", " + green + ", " + blue + ", " + alpha + ")";
    }

    function colorsForTheme(theme) {
        if (theme === "light") {
            return {
                paper: "#ffffff",
                plot: "#f8f9fa",
                text: "#6c757d",
                grid: "#dee2e6",
            };
        }
        return {
            paper: "#14171c",
            plot: "#101216",
            text: "#9aa1aa",
            grid: "#2b3035",
        };
    }

    function visiblePoints(points, historyPoints) {
        return historyPoints > 0 ? points.slice(-historyPoints) : points.slice();
    }

    function timeAxis(themeColors) {
        return {
            type: "date",
            title: "Время устройства",
            gridcolor: themeColors.grid,
            zerolinecolor: themeColors.grid,
            hoverformat: "%d.%m.%Y %H:%M:%S.%L",
            tickformatstops: [
                {dtickrange: [null, 1000], value: "%H:%M:%S.%L"},
                {dtickrange: [1000, 60000], value: "%H:%M:%S"},
                {dtickrange: [60000, 3600000], value: "%H:%M"},
                {dtickrange: [3600000, null], value: "%d.%m %H:%M"},
            ],
        };
    }

    function scalarTraces(points, lineColor, parameterLabel) {
        const x = points.map(function (point) { return point.x; });
        const minimum = points.map(function (point) { return point.min; });
        const maximum = points.map(function (point) { return point.max; });
        const mean = points.map(function (point) { return point.mean; });
        return [
            {
                type: "scatter",
                mode: "lines",
                x: x,
                y: minimum,
                line: {color: lineColor, width: 0},
                hoverinfo: "skip",
                showlegend: false,
                name: "Минимум",
            },
            {
                type: "scatter",
                mode: "lines",
                x: x,
                y: maximum,
                line: {color: lineColor, width: 0},
                fill: "tonexty",
                fillcolor: rgba(lineColor, 0.16),
                hoverinfo: "skip",
                name: "Диапазон min–max",
            },
            {
                type: "scatter",
                mode: "lines",
                x: x,
                y: mean,
                line: {color: lineColor, width: 2},
                name: "Среднее",
                hovertemplate: "%{x|%d.%m.%Y %H:%M:%S.%L}<br>"
                    + parameterLabel + ": %{y:.5f}<extra></extra>",
            },
        ];
    }

    function groupedTraces(device, parameter, definitions, historyPoints, config) {
        return definitions.map(function (definition) {
            const points = visiblePoints(
                pointsFor(device, parameter, definition, config),
                historyPoints
            );
            return {
                type: "scatter",
                mode: "lines",
                x: points.map(function (point) { return point.x; }),
                y: points.map(function (point) { return point.mean; }),
                line: {color: definition.color || "#10b981", width: 2},
                name: definition.label || definition.field,
                hovertemplate: "%{x|%d.%m.%Y %H:%M:%S.%L}<br>"
                    + (definition.label || definition.field)
                    + ": %{y:.5f} А<extra></extra>",
            };
        });
    }

    function figureFor(device, parameter, theme, historyPoints, config) {
        const definitions = seriesDefinitions(parameter, config);
        const deviceLabels = (config && config.device_labels) || {};
        const parameterLabels = (config && config.parameter_labels) || {};
        const deviceColors = (config && config.device_colors) || {};
        const deviceLabel = deviceLabels[device] || device;
        const parameterLabel = parameterLabels[parameter] || parameter;
        const lineColor = deviceColors[device] || "#10b981";
        const themeColors = colorsForTheme(theme);
        let traces;

        if (definitions.length > 1) {
            traces = groupedTraces(
                device, parameter, definitions, historyPoints, config
            );
        } else {
            const points = visiblePoints(
                pointsFor(device, parameter, definitions[0], config),
                historyPoints
            );
            traces = scalarTraces(points, lineColor, parameterLabel);
        }

        return {
            data: traces,
            layout: {
                paper_bgcolor: themeColors.paper,
                plot_bgcolor: themeColors.plot,
                font: {color: themeColors.text, family: "system-ui, sans-serif", size: 12},
                title: {text: deviceLabel + " — " + parameterLabel, x: 0.01},
                xaxis: timeAxis(themeColors),
                yaxis: {
                    title: parameterLabel,
                    gridcolor: themeColors.grid,
                    zerolinecolor: themeColors.grid,
                },
                margin: {l: 60, r: 20, t: 50, b: 50},
                showlegend: true,
                legend: {orientation: "h", y: 1.08, x: 1, xanchor: "right"},
                uirevision: [device, messageTypeFor(parameter, config), parameter].join("|"),
                annotations: [],
            },
        };
    }

    function withLimit(extension, historyPoints) {
        const limit = Number(historyPoints || 0);
        if (limit > 0) {
            extension.push(limit);
        }
        return extension;
    }

    function scalarExtension(device, parameter, definition, added, historyPoints, config) {
        const key = seriesKey(
            device,
            messageTypeFor(parameter, config),
            definition.field
        );
        const points = added[key] || [];
        if (!points.length) {
            return null;
        }
        const x = points.map(function (point) { return point.x; });
        const minimum = points.map(function (point) { return point.min; });
        const maximum = points.map(function (point) { return point.max; });
        const mean = points.map(function (point) { return point.mean; });
        return {
            value: withLimit(
                [{x: [x, x, x], y: [minimum, maximum, mean]}, [0, 1, 2]],
                historyPoints
            ),
            last: points[points.length - 1],
            buffered: pointsFor(device, parameter, definition, config).length,
        };
    }

    function groupedExtension(device, parameter, definitions, added, historyPoints, config) {
        const messageType = messageTypeFor(parameter, config);
        const additions = definitions.map(function (definition) {
            return added[seriesKey(device, messageType, definition.field)] || [];
        });
        if (!additions.some(function (points) { return points.length > 0; })) {
            return null;
        }
        const latestValues = [];
        let buffered = 0;
        additions.forEach(function (points, index) {
            const definition = definitions[index];
            const allPoints = pointsFor(device, parameter, definition, config);
            buffered = Math.max(buffered, allPoints.length);
            const last = points.length
                ? points[points.length - 1]
                : allPoints[allPoints.length - 1];
            if (last) {
                latestValues.push(
                    (definition.label || definition.field) + " " + fixed(last.mean) + " А"
                );
            }
        });
        return {
            value: withLimit(
                [
                    {
                        x: additions.map(function (points) {
                            return points.map(function (point) { return point.x; });
                        }),
                        y: additions.map(function (points) {
                            return points.map(function (point) { return point.mean; });
                        }),
                    },
                    definitions.map(function (_definition, index) { return index; }),
                ],
                historyPoints
            ),
            latestValues: latestValues,
            buffered: buffered,
        };
    }

    window.dash_clientside = Object.assign({}, window.dash_clientside, {
        bro30Graph: {
            extendTelemetry: function (message, resetClicks, device, parameter, historyPoints, config) {
                const noUpdate = window.dash_clientside.no_update;
                if (applyReset(resetClicks)) {
                    return [noUpdate, ""];
                }

                const event = parseMessage(message);
                const batches = eventBatches(event, config || {});
                if (!batches.length) {
                    return [noUpdate, noUpdate];
                }

                const configLimit = Number((config || {}).max_buffer_points || 216000);
                const added = {};
                batches.forEach(function (batch) {
                    ingestBatch(batch, configLimit, added);
                });

                const definitions = seriesDefinitions(parameter, config || {});
                const grouped = definitions.length > 1;
                const result = grouped
                    ? groupedExtension(
                        device, parameter, definitions, added, historyPoints, config || {}
                    )
                    : scalarExtension(
                        device, parameter, definitions[0], added, historyPoints, config || {}
                    );
                if (!result) {
                    return [noUpdate, noUpdate];
                }

                const limit = Number(historyPoints || 0);
                const buffered = limit > 0
                    ? Math.min(result.buffered, limit)
                    : result.buffered;
                const deviceLabel = ((config || {}).device_labels || {})[device] || device;
                const parameterLabel = ((config || {}).parameter_labels || {})[parameter] || parameter;
                let status = "Устройство: " + deviceLabel
                    + " | Параметр: " + parameterLabel
                    + " | Точек: " + buffered;
                if (grouped) {
                    status += " | Последние средние: " + result.latestValues.join(" · ");
                } else {
                    status += " | Последнее среднее: " + fixed(result.last.mean);
                    if (result.last.outOfRange) {
                        status += " | ⚠ угол вне диапазона −π…π";
                    }
                }

                return [result.value, status];
            },

            buildFigure: function (device, parameter, theme, resetClicks, historyPoints, config) {
                applyReset(resetClicks);
                return figureFor(
                    device,
                    parameter,
                    theme || "dark",
                    Number(historyPoints || 0),
                    config || {}
                );
            },
        },
    });
}());
