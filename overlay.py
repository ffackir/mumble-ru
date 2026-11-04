"""Mumble RU companion: tkinter always-on-top overlay, Python 3.11+."""
import argparse
import collections
import ctypes
import json
import os
from pathlib import Path
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox

from protocol import PORT, Segmenter, decode
from lifecycle import CompanionLease
from chat_panel import ChatPanel
from floating_button import FloatingTranslationButton
from eve_theme import install_theme, divider

BASE = Path(__file__).resolve().parent
CONFIG = Path(os.getenv('LOCALAPPDATA', str(BASE))) / 'MumbleRU' / 'settings.json'
BG, FG, MUTED, ACCENT = '#0b1015', '#edf3f6', '#b2c1cb', '#82c8e8'

class App:
    def __init__(self, demo=False, managed=False, mumble_pid=0):
        self.root = tk.Tk()
        install_theme(self.root)
        self.root.title('Mumble RU — голосовой перевод')
        self.root.geometry('720x460+30+40')
        config = {}
        try:
            config = json.loads(CONFIG.read_text('utf-8'))
            geometry = config.get('voice_geometry', '')
            if isinstance(geometry, str) and geometry:
                self.root.geometry(geometry)
        except (OSError, ValueError, tk.TclError):
            pass
        self.root.minsize(640, 300)
        self.root.configure(bg=BG)
        self.topmost_var = tk.BooleanVar(value=bool(config.get('main_topmost', False)))
        self.root.attributes('-topmost', self.topmost_var.get())
        self.root.attributes('-alpha', 0.94)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.stopped = threading.Event()
        self.enabled = threading.Event()
        self.messages = queue.Queue(maxsize=256)
        self.jobs = queue.Queue(maxsize=6)
        self.users = {}
        self.captions = collections.deque(maxlen=3)
        self.last_plugin = 0
        self.last_received = 0
        self.caption_epoch = 0
        self.generation = 0
        self.locked = False
        self.demo = demo
        self.floating = None
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill='x', padx=18, pady=(14, 4))
        tk.Label(header, text='NEOCOM // ГОЛОС', fg=ACCENT, bg=BG,
                 font=('Bahnschrift', 12, 'bold')).pack(side='left')
        self.lock_button = tk.Button(header, text='Закрепить · F8', command=self.toggle_lock,
            fg=FG, bg='#182833', activebackground='#263e4c', activeforeground=FG, relief='flat')
        self.lock_button.pack(side='right')
        tk.Checkbutton(header, text='Поверх окон', variable=self.topmost_var,
            command=self.toggle_topmost, bg=BG, fg=FG, selectcolor='#182833',
            activebackground=BG, activeforeground=FG).pack(side='right', padx=10)
        divider(self.root)
        self.status = tk.StringVar(value='Ожидаю подключение плагина Mumble…')
        tk.Label(self.root, textvariable=self.status, bg=BG, fg=MUTED, anchor='w',
                 font=('Segoe UI', 10)).pack(fill='x', padx=18)
        self.speakers = tk.StringVar(value='Сейчас никто не говорит')
        tk.Label(self.root, textvariable=self.speakers, bg=BG, fg='#80d5a8', anchor='w',
                 font=('Segoe UI', 12, 'bold'), wraplength=650).pack(fill='x', padx=18, pady=(12, 8))
        self.caption_text = tk.StringVar(value='Переводы появятся здесь с именем говорящего.\nНужен Mumble 1.5+ x64. Игра — в режиме без рамки.')
        self.label = tk.Label(self.root, textvariable=self.caption_text, bg=BG, fg=FG,
            font=('Segoe UI', 15), anchor='nw', justify='left', wraplength=650)
        self.label.pack(fill='x', padx=18, pady=4)
        self.chat_window = tk.Toplevel(self.root)
        self.chat_window.title('EVE — чат и перевод')
        self.chat_window.configure(bg=BG)
        self.chat_window.geometry('800x650+760+80')
        chat_geometry = config.get('chat_geometry') or config.get('geometry')
        if isinstance(chat_geometry, str) and chat_geometry:
            try: self.chat_window.geometry(chat_geometry)
            except tk.TclError: pass
        self.chat_window.minsize(640, 460)
        self.chat_window.protocol('WM_DELETE_WINDOW', self.hide_chat)
        self.chat_topmost_var = tk.BooleanVar(value=bool(config.get('chat_topmost', False)))
        self.chat_window.attributes('-topmost', self.chat_topmost_var.get())
        chat_header = tk.Frame(self.chat_window, bg=BG)
        chat_header.pack(fill='x', padx=18, pady=(10, 0))
        tk.Label(chat_header, text='NEOCOM // КАНАЛЫ', bg=BG, fg=ACCENT,
                 font=('Bahnschrift', 11, 'bold')).pack(side='left')
        tk.Button(chat_header, text='Голос', command=self.show_voice, bg='#182833', fg=FG,
                  relief='flat').pack(side='right')
        self.chat_lock_button = tk.Button(chat_header, text='Закрепить · F8', command=self.toggle_lock,
                                          bg='#182833', fg=FG, relief='flat')
        self.chat_lock_button.pack(side='right', padx=8)
        tk.Checkbutton(chat_header, text='Поверх окон', variable=self.chat_topmost_var,
            command=self.toggle_chat_topmost, bg=BG, fg=FG, selectcolor='#182833',
            activebackground=BG, activeforeground=FG).pack(side='right', padx=8)
        divider(self.chat_window)
        self.chat_panel = ChatPanel(self.chat_window, config, self.stopped, demo=demo,
                                    on_preferences_changed=self.save_preferences)
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill='x', padx=18, pady=(4, 12))
        tk.Button(footer, text='Открыть чаты EVE', command=self.show_chat,
                  bg='#182833', fg=FG, relief='flat').pack(anchor='w', pady=(0, 8))
        self.translate_var = tk.BooleanVar(value=bool(config.get('translate_enabled', True)) and not demo)
        self.toggle = tk.Checkbutton(footer, text='Переводить речь на русский', variable=self.translate_var,
            command=self.toggle_translation, fg=FG, bg=BG, selectcolor='#182833',
            activebackground=BG, activeforeground=FG, font=('Segoe UI', 10))
        self.toggle.pack(anchor='w')
        tk.Label(footer, text='Речь распознаётся на ПК. Текст речи и включённых чатов отправляется Google.\nF8 — закрепить/освободить окно. Перетащите окно за заголовок.',
            bg=BG, fg=MUTED, justify='left', font=('Segoe UI', 9)).pack(anchor='w')
        self.root.bind('<Configure>', self.resize)
        self.hotkey = False
        self.hotkey_thread = None
        self.hotkey_ready = threading.Event()
        if os.name == 'nt':
            # No injection into the game. Global F8 unlocks a click-through window.
            self.hotkey_thread = threading.Thread(target=self.hotkey_worker, daemon=True)
            self.hotkey_thread.start()
            self.hotkey_ready.wait(0.5)
        if not self.hotkey:
            self.lock_button.configure(text='Без рамки · двойной щелчок')
            self.root.bind('<Double-Button-1>', lambda e: self.toggle_lock())
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if os.name == 'nt':
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.socket.bind(('127.0.0.1', PORT))
            self.socket.settimeout(0.12)
        except OSError as exc:
            self.stopped.set()
            self.socket.close()
            self.root.destroy()
            raise RuntimeError('Не удалось открыть порт 47831. Возможно, Mumble RU уже запущен.') from exc
        try:
            self.lease = CompanionLease(reset_stop=not managed)
            if managed and mumble_pid:
                self.lease.watch_parent(mumble_pid)
        except Exception:
            self.stopped.set()
            self.socket.close()
            self.root.destroy()
            raise
        if self.translate_var.get():
            self.enabled.set()
        self.receiver_thread = threading.Thread(target=self.receive, daemon=True)
        self.receiver_thread.start()
        threading.Thread(target=self.translate_worker, daemon=True).start()
        self.floating = FloatingTranslationButton(self, config)
        if demo:
            self.captions.append(('Fleet Commander', 'Выровняйтесь на ворота. Держите дистанцию, ждите команды на прыжок.', 'Align to the gate. Hold your jump.'))
            self.update_captions()
            self.speakers.set('● Fleet Commander     ● Logi Pilot')
            self.status.set('Демонстрация интерфейса · аудио не записывается')
        self.root.after(100, self.tick)

    def post(self, item):
        try:
            self.messages.put_nowait(item)
        except queue.Full:
            pass

    def hotkey_worker(self):
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        self.hotkey = bool(user32.RegisterHotKey(None, 0x4D52, 0x4000, 0x77))
        self.hotkey_ready.set()
        if not self.hotkey:
            return
        try:
            msg = wintypes.MSG()
            while not self.stopped.wait(0.04):
                while user32.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1):
                    self.post(('toggle_lock',))
        finally:
            user32.UnregisterHotKey(None, 0x4D52)

    def receive(self):
        segmenter = Segmenter()
        generation = self.generation
        while not self.stopped.is_set():
            try:
                packet, address = self.socket.recvfrom(2048)
                if address[0] != '127.0.0.1':
                    continue
                kind, key, seq, rate, payload = decode(packet)
                now = time.monotonic()
                self.post(('heartbeat', now))
                if generation != self.generation:
                    segmenter.streams.clear()
                    generation = self.generation
                if kind == 1:
                    self.post(('voice', key, now))
                    if self.enabled.is_set():
                        for item in segmenter.add(key, rate, payload, now):
                            self.submit(item, generation)
                elif kind == 2:
                    self.post(('name', key, payload.decode('utf-8', 'replace')[:160]))
                elif kind == 3:
                    self.post(('talk', key, rate, now))
                elif kind == 4:
                    for user in list(segmenter.streams):
                        if user[0] == key[0]:
                            segmenter.streams.pop(user, None)
                    self.post(('reset', key[0]))
                if not self.enabled.is_set():
                    segmenter.streams.clear()
            except socket.timeout:
                if self.enabled.is_set():
                    for item in segmenter.flush():
                        self.submit(item, generation)
            except ValueError:
                continue
            except OSError:
                break

    def submit(self, item, generation):
        job = (generation, time.monotonic(), item)
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            self.post(('status', 'Очередь перевода заполнена — часть речи пропущена.'))

    def translate_worker(self):
        translator = None
        while not self.stopped.is_set():
            try:
                generation, created, (key, rate, pcm) = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            if not self.enabled.is_set() or generation != self.generation:
                continue
            try:
                if translator is None:
                    self.post(('status', 'Загружаю Whisper Base. Первый запуск скачает модель (~150 МБ)…'))
                    from translator import Translator
                    translator = Translator()
                    self.post(('status', 'Whisper готов. Слушаю Mumble…'))
                if not self.enabled.is_set() or generation != self.generation:
                    continue
                text, language = translator.transcribe(pcm, rate)
                if not text or not self.enabled.is_set() or generation != self.generation:
                    continue
                try:
                    translated = translator.translate(text, language)
                except Exception:
                    self.post(('status', 'Перевод недоступен. Показан распознанный оригинал.'))
                    translated = '[оригинал] ' + text
                if self.enabled.is_set() and generation == self.generation:
                    self.post(('caption', generation, key, translated, text))
            except Exception as exc:
                self.post(('status', 'Ошибка распознавания: ' + str(exc)[:180]))

    def toggle_translation(self):
        self.generation += 1
        if self.translate_var.get():
            self.enabled.set()
            self.status.set('Перевод включён. Ожидаю речь…')
        else:
            self.enabled.clear()
            self.status.set('Перевод выключен. Показываю говорящих.')

    def update_captions(self):
        self.caption_text.set('\n\n'.join(f'{name}\n{translation}' for name, translation, _ in self.captions))

    def resize(self, event):
        if event.widget is self.root:
            self.label.configure(wraplength=max(400, event.width - 36))

    def toggle_lock(self):
        self.locked = not self.locked
        self.show_chat()
        self.chat_window.overrideredirect(self.locked)
        self.chat_window.attributes('-topmost', self.chat_topmost_var.get())
        if os.name == 'nt' and self.hotkey:
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.GetAncestor.restype = ctypes.c_void_p
            user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            hwnd = user32.GetAncestor(self.chat_window.winfo_id(), 2)
            style = user32.GetWindowLongW(hwnd, -20)
            if self.locked:
                style |= 0x80000 | 0x20  # layered + transparent: pass mouse through
            else:
                style &= ~0x20
            user32.SetWindowLongW(hwnd, -20, style)
        self.lock_button.configure(text='Освободить · F8' if self.locked else 'Закрепить · F8')
        self.chat_lock_button.configure(text='Освободить · F8' if self.locked else 'Закрепить · F8')

    def show_chat(self):
        self.chat_window.deiconify()
        self.chat_window.lift()

    def hide_chat(self):
        self.save_preferences()
        self.chat_window.withdraw()

    def show_voice(self):
        self.root.deiconify()
        self.root.lift()

    def toggle_chat_topmost(self):
        self.chat_window.attributes('-topmost', self.chat_topmost_var.get())
        self.save_preferences()

    def toggle_topmost(self):
        self.root.attributes('-topmost', self.topmost_var.get())
        self.save_preferences()

    def tick(self):
        if self.stopped.is_set():
            return
        if self.lease.shutdown_requested():
            self.close()
            return
        now = time.monotonic()
        for _ in range(256):
            try:
                item = self.messages.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == 'toggle_lock':
                self.toggle_lock()
            elif kind == 'heartbeat':
                self.last_plugin = item[1]
            elif kind in ('name', 'talk', 'voice'):
                key = item[1]
                if key not in self.users and len(self.users) >= 256:
                    self.users.pop(next(iter(self.users)))
                user = self.users.setdefault(key, {'name': f'Участник {key[1]}', 'until': 0, 'seen': now})
                user['seen'] = now
                if kind == 'name':
                    user['name'] = item[2]
                elif kind == 'voice':
                    user['until'] = item[2] + 0.7
                elif kind == 'talk':
                    user['until'] = item[3] + 2 if item[2] in (1, 2, 3) else 0
            elif kind == 'reset':
                self.users = {key: value for key, value in self.users.items() if key[0] != item[1]}
            elif kind == 'caption' and item[1] == self.generation:
                user = self.users.get(item[2], {})
                self.captions.append((user.get('name', f'Участник {item[2][1]}'), item[3], item[4]))
                self.update_captions()
                self.status.set('Перевод получен · Whisper → русский')
            elif kind == 'status':
                self.status.set(item[1])
        if not self.demo:
            active = [u['name'] for u in self.users.values() if u['until'] > now]
            self.speakers.set('● ' + '     ● '.join(active[:6]) if active else 'Сейчас никто не говорит')
            if self.last_plugin and now - self.last_plugin > 4:
                self.status.set('Нет сигнала от плагина. Проверьте, включён ли Mumble RU.')
            elif self.last_plugin and self.status.get().startswith('Ожидаю подключение'):
                self.status.set('Плагин подключён · ' + ('перевод включён' if self.enabled.is_set() else 'перевод выключен'))
        self.root.after(100, self.tick)

    def close(self):
        if self.stopped.is_set():
            return
        self.enabled.clear()
        self.stopped.set()
        if self.floating: self.floating.stop()
        self.chat_panel.stop()
        self.lease.close()
        self.socket.close()
        self.receiver_thread.join(timeout=0.3)
        if self.hotkey_thread:
            self.hotkey_thread.join(timeout=0.3)
        self.save_preferences()
        self.root.destroy()

    def save_preferences(self):
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps({'geometry': self.root.geometry(),
                                          'voice_geometry': self.root.geometry(),
                                          'chat_geometry': self.chat_window.geometry(),
                                          'chat_topmost': self.chat_topmost_var.get(),
                                          'main_topmost': self.topmost_var.get(),
                                          'translate_enabled': self.translate_var.get(),
                                          **(self.floating.preferences() if self.floating else {}),
                                          **self.chat_panel.preferences()}), 'utf-8')
        except OSError:
            pass

    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--managed', action='store_true', help='Launched automatically by Mumble')
    parser.add_argument('--mumble-pid', type=int, default=0)
    parser.add_argument('--choose-chats', action='store_true')
    parser.add_argument('--alert-settings', action='store_true')
    args = parser.parse_args()
    try:
        app = App(demo=args.demo, managed=args.managed, mumble_pid=args.mumble_pid)
        if args.alert_settings:
            app.root.after(300, app.chat_panel.open_settings)
        if args.choose_chats:
            def prompt_channels(attempt=0):
                if app.stopped.is_set(): return
                if app.chat_panel.available or attempt >= 30:
                    app.chat_panel.choose_channels()
                else:
                    app.root.after(200, lambda: prompt_channels(attempt + 1))
            app.root.after(200, prompt_channels)
        if args.smoke_test:
            app.root.after(1500, app.close)
        app.run()
    except Exception as error:
        messagebox.showerror('Mumble RU', str(error))
        raise
