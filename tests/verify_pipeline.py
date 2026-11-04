"""Opt-in integration test using a synthetic WAV; never connects to a server.

Usage: .venv\Scripts\python.exe tests\verify_pipeline.py test-data\fleet.wav
Sends only the synthetic recognized text to Google Translate.
"""
import ctypes
import json
from pathlib import Path
import sys
import time
import wave
from unittest.mock import patch
import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from overlay import App

with wave.open(sys.argv[1], 'rb') as audio:
    assert audio.getsampwidth() == 2 and audio.getnchannels() == 1
    rate = audio.getframerate()
    values = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768

dll = ctypes.CDLL(str(BASE / 'mumble_ru.dll'))
functions = (ctypes.c_void_p * 1024)()
name = ctypes.create_string_buffer(b'Fleet Commander')
def name_for(caller, connection, user, output):
    output[0] = ctypes.addressof(name)
    return 0
name_callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_int32,
    ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))(name_for)
free_callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p)(lambda a, b: 0)
functions[0] = ctypes.cast(free_callback, ctypes.c_void_p)
functions[4] = ctypes.cast(name_callback, ctypes.c_void_p)
dll.mumble_registerAPIFunctions.argtypes = [ctypes.c_void_p]
dll.mumble_onAudioSourceFetched.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_uint32,
    ctypes.c_uint16, ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
dll.mumble_registerAPIFunctions(functions)

with patch('overlay.CONFIG', BASE / 'test-data' / 'settings.json'):
    app = App()
    app.root.withdraw()
    app.translate_var.set(True)
    app.toggle_translation()
    assert dll.mumble_init(1) == 0
    dll.mumble_onServerSynchronized(7)
    dll.mumble_onUserTalkingStateChanged(7, 99, 1)
    frame = max(1, rate // 50)
    offset = 0
    begin = time.monotonic()
    try:
        while time.monotonic() - begin < 30:
            if offset < len(values):
                chunk = np.ascontiguousarray(values[offset:offset + frame])
                dll.mumble_onAudioSourceFetched(chunk.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                    len(chunk), 1, rate, True, 99)
                offset += len(chunk)
            app.root.update()
            if app.captions and 'gate' in app.captions[-1][2].lower():
                speaker, russian, original = app.captions[-1]
                assert speaker == 'Fleet Commander', speaker
                assert not russian.startswith('[оригинал]'), russian
                assert any('\u0400' <= c <= '\u04ff' for c in russian), russian
                result = {'speaker': speaker, 'original': original, 'russian': russian,
                          'pipeline_seconds': round(time.monotonic() - begin, 2)}
                print(json.dumps(result, ensure_ascii=True))
                (BASE / 'test-data' / 'pipeline-result.json').write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                break
            time.sleep(.02)
        else:
            raise AssertionError('Pipeline timed out: ' + app.status.get())
    finally:
        dll.mumble_shutdown()
        app.close()
