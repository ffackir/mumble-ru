from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from eve_chat import ChatWatcher

SINCE = datetime(2026, 11, 4, tzinfo=timezone.utc)

def header(channel='fleet', name='Fleet / 舰队', listener='Pilot'):
    return f'\ufeff---------------------------------------------------------------\r\nChannel ID: {channel}\r\nChannel Name: {name}\r\nListener: {listener}\r\nSession started: 2026.11.04 00:00:00\r\n---------------------------------------------------------------\r\n'

def line(text='Align to the gate.', stamp='2026.11.04 00:31:00', author='Commander'):
    return f'[ {stamp} ] {author} > {text}\r\n'

class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.test_root = (BASE / 'test-data').resolve()
        self.test_root.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='eve-chat-test-', dir=self.test_root))

    def tearDown(self):
        target = self.folder.resolve()
        assert target.is_relative_to(self.test_root) and target != self.test_root
        shutil.rmtree(target)

    def log(self, name='Fleet_20261104_000000_1.txt', channel='fleet', listener='Pilot', messages=''):
        path = self.folder / name
        path.write_bytes((header(channel=channel, listener=listener) + messages).encode('utf-16-le'))
        return path

    def append(self, path, text):
        with path.open('ab') as file: file.write(text.encode('utf-16-le'))

class WatcherTests(FixtureCase):
    def test_history_is_bounded_deduplicated_and_separate_from_live_tail(self):
        contents = ''.join(line(f'History {i}') for i in range(120))
        a = self.log(listener='Pilot A', messages=contents)
        self.log(name='Fleet_20261104_000000_2.txt', listener='Pilot B', messages=contents)
        self.log(name='Local_20261104_000000_1.txt', channel='local', messages=line('Private other channel'))
        watcher = ChatWatcher(self.folder, since=SINCE)
        self.assertEqual(watcher.poll(), [])
        history = watcher.read_history({'fleet'})
        self.assertEqual(set(history), {'fleet'})
        self.assertEqual(len(history['fleet']), 100)
        self.assertEqual(len(set(history['fleet'])), 100)
        self.assertEqual(watcher.poll(), [])
        self.append(a, line('Live after history'))
        self.assertEqual([m.text for m in watcher.poll()], ['Live after history'])

    def test_touched_old_log_does_not_replace_current_session_history(self):
        old = self.log(messages=line('Old session'))
        self.log(name='Fleet_20261104_003500_1.txt', messages=line('Current session'))
        os.utime(old, (time.time() + 60, time.time() + 60))
        watcher = ChatWatcher(self.folder, since=SINCE)
        watcher.poll()
        self.assertEqual([m.text for m in watcher.read_history({'fleet'})['fleet']], ['Current session'])

    def test_existing_history_is_skipped_and_new_message_is_read_once(self):
        path = self.log(messages=line('Old message'))
        watcher = ChatWatcher(self.folder, since=SINCE)
        self.assertEqual(watcher.poll(), [])
        self.append(path, line())
        records = watcher.poll()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].text, 'Align to the gate.')
        self.assertEqual(records[0].channel, 'Fleet / 舰队')
        self.assertEqual(records[0].author, 'Commander')
        self.assertEqual(watcher.poll(), [])

    def test_utf16_split_code_unit_and_incomplete_line(self):
        path = self.log()
        watcher = ChatWatcher(self.folder, since=SINCE)
        watcher.poll()
        data = line('集合 🚀 на ворота').encode('utf-16-le')
        with path.open('ab') as file: file.write(data[:71])
        self.assertEqual(watcher.poll(), [])
        with path.open('ab') as file: file.write(data[71:])
        self.assertEqual(watcher.poll()[0].text, '集合 🚀 на ворота')

    def test_partial_line_at_initial_attachment_is_not_translated(self):
        path = self.log(messages=line('Do not import this')[:-8])
        watcher = ChatWatcher(self.folder, since=SINCE)
        watcher.poll()
        self.append(path, 'tail\r\n' + line('New line'))
        self.assertEqual([m.text for m in watcher.poll()], ['New line'])

    def test_new_session_is_detected_and_old_timestamps_ignored(self):
        watcher = ChatWatcher(self.folder, since=SINCE)
        watcher.poll()
        self.log(messages=line('Yesterday', stamp='2026.11.03 23:59:59') + line('New session'))
        self.assertEqual([m.text for m in watcher.poll()], ['New session'])

    def test_rotation_and_truncation(self):
        path = self.log(messages=line('x' * 3000))
        watcher = ChatWatcher(self.folder, since=SINCE)
        watcher.poll()
        path.write_bytes((header() + line('After truncation')).encode('utf-16-le'))
        self.assertEqual([m.text for m in watcher.poll()], ['After truncation'])
        other = self.log(name='Fleet_20261104_003500_1.txt', messages=line('After rotation'))
        os.utime(other, (time.time() + 1, time.time() + 1))
        self.assertEqual([m.text for m in watcher.poll()], ['After rotation'])

    def test_duplicate_logs_from_two_characters_are_deduplicated(self):
        a = self.log(listener='Pilot A')
        b = self.log(name='Fleet_20261104_000000_2.txt', listener='Pilot B')
        watcher = ChatWatcher(self.folder, since=SINCE)
        watcher.poll()
        self.append(a, line())
        self.append(b, line())
        self.assertEqual(len(watcher.poll()), 1)

    def test_incomplete_header_is_retried_and_system_notices_ignored(self):
        watcher = ChatWatcher(self.folder, since=SINCE)
        watcher.poll()
        path = self.folder / 'Fleet_20261104_000000_1.txt'
        path.write_bytes('\ufeffChannel Name: Fle'.encode('utf-16-le'))
        self.assertEqual(watcher.poll(), [])
        path.write_bytes((header() + line('Connected', author='EVE System') + line('Hello')).encode('utf-16-le'))
        self.assertEqual([m.text for m in watcher.poll()], ['Hello'])

class PanelTests(FixtureCase):
    def test_history_selects_freshest_selected_chat_without_sound_or_translation(self):
        from chat_panel import ChatPanel
        self.log(messages=line('Older fleet history', stamp='2026.11.03 20:00:00'))
        self.log(name='Local_20261104_000000_1.txt', channel='local',
                 messages=line('Fresh local history', stamp='2026.11.03 21:00:00'))
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        with patch('chat_panel.TextTranslator.translate') as translate, patch('chat_panel.play_alert') as sound:
            panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet', 'local']}, stopped)
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not panel.latest_activity:
                    root.update(); time.sleep(.02)
                self.assertEqual(panel.active_channel(), 'local')
                shown = panel.tabs['local'][1].get('1.0', 'end')
                self.assertIn('Fresh local history', shown)
                self.assertIn('Открыто', shown)
                self.assertNotIn('Older fleet history', shown)
                self.assertFalse(any(panel.unread.values()))
                translate.assert_not_called(); sound.assert_not_called()
                panel.notebook.select(panel.tabs['fleet'][0]); panel.tab_changed()
                panel.select_current_chat()
                self.assertEqual(panel.active_channel(), 'local')
            finally:
                stopped.set(); panel.stop(); root.destroy()

    def test_hidden_active_chat_stays_unread_until_visible_at_bottom(self):
        from chat_panel import ChatPanel
        from eve_chat import ChatMessage
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet']}, stopped)
        try:
            message = ChatMessage('fleet', 'Fleet', panel.opened_stamp, 'Pilot', 'New incoming text')
            panel.post(('caption', panel.revision, message, 'Новый текст'))
            panel.drain()
            self.assertEqual(panel.unread['fleet'], 1)
            text = panel.tabs['fleet'][1]
            self.assertIn('Новые сообщения', text.get('1.0', 'end'))
            self.assertTrue(text.tag_ranges('unread'))
            panel.tab_changed()
            self.assertEqual(panel.unread['fleet'], 1, 'Hidden tab is not read')
            with patch.object(panel, 'is_reading', return_value=False): panel.mark_visible_read()
            self.assertEqual(panel.unread['fleet'], 1, 'Scrolling history must not read new messages')
            with patch.object(panel, 'is_reading', return_value=True): panel.mark_visible_read()
            self.assertEqual(panel.unread['fleet'], 0)
            self.assertFalse(text.tag_ranges('unread'))
            panel.post(('caption', panel.revision, message, 'Обновлённый перевод'))
            panel.drain()
            self.assertEqual(len(panel.channel_history['fleet']), 1)
            self.assertEqual(panel.unread['fleet'], 0, 'Translation update is not a second message')
        finally:
            stopped.set(); panel.stop(); root.destroy()

    def test_selected_message_translation_does_not_need_a_reply(self):
        from chat_panel import ChatPanel
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet']}, stopped)
        try:
            panel.notebook.select(panel.tabs['fleet'][0]); panel.tab_changed()
            text = panel.tabs['fleet'][1]
            text.configure(state='normal'); text.delete('1.0', 'end'); text.insert('1.0', '你好朋友')
            text.tag_add('sel', '1.0', '1.end'); text.configure(state='disabled')
            with patch('chat_panel.TextTranslator.translate', return_value='Привет, друг') as translator:
                panel.open_message_translation()
                end = time.monotonic() + 2
                output, status, button, value = panel.message_widgets
                while time.monotonic() < end and not value['text']:
                    root.update(); time.sleep(.02)
                self.assertEqual(output.get('1.0', 'end').strip(), 'Привет, друг')
                translator.assert_called_once_with('你好朋友')
                self.assertIsNone(panel.reply_dialog)
        finally:
            stopped.set(); panel.stop(); root.destroy()

    def test_reply_button_requires_selection_and_opens_compose_dialog(self):
        from chat_panel import ChatPanel
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet']}, stopped)
        try:
            panel.notebook.select(panel.tabs['fleet'][0]); panel.tab_changed()
            panel.open_reply()
            self.assertIsNone(panel.reply_dialog)
            text = panel.tabs['fleet'][1]
            text.configure(state='normal'); text.delete('1.0', 'end'); text.insert('1.0', 'Hello, fleet!')
            text.tag_add('sel', '1.0', '1.end'); text.configure(state='disabled')
            with patch('chat_panel.TextTranslator.translate', return_value='Привет, флот!'):
                panel.open_reply()
                self.assertTrue(panel.reply_dialog.winfo_exists())
                self.assertEqual(panel.reply_dialog.title(), 'Ответить с переводом')
        finally:
            stopped.set(); panel.stop(); root.destroy()

    def test_jump_buttons_and_manual_origin_override(self):
        from chat_panel import ChatPanel
        from eve_chat import ChatMessage
        from eve_routes import PlayerLocation
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet'],
                                'route_origin_mode': 'manual', 'route_manual_origin': 'Jita'}, stopped)
        try:
            panel.locations = [PlayerLocation('Pilot', 30000144, 'Perimeter', '2026.11.04 01:00:00')]
            self.assertEqual(panel.current_origin()[:2], (30000142, 'Jita'))
            message = ChatMessage('fleet', 'Fleet', '2026.11.04 01:00:00', 'FC', 'Go to Perimeter')
            with patch.object(panel.route_client, 'jumps', return_value=1) as route, patch('chat_panel.play_alert'):
                panel.post(('caption', panel.revision, message, 'Go to Perimeter'))
                panel.drain()
                self.assertEqual(len(panel.route_buttons['fleet']), 1)
                old = panel.route_buttons['fleet'][0]
                panel.render_history('fleet')
                self.assertFalse(old.winfo_exists())
                panel.open_route(30000144, 'Perimeter')
                end = time.monotonic() + 2
                while time.monotonic() < end and ('1 прыжк.' not in panel.route_result.get() or
                        panel.route_buttons['fleet'][0].cget('text') != 'Прыжки: 1'):
                    root.update(); time.sleep(.02)
                self.assertIn('1 прыжк.', panel.route_result.get())
                self.assertEqual(panel.route_buttons['fleet'][0].cget('text'), 'Прыжки: 1')
                route.assert_any_call(30000142, 30000144)
            panel.origin_mode = 'auto'
            panel.locations = [PlayerLocation('Pilot', 30000144, 'Perimeter', '2026.11.04 01:00:00')]
            self.assertEqual(panel.current_origin()[:2], (30000144, 'Perimeter'))
        finally:
            stopped.set(); panel.stop(); root.destroy()

    def test_nearby_sound_includes_three_zero_and_respects_chat_mute(self):
        from chat_panel import ChatPanel
        from eve_chat import ChatMessage
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet'],
                                'route_origin_mode': 'manual', 'route_manual_origin': 'Jita'}, stopped)
        try:
            key = (30000142, 30000144)
            with patch('chat_panel.play_alert') as sound:
                for count in (0, 1, 2, 3):
                    panel.alerts.last_sound = float('-inf')
                    message = ChatMessage('fleet', 'Fleet', f'2026.11.04 01:00:0{count}', 'FC', f'Perimeter {count}')
                    panel.nearby_alert(key, message, count)
                    panel.nearby_alert(key, message, count)
                self.assertEqual(sound.call_count, 4)
                message = ChatMessage('fleet', 'Fleet', '2026.11.04 01:00:08', 'FC', 'Perimeter')
                panel.alerts.last_sound = float('-inf')
                panel.nearby_alert(key, message, 4)
                panel.muted_channels.add('fleet')
                panel.nearby_alert(key, message, 3)
                self.assertEqual(sound.call_count, 4)
            self.assertTrue(panel.preferences()['chat_nearby_sound'])
        finally:
            stopped.set(); panel.stop(); root.destroy()

    def test_per_chat_sound_toggle_is_independent(self):
        from chat_panel import ChatPanel
        from eve_chat import ChatMessage
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        with patch('chat_panel.play_alert') as sound:
            panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet', 'local'],
                                    'chat_filter_words': ['warp']}, stopped)
            try:
                panel.sound_toggles['fleet'].set(False)
                panel.toggle_chat_sound('fleet')
                panel.alert_for(ChatMessage('fleet', 'Fleet', '2026.11.04 01:00:00', 'FC', 'warp'))
                sound.assert_not_called()
                panel.alert_for(ChatMessage('local', 'Local', '2026.11.04 01:00:00', 'Pilot', 'warp'))
                sound.assert_called_once()
                self.assertEqual(panel.preferences()['chat_muted_channels'], ['fleet'])
                panel.sound_toggles['fleet'].set(True)
                panel.toggle_chat_sound('fleet')
                self.assertFalse(panel.preferences()['chat_muted_channels'])
            finally:
                stopped.set(); panel.stop(); root.destroy()

    def test_floating_translation_button_controls_active_chat(self):
        from chat_panel import ChatPanel
        from floating_button import FloatingTranslationButton
        from types import SimpleNamespace
        from unittest.mock import Mock
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet', 'local']}, stopped)
        saved = Mock()
        panel.on_preferences_changed = saved
        app = SimpleNamespace(root=root, stopped=stopped, chat_panel=panel, save_preferences=saved, close=Mock())
        floating = FloatingTranslationButton(app, {})
        try:
            root.update_idletasks()
            self.assertEqual(floating.window.attributes('-transparentcolor'), floating.KEY)
            self.assertFalse(floating.window.attributes('-topmost'))
            floating.topmost_var.set(True)
            floating.toggle_topmost()
            root.update_idletasks()
            self.assertTrue(floating.window.attributes('-topmost'))
            self.assertTrue(floating.preferences()['floating_topmost'])
            floating.topmost_var.set(False)
            floating.toggle_topmost()
            panel.notebook.select(panel.tabs['fleet'][0]); panel.tab_changed()
            floating.click()
            self.assertFalse(panel.channel_state('fleet')[0])
            self.assertTrue(panel.channel_state('local')[0])
            self.assertIn('ВЫКЛ', floating.canvas.itemcget(floating.title, 'text'))
            floating.click()
            self.assertTrue(panel.channel_state('fleet')[0])
            self.assertIn('ВКЛ', floating.canvas.itemcget(floating.title, 'text'))
            floating.press(SimpleNamespace(x_root=100, y_root=100))
            floating.drag(SimpleNamespace(x_root=120, y_root=115))
            floating.release(SimpleNamespace(x=60))
            self.assertTrue(panel.channel_state('fleet')[0], 'Dragging must not toggle translation')
            saved.assert_called()
        finally:
            stopped.set(); floating.stop(); panel.stop(); root.destroy()

    def test_per_chat_translation_toggle_keeps_original_and_other_chat_working(self):
        from chat_panel import ChatPanel
        a = self.log()
        b = self.log(name='Local_20261104_000000_1.txt', channel='local')
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        with patch('chat_panel.TextTranslator.translate', side_effect=lambda text: 'RU ' + text) as translate:
            panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet', 'local']}, stopped)
            def wait_for(predicate):
                end = time.monotonic() + 3
                while time.monotonic() < end and not predicate():
                    root.update(); time.sleep(.02)
                self.assertTrue(predicate())
            try:
                wait_for(lambda: len(panel.available) == 2)
                panel.chat_toggles['fleet'].set(False)
                panel.toggle_chat('fleet')
                self.assertEqual(panel.preferences()['chat_paused_channels'], ['fleet'])
                stamp = datetime.now(timezone.utc).strftime('%Y.%m.%d %H:%M:%S')
                self.append(a, line('Original fleet', stamp=stamp))
                self.append(b, line('Translated local', stamp=stamp))
                wait_for(lambda: len(panel.history) == 2)
                self.assertEqual([call.args[0] for call in translate.call_args_list], ['Translated local'])
                self.assertIn('Original fleet', panel.tabs['fleet'][1].get('1.0', 'end'))
                self.assertNotIn('RU Original fleet', panel.tabs['fleet'][1].get('1.0', 'end'))
                panel.chat_toggles['fleet'].set(True)
                panel.toggle_chat('fleet')
                self.append(a, line('New fleet', stamp=stamp))
                wait_for(lambda: len(panel.history) == 3)
                self.assertIn('RU New fleet', panel.tabs['fleet'][1].get('1.0', 'end'))
                self.assertFalse(panel.preferences()['chat_paused_channels'])
            finally:
                stopped.set(); panel.stop(); root.destroy()

    def test_tabs_keep_chats_separate_unread_and_single_sound(self):
        from chat_panel import ChatPanel
        from eve_chat import ChatMessage
        root = tk.Tk(); root.withdraw()
        stopped = threading.Event()
        with patch('chat_panel.play_alert') as sound:
            panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet', 'local'],
                                    'chat_filter_words': ['warp']}, stopped)
            try:
                panel.available = [('fleet', 'Fleet'), ('local', 'Local')]
                panel.sync_tabs()
                panel.notebook.select(panel.tabs['fleet'][0]); panel.tab_changed()
                a = ChatMessage('fleet', 'Fleet', '2026.11.04 01:00:00', 'FC', 'warp now')
                b = ChatMessage('local', 'Local', '2026.11.04 01:00:01', 'Pilot', 'hello')
                panel.post(('alert', panel.revision, a))
                panel.post(('caption', panel.revision, a, 'Прыгайте'))
                panel.post(('caption', panel.revision, b, 'Привет'))
                panel.drain()
                sound.assert_called_once()
                self.assertIn('Прыгайте', panel.tabs['fleet'][1].get('1.0', 'end'))
                self.assertNotIn('Привет', panel.tabs['fleet'][1].get('1.0', 'end'))
                self.assertIn('Привет', panel.tabs['local'][1].get('1.0', 'end'))
                self.assertEqual(panel.unread['local'], 1)
                self.assertEqual(panel.active_channel(), 'fleet')
                panel.notebook.select(panel.tabs['local'][0])
                with patch.object(panel, 'is_reading', return_value=True): panel.tab_changed()
                self.assertEqual(panel.unread['local'], 0)
                self.assertTrue(panel.preferences()['chat_sound_enabled'])
                self.assertEqual(panel.preferences()['chat_filter_words'], ['warp'])
            finally:
                stopped.set(); panel.stop(); root.destroy()

    def test_old_all_channels_setting_requires_explicit_selection(self):
        from chat_panel import ChatPanel
        path = self.log()
        root = tk.Tk()
        root.withdraw()
        stopped = threading.Event()
        with patch('chat_panel.TextTranslator.translate') as translate:
            panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': None}, stopped)
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not panel.available:
                    root.update(); time.sleep(.02)
                self.assertTrue(panel.available)
                self.assertEqual(panel.preferences()['chat_channels'], [])
                self.assertIn('Чат не выбран', panel.selection_label.get())
                stamp = datetime.now(timezone.utc).strftime('%Y.%m.%d %H:%M:%S')
                self.append(path, line('Do not translate without selection', stamp=stamp))
                end = time.monotonic() + .7
                while time.monotonic() < end:
                    root.update(); time.sleep(.02)
                translate.assert_not_called()
                self.assertFalse(panel.history)
            finally:
                stopped.set()
                panel.stop()
                root.destroy()

    def test_selected_channel_only_pause_and_private_metadata(self):
        from chat_panel import ChatPanel
        a = self.log()
        b = self.log(name='Local_20261104_000000_1.txt', channel='local')
        root = tk.Tk()
        root.withdraw()
        stopped = threading.Event()
        # Translate fixture messages only. No real chat is transmitted in tests.
        with patch('chat_panel.TextTranslator.translate', return_value='На ворота') as translate:
            panel = ChatPanel(root, {'chat_folder': str(self.folder), 'chat_channels': ['fleet']}, stopped)
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not panel.available:
                    root.update(); time.sleep(.02)
                self.assertEqual(len(panel.available), 2)
                stamp = datetime.now(timezone.utc).strftime('%Y.%m.%d %H:%M:%S')
                self.append(a, line('Current selected', stamp=stamp))
                self.append(b, line('Current unselected', stamp=stamp))
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not panel.history:
                    root.update(); time.sleep(.02)
                self.assertEqual(len(panel.history), 1)
                self.assertEqual(translate.call_args.args, ('Current selected',))
                shown = panel.text.get('1.0', 'end')
                self.assertIn('Commander', shown)
                self.assertIn('На ворота', shown)
                panel.enabled_var.set(False)
                panel.toggle()
                self.append(a, line('Paused', stamp=stamp))
                end = time.monotonic() + .7
                while time.monotonic() < end:
                    root.update(); time.sleep(.02)
                self.assertEqual(len(panel.history), 1)
            finally:
                stopped.set()
                panel.stop()
                root.destroy()

if __name__ == '__main__': unittest.main()
