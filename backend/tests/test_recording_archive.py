import json
import pickle
import struct
import tempfile
import unittest
from pathlib import Path

from core.recording_archive import build_index, read_window
from core.recording_manager import RECORDING_FORMAT, RecordingManager
from core.mj_decoder import TLV_HEADER_FMT
from tests.test_recording_manager import make_frame


class ArchiveTests(unittest.TestCase):
    def test_index_decodes_source_time_devices_and_bounded_windows(self):
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root)/'test.pkl', Path(root)/'test.sqlite'
            records = []
            for index in range(4000):
                frame, _ = make_frame()
                payload = bytearray(frame[15:])
                struct.pack_into('<f', payload, 4, 1.0 if index % 2 else 2.0)
                header = struct.pack(TLV_HEADER_FMT, 1, 0xFA82, 0x6C99, 100+index//10, (index%10)*100_000_000, len(payload))
                records.append((index, header+payload))
            with source.open('wb') as out:
                pickle.dump({'format': RECORDING_FORMAT, 'records': records}, out)
            info = build_index(str(source), str(target))
            self.assertEqual(info['devices']['bup_dcu_lfd']['count'], 4000)
            self.assertEqual(info['devices']['bup_dcu_lfd']['start'], 100000)
            window = read_window(str(target), 'bup_dcu_lfd', 100000, 500000)
            self.assertLessEqual(len(window['series']), 1501)
            self.assertTrue(all(r['values']['load_sens_angle']['min'] >= 1 for r in window['series']))
            self.assertEqual(read_window(str(target), 'bup_dcu_rfd', 100000, 101000)['series'], [])
            small = read_window(str(target), 'bup_dcu_lfd', 100000, 100100)
            self.assertEqual(len(small['series']), 2)
            with self.assertRaises(ValueError):
                read_window(str(target), 'bup_dcu_lfd', float('nan'), 100)


class DeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_finished_recordings_can_be_deleted_and_file_is_recoverable(self):
        with tempfile.TemporaryDirectory() as root:
            manager = RecordingManager(root, min_free_bytes=0)
            await manager.start_recording()
            rid = manager.get_status()['recording_id']
            with self.assertRaises(ValueError):
                await manager.delete_recording(rid)
            frame, header = make_frame()
            manager.observe_and_capture(frame, header)
            await manager.stop_recording()
            original = manager.list_recordings()[0]
            await manager.delete_recording(rid)
            self.assertEqual(manager.list_recordings(), [])
            self.assertFalse((manager.recordings_dir/original['path']).exists())
            catalog = json.loads(manager.index_path.read_text())
            deleted = catalog['recordings'][0]
            self.assertEqual(deleted['status'], 'deleted')
            self.assertTrue((manager.recordings_dir/deleted['path']).is_file())
            with self.assertRaises(ValueError):
                await manager.delete_recording('../index.json')
            await manager.close()

class RecordingAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_http_lifecycle_preview_download_and_delete(self):
        import asyncio
        import urllib.request
        from core.recording_http import RecordingAPI
        with tempfile.TemporaryDirectory() as root:
            manager = RecordingManager(root, min_free_bytes=0)
            api = RecordingAPI(manager, port=0)
            base = 'http://127.0.0.1:' + str(api.server.server_port)
            def get(path, post=False, binary=False):
                request = urllib.request.Request(base+'/'+path, data=(json.dumps({'label': 'Проверка названия №1'}).encode() if path == 'start' else b'') if post else None)
                with urllib.request.urlopen(request, timeout=10) as response:
                    raw = response.read()
                    return raw if binary else json.loads(raw)
            try:
                started = await asyncio.to_thread(get, 'start', True)
                self.assertEqual(started['state'], 'recording')
                self.assertIsNone(started['requested_duration_seconds'])
                rid = started['recording_id']
                frame, header = make_frame()
                manager.observe_and_capture(frame, header)
                stopped = await asyncio.to_thread(get, 'stop', True)
                self.assertEqual(stopped['state'], 'idle')
                self.assertEqual(stopped['last_completed']['label'], 'Проверка названия №1')
                self.assertEqual(stopped['last_completed']['packet_count'], 1)
                for _ in range(100):
                    preview = await asyncio.to_thread(get, f'recordings/{rid}/preview')
                    if preview['state'] == 'ready':
                        break
                    await asyncio.sleep(.05)
                self.assertEqual(preview['state'], 'ready')
                self.assertIn('bup_dcu_lfd', preview['devices'])
                window = await asyncio.to_thread(get, f'recordings/{rid}/window?device=bup_dcu_lfd&start=99000&end=101000')
                self.assertEqual(len(window['series']), 1)
                raw = await asyncio.to_thread(get, f'recordings/{rid}/download', False, True)
                self.assertEqual(raw, (manager.recordings_dir/stopped['last_completed']['path']).read_bytes())
                await asyncio.to_thread(get, f'recordings/{rid}/delete', True)
                self.assertEqual((await asyncio.to_thread(get, 'recordings'))['recordings'], [])
                self.assertIsNone((await asyncio.to_thread(get, 'status'))['last_completed'])
            finally:
                await api.close()
                await manager.close()
