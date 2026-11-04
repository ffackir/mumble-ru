import array
import ctypes
import math
from pathlib import Path
import socket
import struct
import sys
import subprocess
import time
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from protocol import HEADER, PORT, Segmenter, decode
from translator import Translator

def pcm(seconds, rate=16000):
    return array.array('h', [int(5000 * math.sin(i * .12)) for i in range(int(rate * seconds))]).tobytes()

class ProtocolTests(unittest.TestCase):
    def test_rejects_malformed_packets(self):
        invalid = [b'', b'MRU1', HEADER.pack(b'NOPE', 1, 1, 2, 3, 16000),
                   HEADER.pack(b'MRU1', 1, 1, 2, 3, 0) + b'00',
                   HEADER.pack(b'MRU1', 1, 1, 2, 3, 16000) + b'0',
                   HEADER.pack(b'MRU1', 5, 1, 2, 3, 0) + b'extra']
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode(data)

    def test_speakers_never_mix(self):
        segmenter = Segmenter()
        a, b = pcm(.4), pcm(.5)
        self.assertEqual(segmenter.add((1, 10), 16000, a, now=1), [])
        self.assertEqual(segmenter.add((1, 20), 16000, b, now=1.1), [])
        ready = segmenter.flush(now=2)
        self.assertEqual({item[0]: item[2] for item in ready}, {(1, 10): a, (1, 20): b})
        self.assertFalse(segmenter.streams)

    def test_long_speech_is_split(self):
        segmenter = Segmenter()
        ready = []
        for i in range(51):
            ready.extend(segmenter.add((1, 1), 16000, pcm(.1), now=1 + i * .1))
        self.assertEqual(len(ready), 1)
        self.assertLessEqual(len(ready[0][2]), 16000 * 2 * 5.1)

    def test_silence_and_clicks_do_not_create_jobs(self):
        segmenter = Segmenter()
        segmenter.add((1, 1), 16000, bytes(16000), now=1)
        self.assertEqual(segmenter.flush(now=2), [])
        segmenter.add((1, 1), 16000, pcm(.1), now=3)
        self.assertEqual(segmenter.flush(now=4), [])

    def test_resample_boundary_keeps_users_separate(self):
        segmenter = Segmenter()
        segmenter.add((1, 1), 16000, pcm(.4), now=1)
        ready = segmenter.add((1, 1), 48000, pcm(.4, 48000), now=1.1)
        self.assertEqual(ready[0][1], 16000)
        self.assertEqual(segmenter.flush(now=2)[0][1], 48000)

class TranslationTests(unittest.TestCase):
    def test_google_response_is_joined_and_cached(self):
        translator = Translator.__new__(Translator)
        from collections import OrderedDict
        translator.cache = OrderedDict()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return '[[["Держите ","Hold "],["прыжок","jump"]]]'.encode()
        with patch('urllib.request.urlopen', return_value=Response()) as network:
            self.assertEqual(translator.translate('Hold jump'), 'Держите прыжок')
            self.assertEqual(translator.translate('Hold jump'), 'Держите прыжок')
            self.assertEqual(network.call_count, 1)
            self.assertNotIn('Captain', network.call_args.args[0].full_url)
        self.assertEqual(translator.translate('Русский текст', 'ru'), 'Русский текст')

    def test_hold_jump_means_wait_without_changing_negation(self):
        translator = Translator.__new__(Translator)
        from collections import OrderedDict
        translator.cache = OrderedDict()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'[[["translated"]]]'
        with patch('urllib.request.urlopen', return_value=Response()) as network:
            translator.translate('Align to the gate. Hold your jump.')
            self.assertIn('Do+not+jump+yet', network.call_args.args[0].full_url)
            translator.translate('Do not hold your jump.')
            self.assertIn('Do+not+hold+your+jump', network.call_args.args[0].full_url)

@unittest.skipUnless(sys.platform == 'win32' and (BASE / 'mumble_ru.dll').exists(), 'Windows DLL required')
class NativePluginTests(unittest.TestCase):
    def test_dll_callbacks_forward_unchanged_audio_and_talking_state(self):
        dll = ctypes.CDLL(str(BASE / 'mumble_ru.dll'))
        fake_api = (ctypes.c_void_p * 1024)()
        nickname = ctypes.create_string_buffer('Командир'.encode('utf-8'))
        free_callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p)(lambda caller, pointer: 0)
        def get_name(caller, connection, user, output):
            output[0] = ctypes.addressof(nickname)
            return 0
        name_callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_int32,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))(get_name)
        # First API fields: freeMemory, getActiveServerConnection,
        # isConnectionSynchronized, getLocalUserID, getUserName.
        fake_api[0] = ctypes.cast(free_callback, ctypes.c_void_p)
        fake_api[4] = ctypes.cast(name_callback, ctypes.c_void_p)
        dll.mumble_registerAPIFunctions.argtypes = [ctypes.c_void_p]
        dll.mumble_registerAPIFunctions(fake_api)
        dll.mumble_init.argtypes = [ctypes.c_uint32]
        dll.mumble_init.restype = ctypes.c_int
        dll.mumble_onServerSynchronized.argtypes = [ctypes.c_int32]
        dll.mumble_onAudioSourceFetched.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_uint32,
            ctypes.c_uint16, ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        dll.mumble_onAudioSourceFetched.restype = ctypes.c_bool
        dll.mumble_onUserTalkingStateChanged.argtypes = [ctypes.c_int32, ctypes.c_uint32, ctypes.c_int]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(('127.0.0.1', PORT))
            receiver.settimeout(2)
            self.assertEqual(dll.mumble_init(42), 0)
            from lifecycle import CompanionLease
            lease = None
            try:
                dll.mumble_onServerSynchronized(7)
                raw = [1., -1., 2., 2., float('nan'), .5, .25, .25]
                values = (ctypes.c_float * len(raw))(*raw)
                before = bytes(values)
                # No companion: there must be no audio or heartbeat at all.
                dll.mumble_onAudioSourceFetched(values, 4, 2, 48000, True, 99)
                receiver.settimeout(.15)
                with self.assertRaises(socket.timeout):
                    receiver.recvfrom(2048)
                lease = CompanionLease()
                time.sleep(.12)
                receiver.settimeout(2)
                self.assertFalse(dll.mumble_onAudioSourceFetched(values, 4, 2, 48000, True, 99))
                self.assertEqual(before, bytes(values))
                dll.mumble_onUserTalkingStateChanged(7, 99, 1)
                received = {}
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not (1 in received and 2 in received and 3 in received):
                    data, _ = receiver.recvfrom(2048)
                    kind, key, seq, rate, payload = decode(data)
                    received[kind] = (key, rate, payload)
                    if 2 not in received:
                        # The bridge deliberately drops packets if its audio-safe
                        # queue is busy. Retry the metadata event under scheduler load.
                        dll.mumble_onUserTalkingStateChanged(7, 99, 1)
                self.assertEqual(received[1][:2], ((7, 99), 48000))
                self.assertEqual(struct.unpack('<4h', received[1][2]), (0, 32767, 0, 8191))
                self.assertEqual(received[3][:2], ((7, 99), 1))
                self.assertEqual(received[2][2].decode('utf-8'), 'Командир')
                # Non-speech output must not be captured.
                self.assertFalse(dll.mumble_onAudioSourceFetched(values, 4, 2, 48000, False, 100))
                lease.close()
                time.sleep(.12)
                receiver.setblocking(False)
                try:
                    while receiver.recvfrom(2048): pass
                except BlockingIOError:
                    pass
                receiver.settimeout(.15)
                dll.mumble_onAudioSourceFetched(values, 4, 2, 48000, True, 99)
                with self.assertRaises(socket.timeout):
                    receiver.recvfrom(2048)
                # Reopening the companion resumes capture without reloading DLL.
                lease = CompanionLease()
                time.sleep(.12)
                dll.mumble_onAudioSourceFetched(values, 4, 2, 48000, True, 99)
                receiver.settimeout(2)
                while decode(receiver.recvfrom(2048)[0])[0] != 1: pass
            finally:
                if lease:
                    lease.close()
                dll.mumble_shutdown()

class LifecycleTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows process handles required')
    def test_parent_exit_requests_companion_shutdown(self):
        from lifecycle import CompanionLease
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        lease = CompanionLease()
        try:
            lease.watch_parent(child.pid)
            self.assertFalse(lease.shutdown_requested())
            child.terminate()
            child.wait(timeout=3)
            self.assertTrue(lease.shutdown_requested())
        finally:
            lease.close()
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows DLL required')
    def test_plugin_starts_and_stops_companion(self):
        import winreg
        folder = BASE / 'test-data'
        folder.mkdir(exist_ok=True)
        stub = folder / 'managed_companion.py'
        state = folder / 'managed-state.txt'
        state.write_text('', encoding='utf-8')
        stub.write_text("import sys,time\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parents[1]))\nfrom lifecycle import CompanionLease\nlease=CompanionLease(reset_stop=False)\np=Path(__file__).with_name('managed-state.txt')\np.write_text('running')\nend=time.monotonic()+8\nwhile time.monotonic()<end and not lease.shutdown_requested(): time.sleep(.02)\nlease.close()\np.write_text('stopped')\n", encoding='utf-8')
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\MumbleRU')
        previous = {}
        for name in ('Python', 'Script'):
            try: previous[name] = winreg.QueryValueEx(key, name)
            except FileNotFoundError: previous[name] = None
        dll = ctypes.CDLL(str(BASE / 'mumble_ru.dll'))
        functions = (ctypes.c_void_p * 1024)()
        def active(caller, output):
            output[0] = 7
            return 0
        active_callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32))(active)
        functions[1] = ctypes.cast(active_callback, ctypes.c_void_p)
        dll.mumble_registerAPIFunctions.argtypes = [ctypes.c_void_p]
        dll.mumble_registerAPIFunctions(functions)
        started = False
        try:
            winreg.SetValueEx(key, 'Python', 0, winreg.REG_SZ, sys.executable)
            winreg.SetValueEx(key, 'Script', 0, winreg.REG_SZ, str(stub))
            self.assertEqual(dll.mumble_init(5), 0)
            started = True
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and state.read_text() != 'running': time.sleep(.04)
            self.assertEqual(state.read_text(), 'running')
            dll.mumble_shutdown()
            started = False
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and state.read_text() != 'stopped': time.sleep(.04)
            self.assertEqual(state.read_text(), 'stopped')
        finally:
            if started: dll.mumble_shutdown()
            for name, value in previous.items():
                if value is None:
                    try: winreg.DeleteValue(key, name)
                    except FileNotFoundError: pass
                else:
                    winreg.SetValueEx(key, name, 0, value[1], value[0])
            key.Close()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows mutex required')
    def test_crashed_companion_releases_ownership(self):
        from lifecycle import CompanionLease
        child = subprocess.Popen([sys.executable, '-u', '-c',
            'from lifecycle import CompanionLease; import time; lease=CompanionLease(); print("ready",flush=True); time.sleep(30)'],
            cwd=BASE, stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            with self.assertRaises(RuntimeError):
                CompanionLease()
            child.terminate()
            child.wait(timeout=3)
            # WAIT_ABANDONED must also permit starting a new companion.
            lease = CompanionLease()
            lease.close()
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)
            child.stdout.close()

class OverlayTests(unittest.TestCase):
    def setUp(self):
        empty = BASE / 'test-data' / 'empty-chat-logs'
        empty.mkdir(parents=True, exist_ok=True)
        self.chat_patch = patch('chat_panel.default_chat_folder', return_value=empty)
        self.chat_patch.start()
        self.addCleanup(self.chat_patch.stop)

    def test_chat_window_is_independent_from_voice_window(self):
        from overlay import App
        settings = BASE / 'test-data' / 'separate-window-settings.json'
        with patch('overlay.CONFIG', settings):
            app = App()
            try:
                app.root.update()
                self.assertIs(app.chat_panel.parent, app.chat_window)
                self.assertIsNot(app.chat_window, app.root)
                app.root.iconify()
                app.root.update()
                self.assertTrue(app.chat_window.winfo_viewable())
                app.hide_chat()
                app.root.update()
                self.assertFalse(app.chat_window.winfo_viewable())
                app.show_chat()
                app.chat_topmost_var.set(True)
                app.toggle_chat_topmost()
                app.root.update()
                self.assertTrue(app.chat_window.winfo_viewable())
                self.assertFalse(app.root.attributes('-topmost'))
                self.assertTrue(app.chat_window.attributes('-topmost'))
            finally:
                app.close()

    def test_translation_setting_survives_close_and_reopen(self):
        import json
        from overlay import App
        settings = BASE / 'test-data' / 'resume-settings.json'
        settings.parent.mkdir(exist_ok=True)
        settings.write_text('{"translate_enabled": true}', encoding='utf-8')
        with patch('overlay.CONFIG', settings):
            app = App()
            try:
                app.root.withdraw()
                self.assertTrue(app.enabled.is_set())
            finally:
                app.close()
            app = App()
            try:
                app.root.withdraw()
                self.assertTrue(app.enabled.is_set())
                app.translate_var.set(False)
                app.toggle_translation()
            finally:
                app.close()
            self.assertFalse(json.loads(settings.read_text())['translate_enabled'])

    def test_speaker_caption_and_translation_toggle(self):
        from overlay import App
        test_settings = BASE / 'test-data' / 'settings.json'
        settings_patch = patch('overlay.CONFIG', test_settings)
        settings_patch.start()
        app = App()
        try:
            app.root.withdraw()
            app.post(('name', (7, 99), 'Test Commander'))
            app.post(('talk', (7, 99), 1, time.monotonic()))
            app.post(('caption', app.generation, (7, 99), 'Ждите прыжка', 'Hold jump'))
            app.tick()
            self.assertIn('Test Commander', app.speakers.get())
            self.assertIn('Ждите прыжка', app.caption_text.get())
            app.translate_var.set(True)
            app.toggle_translation()
            self.assertTrue(app.enabled.is_set())
            app.translate_var.set(False)
            app.toggle_translation()
            self.assertFalse(app.enabled.is_set())
            app.post(('reset', 7))
            app.tick()
            self.assertFalse(app.users)
        finally:
            app.close()
            settings_patch.stop()

if __name__ == '__main__':
    unittest.main()
