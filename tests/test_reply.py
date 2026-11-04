import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from translator import translate_reply

class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, count): return json.dumps(self.value).encode()

class ReplyTests(unittest.TestCase):
    def test_reply_uses_detected_language_of_selected_text(self):
        responses = [Response([[['Привет']], None, 'zh-CN']),
                     Response([[['你好']], None, 'ru'])]
        with patch('urllib.request.urlopen', side_effect=responses) as network:
            reply, language = translate_reply('你好朋友', 'Привет')
            self.assertEqual((reply, language), ('你好', 'zh-CN'))
            target = parse_qs(urlparse(network.call_args_list[1].args[0].full_url).query)['tl']
            self.assertEqual(target, ['zh-CN'])

    def test_russian_selection_does_not_require_second_translation(self):
        with patch('urllib.request.urlopen', return_value=Response([[['Привет']], None, 'ru'])) as network:
            self.assertEqual(translate_reply('Привет', 'Здравствуйте'), ('Здравствуйте', 'ru'))
            network.assert_called_once()

    def test_unknown_language_does_not_guess_a_target(self):
        with patch('urllib.request.urlopen', return_value=Response([[['123']]])):
            with self.assertRaisesRegex(ValueError, 'определить язык'):
                translate_reply('123', 'Привет')

if __name__ == '__main__': unittest.main()
