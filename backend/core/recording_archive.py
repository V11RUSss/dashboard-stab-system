"""On-demand, disk-backed plot index for locally recorded, trusted PKL files."""
import json
import math
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from core.mj_decoder import decode, parse_tlv_header, TLV_HEADER_SIZE
from core.telemetry_aggregator import _MessageBucket
from tools.recordings import iter_raw_records


def build_index(source: str, target: str) -> dict:
    # Called in a separate process; never consumes the live event loop.
    temporary = target + '.tmp'
    Path(temporary).unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    db.execute('CREATE TABLE samples(device TEXT, kind TEXT, time REAL, data TEXT)')
    buckets = {}
    def flush(key):
        _, bucket = buckets.pop(key)
        row = bucket.result()
        db.execute('INSERT INTO samples VALUES(?,?,?,?)',
                   (row['device_id'], row['msg_type'], row['timestamp'] * 1000,
                    json.dumps(row, allow_nan=False)))
    try:
        for _, raw in iter_raw_records(Path(source)):
            header = parse_tlv_header(raw)
            if not header or header['msg_type'] not in (1, 2):
                continue
            decoded = decode(header, raw[TLV_HEADER_SIZE:])
            if not decoded:
                continue
            key = (decoded['device_id'], decoded['msg_type'])
            window = decoded['ts_s'] * 10 + decoded['ts_ns'] // 100_000_000
            if key in buckets and buckets[key][0] != window:
                flush(key)
            if key not in buckets:
                buckets[key] = (window, _MessageBucket(decoded))
            else:
                buckets[key][1].add(decoded)
        for key in list(buckets):
            flush(key)
        db.execute('CREATE INDEX sample_time ON samples(device,time)')
        db.commit()
    finally:
        db.close()
    os.replace(temporary, target)
    return metadata(target)


def metadata(target: str) -> dict:
    with closing(sqlite3.connect(target)) as db:
        devices = {device: {'start': start, 'end': end, 'count': count}
                   for device, start, end, count in db.execute(
                       'SELECT device,MIN(time),MAX(time),COUNT(*) FROM samples GROUP BY device')}
    return {'devices': devices}


def read_window(target: str, device: str, start: float, end: float) -> dict:
    if not all(math.isfinite(v) for v in (start, end)) or start >= end:
        raise ValueError('Некорректный интервал записи')
    # Bounded response even for a whole-day view. Keep min/max and weighted mean.
    step = max(100.0, (end - start) / 1500)
    groups = {}
    with closing(sqlite3.connect(target)) as db:
        for time, raw in db.execute(
            'SELECT time,data FROM samples WHERE device=? AND time>=? AND time<=? ORDER BY time',
            (device, start, end)):
            row = json.loads(raw)
            key = (row['msg_type'], int((time - start) // step))
            if key not in groups:
                groups[key] = row
                for stats in row['values'].values():
                    stats['_sin'] = math.sin(stats['mean']) * stats['count']
                    stats['_cos'] = math.cos(stats['mean']) * stats['count']
                continue
            result = groups[key]
            result['timestamp'] = row['timestamp']
            for field, value in row['values'].items():
                old = result['values'].get(field)
                if old is None:
                    result['values'][field] = dict(value, _sin=math.sin(value['mean'])*value['count'], _cos=math.cos(value['mean'])*value['count'])
                    continue
                count = old['count'] + value['count']
                old['mean'] = (old['mean']*old['count'] + value['mean']*value['count']) / count
                old['_sin'] += math.sin(value['mean']) * value['count']
                old['_cos'] += math.cos(value['mean']) * value['count']
                old['count'] = count
                old['last'] = value['last']
                old['out_of_range'] = old.get('out_of_range', False) or value.get('out_of_range', False)
                for name, fn in [('min', min), ('max', max)]:
                    old[name] = fn(old[name], value[name]) if old[name] is not None and value[name] is not None else None
    from core.telemetry_aggregator import CIRCULAR_ANGLE_FIELDS
    for row in groups.values():
        for field, stats in row['values'].items():
            sin, cos = stats.pop('_sin'), stats.pop('_cos')
            if field in CIRCULAR_ANGLE_FIELDS.get(row['msg_type'], set()) and not stats.get('out_of_range'):
                stats['mean'] = math.atan2(sin, cos) if math.hypot(sin, cos) > 1e-12 else stats['last']
                if stats['min'] is not None and stats['max'] - stats['min'] > math.pi:
                    stats['min'] = stats['max'] = None
    return {'series': list(groups.values()), 'resolution_ms': step}
