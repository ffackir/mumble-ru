"""Read EVE chat history and new lines. Never modify files or interact with EVE."""
import codecs
import ctypes
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

MESSAGE = re.compile(r'^\[\s*(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s*\]\s*(.*?)\s*>\s*(.*)$')
FILENAME = re.compile(r'^(.*?)_\d{8}_\d{6}(?:_\d+)?$')

def default_chat_folder():
    if hasattr(ctypes, 'windll'):
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
            return Path(buffer.value) / 'EVE' / 'logs' / 'Chatlogs'
    return Path.home() / 'Documents' / 'EVE' / 'logs' / 'Chatlogs'

def encoding_for(data):
    if data.startswith(b'\xff\xfe'): return 'utf-16-le'
    if data.startswith(b'\xfe\xff'): return 'utf-16-be'
    if data.startswith(b'\xef\xbb\xbf'): return 'utf-8-sig'
    if b'\x00' in data[:100]: return 'utf-16-le'
    return 'utf-8'

@dataclass(frozen=True)
class ChatMessage:
    channel_id: str
    channel: str
    timestamp: str
    author: str
    text: str

@dataclass
class Tail:
    identity: tuple
    offset: int
    decoder: object
    pending: str = ''
    skip_line: bool = False

class ChatWatcher:
    def __init__(self, folder, since=None):
        self.folder = Path(folder)
        self.since = since or datetime.now(timezone.utc).replace(microsecond=0)
        self.metadata = {}
        self.tails = {}
        self.channels = {}
        self.seen = OrderedDict()
        self.primed = False
        self.latest = {}

    def read_history(self, selected, limit=100):
        """Bounded snapshot from the latest sessions, separate from the live tail."""
        messages = {}
        for path, metadata in self.latest.items():
            channel_id, channel, _, encoding = metadata
            if channel_id not in selected: continue
            try:
                with path.open('rb') as source:
                    source.seek(0, 2)
                    start = max(0, source.tell() - 262144)
                    if encoding.startswith('utf-16'): start -= start % 2
                    source.seek(start)
                    content = source.read(262144).decode(encoding, errors='replace')
                lines = content.split('\n')[:-1]  # Exclude an unfinished last line.
                if start and lines: lines.pop(0)
                for line in lines:
                    match = MESSAGE.match(line.strip('\ufeff\r'))
                    if not match: continue
                    timestamp, author, text = match.groups()
                    try: datetime.strptime(timestamp, '%Y.%m.%d %H:%M:%S')
                    except ValueError: continue
                    if not author.strip() or not text.strip() or author.casefold() in ('eve system', 'система eve'): continue
                    message = ChatMessage(channel_id, channel, timestamp, author, text)
                    messages[message] = None
            except (OSError, UnicodeError):
                continue
        result = {}
        for message in sorted(messages, key=lambda m: (m.timestamp, m.author, m.text)):
            result.setdefault(message.channel_id, []).append(message)
        return {key: values[-limit:] for key, values in result.items()}

    def _metadata(self, path, stat):
        identity = (stat.st_dev, stat.st_ino)
        cached = self.metadata.get(path)
        tail = self.tails.get(path)
        if cached and cached[0] == identity and not (tail and stat.st_size < tail.offset):
            return cached[1]
        with path.open('rb') as source:
            head = source.read(8192)
        if len(head) < 2: return None
        encoding = encoding_for(head)
        header = head.decode(encoding, errors='ignore').lstrip('\ufeff')
        values = {}
        for line in header.splitlines():
            if MESSAGE.match(line): break
            if ':' in line:
                key, value = line.split(':', 1)
                values[key.strip()] = value.strip()
        channel = values.get('Channel Name')
        if not channel or not re.search(r'Session started:\s*\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}', header):
            # Wait for the complete EVE header rather than guessing a partial name.
            return None
        channel_id = values.get('Channel ID', channel)
        listener = values.get('Listener', '')
        result = (channel_id, channel, listener, encoding)
        self.metadata[path] = (identity, result)
        return result

    def poll(self):
        candidates = {}
        channels = {}
        channel_ranks = {}
        for path in self.folder.glob('*.txt'):
            try:
                stat = path.stat()
                metadata = self._metadata(path, stat)
                if not metadata: continue
                channel_id, channel, listener, encoding = metadata
                key = (channel_id, listener)
                # A recently touched old log must not replace a newer session.
                session = re.search(r'_(\d{8}_\d{6})(?:_\d+)?$', path.stem)
                rank = (session.group(1) if session else '', stat.st_mtime_ns, path.name)
                if key not in candidates or rank > candidates[key][0]:
                    candidates[key] = (rank, path, stat, metadata)
                if channel_id not in channel_ranks or rank > channel_ranks[channel_id]:
                    channels[channel_id] = channel
                    channel_ranks[channel_id] = rank
            except (OSError, UnicodeError):
                continue
        self.channels = channels
        self.latest = {path: metadata for _, path, _, metadata in candidates.values()}
        ready = []
        active_paths = set()
        for _, path, stat, metadata in candidates.values():
            active_paths.add(path)
            channel_id, channel, listener, encoding = metadata
            identity = (stat.st_dev, stat.st_ino)
            tail = self.tails.get(path)
            if tail is None or tail.identity != identity or stat.st_size < tail.offset:
                offset = stat.st_size if not self.primed else 0
                skip_line = False
                if offset:
                    # A baseline may coincide with a partially written UTF-16 code unit.
                    if encoding.startswith('utf-16'):
                        offset -= offset % 2
                    try:
                        with path.open('rb') as source:
                            source.seek(max(0, offset - 4))
                            skip_line = not source.read().decode(encoding, errors='ignore').endswith('\n')
                    except OSError:
                        continue
                tail = Tail(identity, offset, codecs.getincrementaldecoder(encoding)(errors='replace'), skip_line=skip_line)
                self.tails[path] = tail
            try:
                with path.open('rb') as source:
                    source.seek(tail.offset)
                    data = source.read(65536)
                tail.offset += len(data)
                tail.pending += tail.decoder.decode(data)
            except (OSError, UnicodeError):
                continue
            lines = tail.pending.split('\n')
            tail.pending = lines.pop()
            if len(tail.pending) > 16000:
                tail.pending = ''
                tail.skip_line = True
            for line in lines:
                if tail.skip_line:
                    tail.skip_line = False
                    continue
                match = MESSAGE.match(line.strip('\ufeff\r'))
                if not match: continue
                timestamp, author, text = match.groups()
                try:
                    stamp = datetime.strptime(timestamp, '%Y.%m.%d %H:%M:%S').replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if stamp < self.since or not text.strip() or not author.strip(): continue
                # System notices and session headers are not participant messages.
                if author.casefold() in ('eve system', 'система eve'): continue
                dedup = (channel_id, timestamp, author, text)
                if dedup in self.seen: continue
                self.seen[dedup] = None
                if len(self.seen) > 1000: self.seen.popitem(last=False)
                ready.append(ChatMessage(channel_id, channel, timestamp, author, text))
        self.tails = {path: tail for path, tail in self.tails.items() if path in active_paths}
        live = set(self.folder.glob('*.txt'))
        self.metadata = {path: value for path, value in self.metadata.items() if path in live}
        self.primed = True
        return sorted(ready, key=lambda message: message.timestamp)
