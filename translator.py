"""Local Whisper transcription; only recognized text goes to Google Translate."""
import json
import re
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path

def translate_reply(selected_text, reply_text):
    """Detect the selected text language, then translate a composed reply to it."""
    def request(text, target):
        query = urllib.parse.urlencode({'client': 'gtx', 'sl': 'auto', 'tl': target, 'dt': 't', 'q': text[:4000]})
        req = urllib.request.Request('https://translate.googleapis.com/translate_a/single?' + query,
                                      headers={'User-Agent': 'Mumble-RU/1.3'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read(256000))
        return data
    if not selected_text.strip() or not reply_text.strip():
        raise ValueError('Нужны выбранный текст и ваш ответ.')
    detected = request(selected_text, 'ru')
    language = detected[2] if len(detected) > 2 else None
    if not isinstance(language, str) or not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2,4})?', language):
        raise ValueError('Не удалось определить язык. Выделите более длинный текст.')
    if language == 'ru': return reply_text, language
    data = request(reply_text, language)
    result = ''.join(part[0] or '' for part in data[0]).strip()
    if not result: raise ValueError('Сервис не вернул перевод.')
    return result, language

class TextTranslator:
    def __init__(self):
        self.cache = OrderedDict()

    def translate(self, text, language='auto'):
        if language == 'ru' or not text:
            return text
        if text in self.cache:
            return self.cache[text]
        # In fleet comms, "hold your jump" means wait, rather than perform a jump.
        normalized = re.sub(r'(^|[.!?]\s+)(hold (?:your )?jump)([.!?]|$)',
                            r'\1Do not jump yet\3', text, flags=re.IGNORECASE)
        query = urllib.parse.urlencode({'client': 'gtx', 'sl': 'auto', 'tl': 'ru', 'dt': 't', 'q': normalized[:4000]})
        request = urllib.request.Request('https://translate.googleapis.com/translate_a/single?' + query,
                                         headers={'User-Agent': 'Mumble-RU/1.0'})
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read(256000))
        result = ''.join(part[0] or '' for part in data[0])
        if not result:
            raise ValueError('Сервис не вернул перевод')
        self.cache[text] = result
        if len(self.cache) > 200:
            self.cache.popitem(last=False)
        return result

class Translator(TextTranslator):
    def __init__(self, model='base'):
        super().__init__()
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model, device='cpu', compute_type='int8', cpu_threads=4,
                                  download_root=str(Path(__file__).parent / 'models'))

    def transcribe(self, pcm, rate):
        import numpy as np
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        if rate != 16000:
            audio = np.interp(np.arange(int(len(audio) * 16000 / rate)) * rate / 16000,
                              np.arange(len(audio)), audio).astype(np.float32)
        segments, info = self.model.transcribe(audio, language=None, beam_size=1,
            condition_on_previous_text=False, vad_filter=True,
            initial_prompt='EVE Online fleet comms: align, warp, jump, tackle, primary, secondary, gate, cyno, bubble, logi, broadcast, hold, anchor.')
        text = ' '.join(s.text.strip() for s in segments if s.no_speech_prob < 0.8).strip()
        return text, info.language
