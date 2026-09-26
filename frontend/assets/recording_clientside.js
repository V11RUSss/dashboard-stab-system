/* Recording controls use a low-rate, same-origin API independent of telemetry. */
(function () {
    'use strict';
    const state = {status: {state: 'idle'}, busy: false, polling: false, lastPoll: 0,
        label: '', list: [], selected: '', mode: 'live', loading: false, loadId: 0, message: '', dirty: true, lastCompleted: null};
    const api = async (path, method = 'GET', body) => {
        const response = await fetch('/api/recordings/' + path, {method, cache: 'no-store',
            headers: {'X-Requested-With': 'recordings', 'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body)});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Ошибка работы с записью');
        return data;
    };
    const el = (tag, text, cls) => { const node = document.createElement(tag); node.textContent = text || ''; if (cls) node.className = cls; return node; };
    const button = (text, action, cls = 'btn btn-sm btn-outline-secondary') => {
        const node = el('button', text, cls); node.type = 'button'; node.onclick = action; return node;
    };
    const name = entry => (entry.label ? entry.label + ' · ' : '') + String(entry.path || '').split('/').pop();
    async function refresh() {
        try {
            state.list = (await api('recordings')).recordings.filter(r => !['recording', 'finalizing'].includes(r.status));
            if (!state.list.some(r => r.id === state.selected)) state.selected = state.list[0]?.id || '';
            state.dirty = true;
        } catch (error) { state.message = error.message; state.dirty = true; }
    }
    async function toggle() {
        if (state.busy || state.status.state === 'finalizing') return;
        state.busy = true; paintStatus();
        try {
            state.status = await api(state.status.state === 'recording' ? 'stop' : 'start', 'POST', {label: state.label});
            state.message = ''; await refresh();
            if (state.status.state === 'idle' && state.status.last_completed) state.selected = state.status.last_completed.id;
        } catch (error) { state.message = error.message; }
        finally { state.busy = false; state.dirty = true; paintStatus(); }
    }
    function live() {
        state.mode = 'live'; state.loadId++; state.loading = false; state.message = ''; state.dirty = true;
        window.bro30Scope.useLive();
    }
    async function open() {
        if (!state.selected) return;
        const id = state.selected, token = ++state.loadId;
        state.loading = true; state.message = 'Подготовка записи…'; state.dirty = true;
        try {
            while (token === state.loadId) {
                const data = await api('recordings/' + id + '/preview');
                if (token !== state.loadId) return;
                if (data.state === 'ready') {
                    window.bro30Scope.useArchive(id, data);
                    state.mode = 'archive'; state.loading = false;
                    state.message = Object.keys(data.devices).length ? 'Открыта: ' + name(data.entry) : 'В записи нет данных для графика';
                    if (data.entry.incomplete) state.message += ' · запись неполная';
                    state.dirty = true; return;
                }
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
        } catch (error) {
            if (token === state.loadId) { state.loading = false; state.message = error.message; state.dirty = true; }
        }
    }
    async function remove() {
        const entry = state.list.find(r => r.id === state.selected);
        if (!entry || !window.confirm('Удалить запись «' + name(entry) + '» из списка?')) return;
        try {
            await api('recordings/' + entry.id + '/delete', 'POST');
            if (window.bro30Scope.archiveId() === entry.id) live();
            state.message = 'Запись удалена'; await refresh();
        } catch (error) { state.message = error.message; state.dirty = true; }
    }
    function paintStatus() {
        const node = document.getElementById('recording-toggle-btn'); if (!node) return;
        const current = state.status, active = current.state === 'recording', finalizing = current.state === 'finalizing';
        node.textContent = state.busy ? 'Подождите…' : finalizing ? 'Сохранение…' : active ? '■ Остановить запись' : '● Начать запись';
        node.disabled = state.busy || finalizing;
        const labelInput = document.getElementById('recording-label');
        if (labelInput) labelInput.disabled = state.busy || active || finalizing;
        node.className = 'btn btn-sm recording-button ' + (active ? 'recording-button-active' : 'btn-outline-primary');
        const detail = document.getElementById('recording-current-status');
        if (detail) detail.textContent = current.last_error || (active ? 'Идёт запись · ' + Math.max(0, Math.floor((Date.now() - Date.parse(current.started_at))/1000)) + ' с' : finalizing ? 'Сохраняем файл…' : current.last_completed ? 'Сохранено: ' + name(current.last_completed) : '');
    }
    function mount() {
        const controls = document.getElementById('recording-controls'), library = document.getElementById('recording-library');
        if (!controls || !library) return;
        if (!controls.children.length) {
            const toggleButton = button('Начать запись', toggle); toggleButton.id = 'recording-toggle-btn';
            const detail = el('div', '', 'scope-note'); detail.id = 'recording-current-status'; detail.setAttribute('role', 'status');
            const labelInput = el('input', '', 'form-control form-control-sm');
            labelInput.id = 'recording-label'; labelInput.type = 'text'; labelInput.maxLength = 80;
            labelInput.placeholder = 'Название записи'; labelInput.setAttribute('aria-label', 'Название записи');
            labelInput.value = state.label; labelInput.style.cssText = 'width:240px;max-width:100%';
            labelInput.oninput = () => { state.label = labelInput.value; };
            controls.append(labelInput, toggleButton, detail);
        }
        if (!state.dirty && library.children.length) return;
        state.dirty = false; library.replaceChildren();
        const modes = el('div', '', 'recording-modes');
        const liveButton = button('Прямой эфир', live), archiveButton = button('Записи', () => { state.mode = 'archive'; state.dirty = true; refresh(); });
        [liveButton, archiveButton].forEach((b, i) => { b.setAttribute('aria-pressed', String((state.mode === 'live') === (i === 0))); });
        modes.append(liveButton, archiveButton); library.append(modes);
        if (state.mode === 'archive') {
            const row = el('div', '', 'recording-library-actions');
            const select = el('select', '', 'form-select form-select-sm'); select.setAttribute('aria-label', 'Сохранённая запись');
            if (!state.list.length) { const option = el('option', 'Сохранённых записей пока нет'); option.value = ''; select.append(option); }
            state.list.forEach(entry => { const option = el('option', name(entry)); option.value = entry.id; select.append(option); });
            select.value = state.selected;
            select.onchange = () => { state.selected = select.value; state.loadId++; state.loading = false; state.dirty = true; };
            const show = button(state.loading ? 'Подготовка…' : 'Открыть', open); show.disabled = !state.selected || state.loading;
            const download = el('a', 'Скачать PKL', 'btn btn-sm btn-outline-secondary');
            if (state.selected) { download.href = '/api/recordings/recordings/' + state.selected + '/download'; download.setAttribute('download', ''); }
            else download.setAttribute('aria-disabled', 'true');
            const del = button('Удалить', remove, 'btn btn-sm btn-outline-danger'); del.disabled = !state.selected || state.loading;
            row.append(select, show, download, del, button('Обновить список', refresh)); library.append(row);
            library.append(el('div', state.message || (window.bro30Scope.archiveId() ? '' : 'Выберите запись и нажмите «Открыть». Пока отображается прямой эфир.'), 'scope-note'));
        } else if (state.message) library.append(el('div', state.message, 'scope-note'));
    }
    window.bro30Recording = {
        tick: function () {
            mount(); paintStatus();
            if (!state.polling && Date.now() - state.lastPoll > 1000) {
                state.polling = true; state.lastPoll = Date.now();
                api('status').then(status => {
                    state.status = status;
                    const done = status.last_completed?.id;
                    if (done !== state.lastCompleted) { state.lastCompleted = done; refresh(); }
                }).catch(error => { state.status = {...state.status, last_error: error.message}; })
                .finally(() => { state.polling = false; paintStatus(); });
            }
        },
    };
}());
