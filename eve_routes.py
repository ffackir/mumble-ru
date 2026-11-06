"""Public ESI gate routes and player position inferred only from Local logs."""
from collections import OrderedDict
from dataclasses import dataclass
import codecs
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from functools import lru_cache

from eve_chat import MESSAGE

class SystemCatalog:
    def __init__(self, rows=None):
        if rows is None:
            rows = json.loads((Path(__file__).parent / 'assets' / 'solar_systems.json').read_text('utf-8'))
        self.names = {row['name'].casefold(): (row['id'], row['name']) for row in rows}
        self.ids = {value[0]: value[1] for value in self.names.values()}
        self.pattern = re.compile(r'(?<![\w-])(?:' + '|'.join(re.escape(name) for name in sorted(self.names, key=len, reverse=True)) + r')(?![\w-])', re.IGNORECASE)

    def resolve(self, name):
        return self.names.get(name.strip().strip('*').strip().casefold())

    def mentions(self, text):
        for match in self.pattern.finditer(text):
            system_id, name = self.names[match.group().casefold()]
            yield match.start(), match.end(), system_id, name

    def suggest(self, query, limit=20):
        query = query.strip().casefold()
        if not query: return []
        matches = [value[1] for key, value in self.names.items() if query in key]
        return sorted(matches, key=lambda name: (not name.casefold().startswith(query), name.casefold()))[:limit]

@lru_cache(maxsize=1)
def default_catalog():
    return SystemCatalog()

@lru_cache(maxsize=1)
def stargate_graph():
    path = Path(__file__).parent / 'assets' / 'stargate_graph.json'
    return json.loads(path.read_text(encoding='utf-8'))


@lru_cache(maxsize=128)
def nearby_gate_distances(origin):
    distances = {origin: 0}
    frontier = [origin]
    graph = stargate_graph()
    for jumps in range(1, 4):
        next_frontier = []
        for system in frontier:
            for neighbor in graph.get(str(system), ()):
                if neighbor not in distances:
                    distances[neighbor] = jumps
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return distances


@dataclass(frozen=True)
class PlayerLocation:
    pilot: str
    system_id: int
    system: str
    timestamp: str

LOCAL_CHANGE = re.compile(r'(?:Channel\s+changed\s+to\s+Local|Канал\s+измен[её]н\s+на\s+Локальный)\s*:\s*(.+?)\s*$', re.IGNORECASE)

class LocationTracker:
    def __init__(self, catalog):
        self.catalog = catalog
        self.files = {}

    def update(self, metadata):
        candidates = {}
        for path, (identity, info) in metadata.items():
            channel_id, channel, pilot, encoding = info
            if channel_id.casefold() != 'local': continue
            try: stat = path.stat()
            except OSError: continue
            rank = (stat.st_mtime_ns, path.name)
            if pilot not in candidates or rank > candidates[pilot][0]:
                candidates[pilot] = (rank, path, stat, identity, encoding)
        locations = []
        active_paths = set()
        for pilot, (_, path, stat, identity, encoding) in candidates.items():
            active_paths.add(path)
            state = self.files.get(path)
            if state is None or state['identity'] != identity or stat.st_size < state['offset']:
                state = self.files[path] = {'identity': identity, 'offset': 0,
                    'decoder': codecs.getincrementaldecoder(encoding)(errors='replace'),
                    'pending': '', 'location': None}
            try:
                with path.open('rb') as source:
                    source.seek(state['offset'])
                    # Large initial logs are scanned over several polls without blocking the UI.
                    data = source.read(2 * 1024 * 1024)
                state['offset'] += len(data)
                text = state['pending'] + state['decoder'].decode(data)
            except (OSError, UnicodeError): continue
            lines = text.split('\n')
            state['pending'] = lines.pop()[-16000:]
            for line in lines:
                match = MESSAGE.match(line.strip('\r\ufeff'))
                if not match: continue
                timestamp, author, body = match.groups()
                if author.casefold() not in ('eve system', 'система eve'): continue
                changed = LOCAL_CHANGE.search(body)
                if not changed: continue
                system = self.catalog.resolve(changed.group(1))
                state['location'] = PlayerLocation(pilot, system[0], system[1], timestamp) if system else None
            if state['offset'] >= stat.st_size and state['location']:
                locations.append(state['location'])
        self.files = {path: state for path, state in self.files.items() if path in active_paths}
        return sorted(locations, key=lambda location: location.timestamp, reverse=True)

class RouteClient:
    def __init__(self):
        self.cache = OrderedDict()

    def jumps(self, origin, destination):
        if origin == destination: return 0
        key = (origin, destination)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 300: return cached[1]
        request = urllib.request.Request(f'https://esi.evetech.net/route/{int(origin)}/{int(destination)}',
            data=json.dumps({'preference': 'Shorter', 'security_penalty': 50}).encode(),
            headers={'User-Agent': 'MumbleRU/1.2 (EVE chat companion)',
                     'X-Compatibility-Date': '2025-09-30', 'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read(2000000))
        except urllib.error.HTTPError as error:
            if error.code in (400, 404): raise ValueError('Нет маршрута по звёздным вратам.') from error
            raise ValueError(f'ESI временно недоступен ({error.code}). Повторите позже.') from error
        route = payload.get('route') if isinstance(payload, dict) else payload
        if not isinstance(route, list) or not route or route[0] != origin or route[-1] != destination:
            raise ValueError('ESI вернул некорректный маршрут.')
        count = len(route) - 1
        self.cache[key] = (time.monotonic(), count)
        if len(self.cache) > 200: self.cache.popitem(last=False)
        return count
