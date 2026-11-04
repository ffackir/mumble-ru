"""Word/phrase matching and bounded notification deduplication."""
from collections import OrderedDict
import re
import time
from pathlib import Path

def parse_words(text):
    result = []
    seen = set()
    for value in re.split(r'[,;\n]+', text):
        word = ' '.join(value.split())[:200]
        key = word.casefold()
        if word and key not in seen:
            result.append(word)
            seen.add(key)
        if len(result) >= 100: break
    return result

class WordAlerts:
    def __init__(self, words=(), enabled=True):
        self.seen = OrderedDict()
        self.last_sound = float('-inf')
        self.configure(words, enabled)

    def configure(self, words, enabled):
        self.words = parse_words('\n'.join(words))
        self.enabled = bool(enabled)
        self.patterns = [re.compile(r'(?<!\w)' + r'\s+'.join(re.escape(part) for part in word.split()) + r'(?!\w)', re.IGNORECASE)
                         for word in self.words]

    def matches(self, text):
        return any(pattern.search(text) for pattern in self.patterns)

    def should_play(self, key, text, now=None):
        if not self.matches(text): return False
        return self.claim(key, now)

    def claim(self, key, now=None):
        if not self.enabled or key in self.seen: return False
        self.seen[key] = None
        if len(self.seen) > 512: self.seen.popitem(last=False)
        now = time.monotonic() if now is None else now
        if now - self.last_sound < 2: return False
        self.last_sound = now
        return True

def play_alert():
    import winsound
    sound = Path(__file__).parent / 'assets' / 'alert.wav'
    winsound.PlaySound(str(sound), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
