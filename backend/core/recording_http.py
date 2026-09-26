"""Loopback-only recording API, proxied by the existing frontend origin."""
import asyncio
import json
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from core.recording_archive import build_index, metadata, read_window


class RecordingAPI:
    def __init__(self, manager, port=8766):
        self.manager = manager
        self.loop = asyncio.get_running_loop()
        self.pool = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context('spawn'))
        self.jobs = {}
        self.lock = threading.Lock()
        self.cache = manager.recordings_dir / '.preview'
        self.cache.mkdir(exist_ok=True)
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.handle_api(False)
            def do_POST(self):
                self.handle_api(True)
            def handle_api(self, post):
                try:
                    if self.headers.get("Origin"):
                        raise ValueError("Use the frontend recording API")
                    parsed = urlparse(self.path)
                    parts = parsed.path.strip('/').split('/')
                    if parts == ['status'] and not post:
                        result = owner.call(owner.status())
                    elif parts == ['recordings'] and not post:
                        result = owner.call(owner.listing())
                    elif parts == ['start'] and post:
                        length = int(self.headers.get('Content-Length', '0'))
                        if not 0 <= length <= 4096:
                            raise ValueError('Слишком длинное название записи')
                        payload = json.loads(self.rfile.read(length) or b'{}')
                        result = owner.call(owner.manager.start_recording(duration_seconds=None, label=payload.get('label', '')))
                    elif parts == ['stop'] and post:
                        result = owner.call(owner.manager.stop_recording(reason='manual'))
                    elif len(parts) == 3 and parts[0] == 'recordings':
                        rid, action = parts[1:]
                        entry, path = owner.call(owner.resolve(rid))
                        if action == 'delete' and post:
                            with owner.lock:
                                job = owner.jobs.get(rid)
                                if job and not job.done():
                                    raise ValueError('Дождитесь подготовки записи')
                                result = owner.call(owner.manager.delete_recording(rid))
                                owner.jobs.pop(rid, None)
                                (owner.cache / (rid + '.sqlite')).unlink(missing_ok=True)
                        elif action == 'download' and not post:
                            with path.open('rb') as source:
                                self.send_response(200)
                                self.send_header('Content-Type', 'application/octet-stream')
                                self.send_header('Content-Length', str(path.stat().st_size))
                                self.send_header('Content-Disposition', f'attachment; filename="{path.name}"')
                                self.end_headers()
                                while chunk := source.read(1024*1024):
                                    self.wfile.write(chunk)
                            return
                        elif action in ('preview', 'window') and not post:
                            with owner.lock:
                                target = owner.cache / (rid + '.sqlite')
                                if not target.exists() and rid not in owner.jobs:
                                    owner.jobs[rid] = owner.pool.submit(build_index, str(path), str(target))
                                job = owner.jobs.get(rid)
                                if job and not job.done():
                                    result = {'state': 'loading'}
                                else:
                                    if job:
                                        job.result()
                                    result = {'state': 'ready', **metadata(str(target)), 'entry': entry}
                                    if action == 'window':
                                        query = parse_qs(parsed.query)
                                        result.update(owner.pool.submit(read_window, str(target), query['device'][0], float(query['start'][0]), float(query['end'][0])).result(timeout=120))
                        else:
                            raise ValueError('Неизвестная операция')
                    else:
                        raise ValueError('Неизвестный адрес')
                    self.reply(200, result)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    self.reply(400, {'error': str(exc)})
            def reply(self, code, data):
                raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=120)

    async def status(self):
        return self.manager.get_status()

    async def listing(self):
        return {'recordings': self.manager.list_recordings()}

    async def resolve(self, rid):
        from tools.recordings import recording_path
        entry = next((r for r in self.manager.list_recordings() if r['id'] == rid), None)
        if not entry:
            raise ValueError('Запись не найдена')
        if entry['status'] in ('recording', 'finalizing'):
            raise ValueError('Сначала остановите запись')
        return entry, recording_path(self.manager.recordings_dir, entry)

    async def close(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        await asyncio.to_thread(self.pool.shutdown, wait=True, cancel_futures=True)
