/* Run with node --test frontend/tests/graph_clientside.test.cjs */
const {test} = require('node:test');
const assert = require('node:assert/strict');
global.window = {dash_clientside: {no_update: Symbol('no update')}};
const scope = require('../assets/graph_clientside.js');
const parameters = [
    {key: 'dcu_telemetry:load_sens_angle', message_type: 'dcu_telemetry', source: 'dcu_tel', field: 'load_sens_angle', fields: ['load_sens_angle'], unit: 'рад', label: 'Angle'},
    {key: 'dcu_telemetry:phase_currents', message_type: 'dcu_telemetry', source: 'dcu_tel', field: 'phase_currents', fields: ['current_a', 'current_b', 'current_c'], unit: 'А', label: 'ABC'},
    {key: 'bup_state:angle', message_type: 'bup_state', source: 'bup_state', field: 'angle', fields: ['angle'], unit: 'рад', label: 'BUP angle'},
];
const config = {parameters, max_buffer_points: 5};
function send(device, time, values, type = 'dcu_telemetry') {
    scope.ingest({data: JSON.stringify({type: 'telemetry_batch', payload: {series: [{device_id: device, msg_type: type, timestamp: time,
        values: Object.fromEntries(Object.entries(values).map(([key, value]) => [key, {mean: value, min: value - 1, max: value + 1, last: value}]))}]}})}, config);
}
function range(fig) { return fig.layout.xaxis.range.map(x => Date.parse(x + 'Z')); }

test('rolling window uses time, supports offset and isolates devices', () => {
    send('left', 100, {load_sens_angle: 1}); send('right', 200, {load_sens_angle: 9});
    const s = scope.settings('left'); s.width = 4; s.offset = 2;
    assert.deepEqual(range(scope.figure('left', 'dark', config)), [94000, 98000]);
    assert.deepEqual(scope.figure('left', 'dark', config).data[0].y, [1]);
    assert.deepEqual(scope.figure('right', 'dark', config).data[0].y, [9]);
});
test('ABC has three independently hidden traces with stable unique colors and shared current axis', () => {
    const s = scope.settings('abc'); s.selected.push(parameters[1].key);
    send('abc', 100, {load_sens_angle: 1, current_a: 2, current_b: 3, current_c: 4});
    const fig = scope.figure('abc', 'light', config);
    assert.equal(fig.data.length, 4); assert.equal(new Set(fig.data.map(t => t.line.color)).size, 4);
    assert.equal(fig.data[0].x[0], "1970-01-01T00:01:40.000");
    assert.deepEqual(fig.data.map(t => t.yaxis), ['y', 'y2', 'y2', 'y2']);
    assert.ok(fig.data.every(t => /^[a-zA-Z0-9_-]+$/.test(t.uid)));
    const colors = fig.data.map(t => t.line.color);
    s.hidden[parameters[1].key + ':current_b'] = true;
    const hidden = scope.figure('abc', 'light', config);
    assert.equal(hidden.data.length, 3); assert.equal(hidden.data[2].line.color, colors[3]);
    s.axes['А'] = [-5, 5]; assert.deepEqual(scope.figure('abc', 'light', config).layout.yaxis2.range, [-5, 5]);
});
test('pause keeps frozen samples even when live history is replaced/evicted', () => {
    send('paused', 100, {load_sens_angle: 2});
    const s = scope.settings('paused'); scope.freeze('paused', s, [99000, 101000]);
    send('paused', 100, {load_sens_angle: 8});
    for (let i = 101; i < 115; i++) send('paused', i, {load_sens_angle: i});
    assert.deepEqual(scope.figure('paused', 'dark', config).data[0].y, [2]);
    assert.deepEqual(range(scope.figure('paused', 'dark', config)), [99000, 101000]);
    s.paused = false; s.frozen = null;
    assert.equal(scope.figure('paused', 'dark', config).data[0].y.at(-1), 114);
    assert.equal(scope.state.buffers['paused|dcu_telemetry|load_sens_angle'].length, 5);
});
test('rare BUP snapshot is restored, distinct from DCU, with no fabricated zeros', () => {
    scope.ingest({data: JSON.stringify({type: 'devices_snapshot', payload: {rare: {messages: {bup_state: {ts_s: 99, ts_ns: 0, angle: 3}}}}})}, config);
    send('rare', 100, {load_sens_angle: 4});
    const s = scope.settings('rare'); s.selected.push('bup_state:angle');
    const fig = scope.figure('rare', 'dark', config);
    assert.deepEqual(fig.data.map(t => t.y), [[4], [3]]);
    assert.equal(fig.data[1].mode, 'lines+markers');
    send('rare', 102, {load_sens_angle: 6});
    assert.deepEqual(scope.figure('rare', 'dark', config).data[1].y, [3]);
});
test('out-of-order history is sorted and duplicate timestamps replaced; angular envelopes retain gaps', () => {
    send('order', 100, {load_sens_angle: 1}); send('order', 98, {load_sens_angle: 2}); send('order', 100, {load_sens_angle: 3});
    assert.deepEqual(scope.state.buffers['order|dcu_telemetry|load_sens_angle'].map(p => p.x), [98000, 100000]);
    scope.ingest({data: JSON.stringify({type: 'telemetry_batch', payload: {series: [{device_id: 'order', msg_type: 'dcu_telemetry', timestamp: 101, values: {load_sens_angle: {mean: 3.14, min: null, max: null}}}]}})}, config);
    scope.settings('order').envelope = true;
    const fig = scope.figure('order', 'dark', config);
    assert.equal(fig.data.length, 3); assert.equal(fig.data[0].y.at(-1), null);
});

test('archive selection cannot mix with live data; returning to live restores ingress', () => {
    send('source-test', 100, {load_sens_angle: 8});
    window.bro30Scope.useArchive('recording-test', {devices: {'source-test': {start: 1000, end: 2000}}, entry: {path: 'test.pkl'}});
    assert.deepEqual(scope.bounds('source-test'), {start: 1000, end: 2000});
    assert.deepEqual(scope.figure('source-test', 'dark', config).data[0].y, []);
    send('source-test', 101, {load_sens_angle: 9});
    assert.deepEqual(scope.figure('source-test', 'dark', config).data[0].y, []);
    scope.settings('source-test').width = 1;
    window.bro30Scope.useLive();
    assert.equal(scope.settings('source-test').width, 10);
    assert.deepEqual(scope.figure('source-test', 'dark', config).data[0].y, [8, 9]);
});
