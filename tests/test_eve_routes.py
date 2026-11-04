import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from eve_routes import LocationTracker, RouteClient, SystemCatalog

ROWS = [{'id': 1, 'name': 'Jita'}, {'id': 2, 'name': 'Perimeter'}, {'id': 3, 'name': '4-HWWF'}]

class RouteTests(unittest.TestCase):
    def test_suggestions_are_case_insensitive_ranked_and_bounded(self):
        catalog = SystemCatalog(ROWS + [{'id': 4, 'name': 'New Jita'}, {'id': 5, 'name': 'Jitan'}])
        self.assertEqual(catalog.suggest('JI'), ['Jita', 'Jitan', 'New Jita'])
        self.assertEqual(catalog.suggest('JI', limit=1), ['Jita'])
        self.assertEqual(catalog.suggest('hww'), ['4-HWWF'])
        self.assertEqual(catalog.suggest(''), [])

    def test_autocomplete_selects_a_valid_system_and_removes_trace(self):
        import tkinter as tk
        from system_entry import SystemEntry
        root = tk.Tk(); root.withdraw()
        variable = tk.StringVar()
        edits = []
        widget = SystemEntry(root, SystemCatalog(ROWS), variable, on_edit=lambda: edits.append(True))
        try:
            variable.set('peri')
            self.assertEqual(widget.matches, ['Perimeter'])
            widget.move(1)
            widget.choose()
            self.assertEqual(variable.get(), 'Perimeter')
            self.assertTrue(edits)
            widget.destroy()
            variable.set('Jita')
        finally:
            root.destroy()

    def test_system_mentions_and_boundaries(self):
        catalog = SystemCatalog(ROWS)
        self.assertEqual([item[3] for item in catalog.mentions('notJita JITA, 4-HWWF* 4-HWWF-other')], ['Jita', '4-HWWF'])
        self.assertEqual(catalog.resolve(' 4-HWWF* '), (3, '4-HWWF'))

    def test_modern_route_response_counts_edges_not_systems_and_caches(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, count): return json.dumps({'route': [1, 2, 3]}).encode()
        client = RouteClient()
        with patch('urllib.request.urlopen', return_value=Response()) as network:
            self.assertEqual(client.jumps(1, 3), 2)
            self.assertEqual(client.jumps(1, 3), 2)
            self.assertEqual(client.jumps(1, 1), 0)
            network.assert_called_once()

    def test_no_gate_route_and_invalid_response_are_not_distances(self):
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('https://example', 404, 'No route', {}, None)):
            with self.assertRaisesRegex(ValueError, 'Нет маршрута'): RouteClient().jumps(1, 2)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, count): return b'{"route":[]}'
        with patch('urllib.request.urlopen', return_value=Response()):
            with self.assertRaisesRegex(ValueError, 'некорректный'): RouteClient().jumps(1, 2)

    def test_location_updates_only_from_system_notices(self):
        test_root = (BASE / 'test-data').resolve(); test_root.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='eve-route-test-', dir=test_root))
        try:
            path = folder / 'Local.txt'
            path.write_bytes(('\ufeff[ 2026.11.04 01:00:00 ] Система EVE > Канал изменен на Локальный: 4-HWWF*\r\n').encode('utf-16-le'))
            stat = path.stat()
            metadata = {path: ((stat.st_dev, stat.st_ino), ('local', 'Local', 'Pilot', 'utf-16-le'))}
            tracker = LocationTracker(SystemCatalog(ROWS))
            self.assertEqual(tracker.update(metadata)[0].system, '4-HWWF')
            with path.open('ab') as file:
                file.write('[ 2026.11.04 01:01:00 ] Player > Channel changed to Local: Perimeter\r\n'.encode('utf-16-le'))
            self.assertEqual(tracker.update(metadata)[0].system, '4-HWWF')
            with path.open('ab') as file:
                file.write('[ 2026.11.04 01:02:00 ] EVE System > Channel changed to Local: Jita\r\n'.encode('utf-16-le'))
            self.assertEqual(tracker.update(metadata)[0].system, 'Jita')
        finally:
            target = folder.resolve()
            assert target.is_relative_to(test_root) and target != test_root
            shutil.rmtree(target)

if __name__ == '__main__': unittest.main()
