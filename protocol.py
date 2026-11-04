"""Bounded receive-only bridge protocol. No Mumble credentials or API calls."""
import array
import math
import struct
import sys
import time
from dataclasses import dataclass, field

PORT = 47831
HEADER = struct.Struct('<4sB3xIIII')

def decode(data):
    if not HEADER.size <= len(data) <= HEADER.size + 1024:
        raise ValueError('Invalid size')
    magic, kind, conn, user, sequence, rate = HEADER.unpack_from(data)
    payload = data[HEADER.size:]
    if magic != b'MRU1' or kind not in range(1, 6):
        raise ValueError('Invalid header')
    if kind == 1 and (not 8000 <= rate <= 192000 or not payload or len(payload) % 2):
        raise ValueError('Invalid PCM')
    if kind in (3, 4, 5) and payload:
        raise ValueError('Unexpected payload')
    return kind, (conn, user), sequence, rate, payload

def samples(pcm):
    result = array.array('h', pcm)
    if sys.byteorder != 'little':
        result.byteswap()
    return result

def rms(pcm):
    values = samples(pcm)
    return math.sqrt(sum(v * v for v in values) / max(1, len(values))) / 32768

@dataclass
class Stream:
    rate: int
    data: bytearray = field(default_factory=bytearray)
    last_packet: float = 0
    last_voice: float = 0

class Segmenter:
    """Separate buffers per speaker, flushed after silence or 5 seconds."""
    def __init__(self):
        self.streams = {}

    def add(self, key, rate, pcm, now=None):
        now = time.monotonic() if now is None else now
        ready = self.flush(now)
        stream = self.streams.get(key)
        if stream is not None and stream.rate != rate:
            ready.extend(self.take(key))
            stream = None
        if stream is None:
            if len(self.streams) >= 64:
                return ready
            stream = self.streams[key] = Stream(rate)
        stream.last_packet = now
        if rms(pcm) >= 0.007:
            stream.last_voice = now
        if stream.data or stream.last_voice == now:
            stream.data.extend(pcm)
        if len(stream.data) >= rate * 2 * 5:
            ready.extend(self.take(key))
        return ready

    def take(self, key):
        stream = self.streams.pop(key, None)
        if stream and len(stream.data) >= stream.rate * 2 * 0.35:
            return [(key, stream.rate, bytes(stream.data))]
        return []

    def flush(self, now=None):
        now = time.monotonic() if now is None else now
        ready = []
        for key, stream in list(self.streams.items()):
            if now - stream.last_packet > 0.65 or now - stream.last_voice > 0.65:
                ready.extend(self.take(key))
        return ready
