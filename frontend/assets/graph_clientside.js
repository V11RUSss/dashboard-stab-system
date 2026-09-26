/* Browser-only oscilloscope. Dash owns the plot; this module owns the empty
 * toolbar/sidebar containers; archived windows are fetched independently of live ingress. */
(function () {
    "use strict";
    const state = {buffers: {}, devices: {}, versions: {}, device: null, theme: null,
        dirty: true, controlsDirty: true, lastVersion: -1, revision: 0, lastRange: null};
    let archive = null, archiveBuffers = {}, archiveRequest = "", archiveGeneration = 0, archiveLoading = false, archiveError = "";
    const finite = value => typeof value === "number" && Number.isFinite(value);
    const keyFor = (device, type, field) => [device, type, field].join("|");
    const stamp = value => typeof value === "number" ? value : Date.parse(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value + "Z");
    const iso = value => new Date(value).toISOString().slice(0, 23);
    const color = index => `hsl(${(index * 137.508 + 155) % 360}, 72%, 48%)`;

    function settings(device) {
        const context = archive ? archive.id + ":" + device : device;
        if (!state.devices[context]) state.devices[context] = {
            selected: ["dcu_telemetry:load_sens_angle"], hidden: {}, colors: {}, nextColor: 0,
            axes: {}, width: 10, offset: 0, paused: false, range: null, frozen: null,
            envelope: false, error: "", choice: "dcu_telemetry:load_sens_angle",
        };
        return state.devices[context];
    }
    function lowerBound(points, x) {
        let lo = 0, hi = points.length;
        while (lo < hi) { const mid = (lo + hi) >>> 1; if (points[mid].x < x) lo = mid + 1; else hi = mid; }
        return lo;
    }
    function put(device, type, field, point, limit) {
        const key = keyFor(device, type, field);
        const points = state.buffers[key] || (state.buffers[key] = []);
        if (!points.length || points[points.length - 1].x < point.x) points.push(point);
        else {
            const index = lowerBound(points, point.x);
            if (points[index] && points[index].x === point.x) points[index] = point;
            else points.splice(index, 0, point);
        }
        if (points.length > limit) points.splice(0, points.length - limit);
        state.versions[device] = (state.versions[device] || 0) + 1;
    }
    function ingest(message, config) {
        let event;
        try { event = JSON.parse(message.data); } catch (_) { return; }
        const limit = config.max_buffer_points || 216000;
        let batches = [];
        if (event.type === "telemetry_batch") batches = [event.payload];
        if (event.type === "telemetry_history") batches = event.payload.batches || [];
        if (event.type === "devices_snapshot") {
            Object.entries(event.payload || {}).forEach(([device, node]) => {
                const data = (node.messages || {}).bup_state;
                if (!data) return;
                const x = data.ts_s * 1000 + (data.ts_ns || 0) / 1e6;
                if (!finite(x)) return;
                config.parameters.filter(p => p.message_type === "bup_state").forEach(p => p.fields.forEach(field => {
                    if (!finite(data[field])) return;
                    const points = state.buffers[keyFor(device, "bup_state", field)];
                    // A snapshot must not overwrite an already aggregated window.
                    if (points && points.some(point => point.x === x)) return;
                    put(device, "bup_state", field, {x, mean: data[field], last: data[field],
                        min: data[field], max: data[field], warning: ["angle", "load_sens_angle"].includes(field) && Math.abs(data[field]) > Math.PI}, limit);
                }));
            });
        }
        batches.forEach(batch => (batch.series || []).forEach(record => {
            if (!finite(record.timestamp)) return;
            Object.entries(record.values || {}).forEach(([field, stats]) => {
                if (!finite(stats.mean)) return;
                put(record.device_id, record.msg_type, field, {x: record.timestamp * 1000,
                    mean: stats.mean, min: finite(stats.min) ? stats.min : null,
                    max: finite(stats.max) ? stats.max : null,
                    last: finite(stats.last) ? stats.last : stats.mean,
                    warning: stats.out_of_range === true}, limit);
            });
        }));
    }
    function bounds(device) {
        if (archive) return archive.devices[device] || {start: Infinity, end: -Infinity};
        let start = Infinity, end = -Infinity;
        Object.entries(state.buffers).forEach(([key, points]) => {
            if (key.startsWith(device + "|") && points.length) {
                start = Math.min(start, points[0].x); end = Math.max(end, points[points.length - 1].x);
            }
        });
        return {start, end};
    }
    function rangeFor(device, s) {
        if (s.paused && s.range) return s.range.slice();
        const end = bounds(device).end;
        const right = (finite(end) ? end : Date.now()) - s.offset * 1000;
        return [right - s.width * 1000, right];
    }
    function freeze(device, s, range) {
        s.paused = true; s.range = range.slice(); s.frozen = {};
        Object.entries(state.buffers).forEach(([key, points]) => {
            if (key.startsWith(device + "|")) s.frozen[key] = points.slice();
        });
    }
    function signalColor(s, id) {
        if (!s.colors[id]) s.colors[id] = color(s.nextColor++);
        return s.colors[id];
    }
    function pointsFor(device, s, p, field) {
        return (archive ? archiveBuffers : s.paused && s.frozen ? s.frozen : state.buffers)[keyFor(device, p.message_type, field)] || [];
    }
    function visible(points, range) {
        // Include a boundary neighbour for continuous lines; isolated events have markers.
        const left = lowerBound(points, range[0]); const right = lowerBound(points, range[1]);
        return points.slice(Math.max(0, left - 1), Math.min(points.length, right + 1));
    }
    function figure(device, theme, config) {
        const s = settings(device), range = rangeFor(device, s), traces = [];
        const params = config.parameters.filter(p => s.selected.includes(p.key));
        const units = [...new Set(params.filter(p => p.fields.some(f => !s.hidden[p.key + ":" + f])).map(p => p.unit))];
        const dark = theme !== "light";
        const layout = {
            paper_bgcolor: dark ? "#14171c" : "#ffffff", plot_bgcolor: dark ? "#101216" : "#f8f9fa",
            font: {color: dark ? "#c5cbd3" : "#495057", family: "system-ui, sans-serif", size: 11},
            margin: {l: 65, r: 35, t: 25, b: 55}, showlegend: false, hovermode: "x unified", dragmode: "zoom",
            uirevision: device + ":" + state.revision,
            xaxis: {type: "date", title: {text: "Время устройства · UTC"}, range: range.map(iso), autorange: false,
                domain: [0, Math.max(0.55, 1 - Math.max(0, units.length - 1) * 0.085)],
                gridcolor: dark ? "#2b3035" : "#dee2e6", tickformat: "%H:%M:%S.%L", hoverformat: "%d.%m.%Y %H:%M:%S.%L"},
        };
        units.forEach((unit, index) => {
            const id = index ? "yaxis" + (index + 1) : "yaxis";
            const custom = s.axes[unit];
            layout[id] = {title: {text: unit}, automargin: true, zeroline: false,
                showgrid: index === 0, gridcolor: dark ? "#2b3035" : "#dee2e6",
                autorange: !custom, ...(custom ? {range: custom} : {}),
                ...(index ? {overlaying: "y", side: "right", anchor: "free",
                    position: layout.xaxis.domain[1] + (index - 1) * 0.085} : {}),
            };
        });
        if (!units.length) layout.yaxis = {visible: false};
        params.forEach(p => p.fields.forEach((field, phase) => {
            const id = p.key + ":" + field, c = signalColor(s, id);
            // Plotly uses uid inside CSS selectors during trace removal.
            const uid = id.replace(/:/g, "--");
            if (s.hidden[id]) return;
            const pts = visible(pointsFor(device, s, p, field), range);
            const axisIndex = units.indexOf(p.unit), axis = axisIndex ? "y" + (axisIndex + 1) : "y";
            const name = p.fields.length > 1 ? `Фаза ${"ABC"[phase]}, А (${p.source})` : p.label;
            const base = {x: pts.map(v => iso(v.x)), yaxis: axis, type: "scatter", legendgroup: p.key, showlegend: false};
            if (s.envelope) {
                traces.push({...base, uid: uid + "--min", y: pts.map(v => v.min), mode: "lines", line: {width: 0, color: c}, hoverinfo: "skip"});
                traces.push({...base, uid: uid + "--max", y: pts.map(v => v.max), mode: "lines", line: {width: 0, color: c},
                    fill: "tonexty", fillcolor: c.replace("hsl(", "hsla(").replace(")", ", 0.12)"), hoverinfo: "skip"});
            }
            traces.push({...base, uid, name, y: pts.map(v => v.mean), mode: "lines+markers",
                marker: {size: p.message_type === "bup_state" ? 5 : 2, color: c}, line: {color: c, width: 1.8},
                hovertemplate: name + ": %{y:.5f}<extra></extra>"});
        }));
        if (!traces.length) layout.annotations = [{text: "Добавьте сигнал справа", x: 0.5, y: 0.5, xref: "paper", yref: "paper", showarrow: false}];
        state.lastRange = range.slice();
        return {data: traces, layout};
    }

    const element = (tag, text, cls) => { const node = document.createElement(tag); if (text) node.textContent = text; if (cls) node.className = cls; return node; };
    function button(text, callback, cls = "btn btn-sm btn-outline-secondary") {
        const node = element("button", text, cls); node.type = "button"; node.addEventListener("click", callback); return node;
    }
    function input(label, type, value, parent) {
        const wrap = element("label", null, "scope-field"), caption = element("span", label);
        const node = element("input", null, "form-control form-control-sm"); node.type = type; node.value = value;
        wrap.append(caption, node); parent.append(wrap); return node;
    }
    function changed(controls = false) { state.dirty = true; state.controlsDirty ||= controls; state.revision++; }
    function mount(device, config) {
        const toolbar = document.getElementById("scope-toolbar"), sidebar = document.getElementById("scope-signals");
        if (!toolbar || !sidebar) return false;
        const s = settings(device);
        toolbar.replaceChildren(); sidebar.replaceChildren(); toolbar.className = "surface scope-toolbar";
        const width = input("Окно, с", "number", Number(s.width.toFixed(3)), toolbar); width.min = "0.1"; width.step = "0.1";
        const offset = input("Назад, с", "number", Number(s.offset.toFixed(3)), toolbar); offset.min = "0"; offset.step = "0.1";
        const applyWindow = () => {
            const w = Number(width.value), o = Number(offset.value);
            if (!width.value || !offset.value || !finite(w) || w < 0.1 || !finite(o) || o < 0) {
                s.error = "Окно должно быть ≥ 0,1 с, смещение — ≥ 0."; changed(); return;
            }
            const anchor = archive ? bounds(device).end : s.paused && s.range ? s.range[1] + s.offset * 1000 : bounds(device).end;
            s.width = w; s.offset = o; s.error = "";
            if (s.paused) { const right = (finite(anchor) ? anchor : Date.now()) - o * 1000; s.range = [right - w * 1000, right]; }
            changed(true);
        };
        toolbar.append(button("Применить окно", applyWindow));
        if (!archive) {
        toolbar.append(button(s.paused ? "Продолжить" : "Пауза", () => {
            if (s.paused) { s.paused = false; s.frozen = null; s.range = null; }
            else freeze(device, s, rangeFor(device, s));
            s.error = ""; changed(true);
        }, "btn btn-sm btn-primary"));
        toolbar.append(button("К последним данным", () => { s.paused = false; s.frozen = null; s.range = null; s.offset = 0; s.error = ""; changed(true); }));
        } else {
            const limits = bounds(device);
            const move = left => {
                const widthMs = s.width * 1000;
                left = Math.max(limits.start, Math.min(left, Math.max(limits.start, limits.end - widthMs)));
                s.range = [left, left + widthMs]; s.offset = Math.max(0, (limits.end - s.range[1]) / 1000); changed(true);
            };
            toolbar.append(button("В начало", () => move(limits.start)),
                button("← Окно", () => move(s.range[0] - s.width * 1000)),
                button("Окно →", () => move(s.range[0] + s.width * 1000)),
                button("В конец", () => move(limits.end - s.width * 1000)),
                button("Вся запись", () => { s.range = [limits.start, Math.max(limits.start + 100, limits.end)]; s.width = (s.range[1] - s.range[0])/1000; s.offset = 0; changed(true); }));
            if (finite(limits.start)) {
                const seek = input("Позиция в записи", "range", s.range[0], toolbar);
                seek.min = limits.start; seek.max = Math.max(limits.start, limits.end - s.width * 1000); seek.step = 1;
                seek.onchange = () => move(Number(seek.value));
            }
        }
        if (archive && !finite(bounds(device).start)) toolbar.querySelectorAll("button").forEach(b => { b.disabled = true; });
        const envelope = element("label", null, "scope-check"); const check = element("input"); check.type = "checkbox"; check.checked = s.envelope;
        check.onchange = () => { s.envelope = check.checked; changed(); }; envelope.append(check, document.createTextNode("Полоса min–max")); toolbar.append(envelope);
        const interval = element("div", null, "scope-exact");
        const range = rangeFor(device, s);
        const from = input("От · UTC", "datetime-local", iso(range[0]), interval); from.step = "0.001";
        const to = input("До · UTC", "datetime-local", iso(range[1]), interval); to.step = "0.001";
        from.disabled = to.disabled = !s.paused;
        const exact = button("Показать интервал", () => {
            const left = stamp(from.value), right = stamp(to.value);
            if (!finite(left) || !finite(right) || left >= right) { s.error = "Укажите корректный интервал: «От» раньше «До»."; changed(); return; }
            s.range = [left, right]; s.width = (right - left) / 1000; s.error = ""; changed(true);
        }); exact.disabled = !s.paused; interval.append(exact); toolbar.append(interval);
        sidebar.append(element("h2", "Сигналы", "scope-title"));
        const select = element("select", null, "form-select form-select-sm"); select.setAttribute("aria-label", "Добавляемый параметр");
        ["dcu_tel", "bup_state"].forEach(source => {
            const group = element("optgroup"); group.label = source === "dcu_tel" ? "dcu_tel · подробная телеметрия" : "bup_state · состояние привода";
            config.parameters.filter(p => p.source === source).forEach(p => { const option = element("option", p.label); option.value = p.key; option.title = p.description; option.disabled = s.selected.includes(p.key); group.append(option); }); select.append(group);
        });
        const available = config.parameters.filter(p => !s.selected.includes(p.key));
        select.value = available.some(p => p.key === s.choice) ? s.choice : (available[0] || {}).key || "";
        select.onchange = () => { s.choice = select.value; };
        sidebar.append(select);
        const add = button("+ Добавить сигнал", () => { if (select.value && !s.selected.includes(select.value)) s.selected.push(select.value); changed(true); }, "btn btn-sm btn-outline-primary scope-add");
        add.disabled = !available.length; sidebar.append(add);
        s.selected.forEach(key => {
            const p = config.parameters.find(item => item.key === key); if (!p) return;
            const card = element("section", null, "scope-signal"); card.title = p.description;
            const header = element("div", null, "scope-signal-heading"); header.append(element("strong", p.label));
            header.append(button("×", () => { s.selected = s.selected.filter(item => item !== key); changed(true); }, "scope-remove")); header.lastChild.setAttribute("aria-label", "Удалить " + p.label); card.append(header);
            p.fields.forEach((field, phase) => {
                const id = p.key + ":" + field, row = element("label", null, "scope-signal-value");
                const visible = element("input"); visible.type = "checkbox"; visible.checked = !s.hidden[id]; visible.setAttribute("aria-label", "Показать " + field);
                visible.onchange = () => { s.hidden[id] = !visible.checked; changed(true); };
                const swatch = element("span", null, "scope-swatch"); swatch.style.background = signalColor(s, id);
                const label = element("span", p.fields.length > 1 ? "Фаза " + "ABC"[phase] : p.field);
                const value = element("output", "—"); value.dataset.signal = id;
                row.append(visible, swatch, label, value); card.append(row);
            }); sidebar.append(card);
        });
        if (!s.selected.length) sidebar.append(element("p", "Выберите параметр и нажмите «Добавить сигнал».", "scope-note"));
        sidebar.append(element("h2", "Вертикальные шкалы", "scope-title"));
        const units = [...new Set(config.parameters.filter(p => s.selected.includes(p.key)).map(p => p.unit))];
        units.forEach(unit => {
            const panel = element("div", null, "scope-axis"); panel.append(element("strong", unit));
            const mode = element("select", null, "form-select form-select-sm"); mode.setAttribute("aria-label", "Шкала " + unit);
            ["Авто", "Вручную"].forEach((label, i) => { const option = element("option", label); option.value = String(i); mode.append(option); }); mode.value = s.axes[unit] ? "1" : "0";
            panel.append(mode);
            const minimum = input("Минимум", "number", s.axes[unit] ? s.axes[unit][0] : "", panel);
            const maximum = input("Максимум", "number", s.axes[unit] ? s.axes[unit][1] : "", panel);
            minimum.step = maximum.step = "any"; minimum.disabled = maximum.disabled = !s.axes[unit];
            mode.onchange = () => { minimum.disabled = maximum.disabled = mode.value === "0"; if (mode.value === "0") { delete s.axes[unit]; s.error = ""; changed(); } };
            panel.append(button("Применить", () => {
                if (mode.value === "0") delete s.axes[unit];
                else if (!minimum.value || !maximum.value || !finite(Number(minimum.value)) || !finite(Number(maximum.value)) || Number(minimum.value) >= Number(maximum.value)) {
                    s.error = "Для шкалы " + unit + " минимум должен быть меньше максимума."; changed(); return;
                } else s.axes[unit] = [Number(minimum.value), Number(maximum.value)];
                s.error = ""; changed();
            })); sidebar.append(panel);
        });
        sidebar.append(element("p", "Одна шкала на единицу измерения. Значения справа — последние в показанном интервале.", "scope-note"));
        return true;
    }
    function updateValues(device, config, range) {
        const s = settings(device);
        document.querySelectorAll("#scope-signals output[data-signal]").forEach(node => {
            const p = config.parameters.find(p => node.dataset.signal.startsWith(p.key + ":")); if (!p) return;
            const field = node.dataset.signal.slice(p.key.length + 1), points = pointsFor(device, s, p, field);
            let index = lowerBound(points, range[1]); if (!points[index] || points[index].x > range[1]) index--;
            const last = points[index];
            node.textContent = last && last.x >= range[0] ? last.last.toFixed(4) + " " + p.unit : "—";
            node.title = last ? "Последнее значение: " + iso(last.x) + " UTC" + (last.warning ? " · ⚠ угол вне −π…π" : "") : "Нет данных";
            node.className = last && last.warning ? "scope-value-warning" : "";
        });
    }
    function render(tick, device, theme, relayout, config) {
        if (window.bro30Recording) window.bro30Recording.tick();
        const no = window.dash_clientside.no_update;
        if (!config || !device) return [no, no];
        const s = settings(device);
        if (archive && !s.paused) {
            const limits = bounds(device), left = finite(limits.start) ? limits.start : Date.now();
            s.paused = true; s.frozen = null; s.range = [left, left + s.width * 1000];
            s.offset = finite(limits.end) ? Math.max(0, (limits.end - s.range[1]) / 1000) : 0;
        }
        const trigger = ((window.dash_clientside.callback_context || {}).triggered || [])[0];
        const switched = state.device !== device;
        if (switched || state.theme !== theme) { state.device = device; state.theme = theme; state.lastRange = null; changed(true); }
        if (!switched && trigger && trigger.prop_id === "live-graph.relayoutData" && relayout) {
            const raw = relayout["xaxis.range"] || [relayout["xaxis.range[0]"], relayout["xaxis.range[1]"]];
            if (raw[0] !== undefined && raw[1] !== undefined) {
                const range = raw.map(stamp);
                if (range.every(finite) && range[0] < range[1] && (!state.lastRange || range.some((x, i) => Math.abs(x - state.lastRange[i]) > 1))) {
                    if (!s.paused && !archive) freeze(device, s, range); else s.range = range;
                    s.width = (range[1] - range[0]) / 1000; s.error = ""; changed(true);
                }
            }
        }
        // Dash tabs can unmount/recreate their content without reloading assets.
        const toolbar = document.getElementById("scope-toolbar");
        if (toolbar && !toolbar.children.length) state.controlsDirty = true;
        if (state.controlsDirty && mount(device, config)) { state.controlsDirty = false; state.dirty = true; }
        if (archive) fetchArchiveWindow(device, s);
        const version = archive ? 0 : state.versions[device] || 0;
        if (!state.dirty && (s.paused || state.lastVersion === version)) return [no, no];
        const result = figure(device, theme, config), range = state.lastRange;
        updateValues(device, config, range);
        const limits = bounds(device);
        const outside = finite(limits.start) && range[0] < limits.start ? " · начало интервала раньше доступной истории" : "";
        const status = archiveError || s.error || ((archive ? (archiveLoading ? "Загрузка интервала записи…" : "Запись · " + archive.name) : s.paused ? "Пауза" : "Слежение") + " · " + iso(range[0]).replace("T", " ") + " — " + iso(range[1]).replace("T", " ") + " UTC" + (finite(limits.end) ? outside : " · ожидание данных"));
        state.dirty = false; state.lastVersion = version;
        return [result, status];
    }
    function fetchArchiveWindow(device, s) {
        const range = rangeFor(device, s), key = JSON.stringify([archive.id, device, range]);
        if (key === archiveRequest || archiveLoading) return;
        archiveRequest = key; archiveBuffers = {}; archiveError = "";
        const generation = ++archiveGeneration, id = archive.id;
        if (!archive.devices[device]) { archiveLoading = false; state.dirty = true; return; }
        archiveLoading = true; state.dirty = true;
        const query = new URLSearchParams({device, start: range[0], end: range[1]});
        fetch('/api/recordings/recordings/' + id + '/window?' + query, {cache: 'no-store'})
            .then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data.error); return data; })
            .then(data => {
                if (generation !== archiveGeneration) return;
                for (const row of data.series || []) {
                    for (const [field, stats] of Object.entries(row.values)) {
                        if (!finite(stats.mean)) continue;
                        const key = keyFor(row.device_id, row.msg_type, field);
                        (archiveBuffers[key] ||= []).push({x: row.timestamp * 1000, mean: stats.mean,
                            min: stats.min, max: stats.max, last: stats.last, warning: stats.out_of_range});
                    }
                }
                Object.values(archiveBuffers).forEach(points => points.sort((a, b) => a.x - b.x));
            }).catch(error => { if (generation === archiveGeneration) archiveError = 'Не удалось открыть интервал: ' + error.message; })
            .finally(() => { if (generation === archiveGeneration) { archiveLoading = false; state.dirty = true; } });
    }
    window.bro30Scope = {
        archiveId: () => archive?.id || null,
        useLive: () => { archive = null; archiveBuffers = {}; archiveRequest = ''; archiveGeneration++; archiveLoading = false; archiveError = ''; state.lastRange = null; if (state.device) { const s = settings(state.device); s.paused = false; s.frozen = null; s.range = null; s.offset = 0; } changed(true); },
        useArchive: (id, data) => {
            archive = {id, devices: data.devices, name: String(data.entry.path).split('/').pop()};
            archiveBuffers = {}; archiveRequest = ''; archiveGeneration++; archiveLoading = false; archiveError = ''; state.lastRange = null;
            changed(true);
        },
    };
    window.dash_clientside = Object.assign({}, window.dash_clientside, {bro30Graph: {
        ingest: function (message, config) { if (config) ingest(message, config); return window.dash_clientside.no_update; }, render,
    }});
    // Pure data helpers exposed only to the Node test runner.
    if (typeof module !== "undefined") module.exports = {state, settings, ingest, figure, freeze, rangeFor, bounds, lowerBound};
}());
