"""Independent text-chat UI and workers; no Whisper or Mumble needed."""
from collections import deque, OrderedDict
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from eve_chat import ChatWatcher, default_chat_folder
from translator import TextTranslator, translate_reply
from alerts import WordAlerts, parse_words, play_alert
from eve_routes import default_catalog, LocationTracker, RouteClient
from system_entry import SystemEntry

BG, FG, MUTED, ACCENT = '#0b1015', '#edf3f6', '#b2c1cb', '#82c8e8'

class ChatPanel:
    def __init__(self, parent, config, stopped, demo=False, on_preferences_changed=None):
        self.parent, self.stopped = parent, stopped
        self.state_lock = threading.Lock()
        self.folder = Path(config.get('chat_folder') or default_chat_folder())
        selection = config.get('chat_channels')
        self.selected = frozenset(selection) if isinstance(selection, list) else frozenset()
        self.paused_channels = set(config.get('chat_paused_channels', []))
        self.channel_epochs = {}
        self.chat_toggles = {}
        self.chat_labels = {}
        self.muted_channels = set(config.get('chat_muted_channels', []))
        self.sound_toggles = {}
        self.active = bool(config.get('chat_enabled', True)) and not demo
        self.revision = 0
        self.on_preferences_changed = on_preferences_changed
        self.alerts = WordAlerts(config.get('chat_filter_words', []), config.get('chat_sound_enabled', True))
        self.catalog = default_catalog()
        self.route_client = RouteClient()
        self.locations = []
        self.route_pilot = config.get('route_pilot', '')
        self.origin_mode = config.get('route_origin_mode', 'auto')
        self.manual_origin = config.get('route_manual_origin', '')
        self.route_buttons = {}
        self.route_dialog = None
        self.route_generation = 0
        self.reply_generation = 0
        self.reply_dialog = None
        self.reply_dialog_generation = 0
        self.message_generation = 0
        self.message_dialog = None
        self.inline_counts = OrderedDict()
        self.route_pending = set()
        self.route_jobs = queue.Queue(maxsize=100)
        self.nearby_waiters = OrderedDict()
        self.nearby_enabled = bool(config.get('chat_nearby_sound', True))
        self.background_transparency = max(0, min(65, int(config.get('background_transparency', 15))))
        self.clear_background = bool(config.get('clear_chat_background', False))
        self.available = []
        self.events = queue.Queue(maxsize=256)
        self.jobs = queue.Queue(maxsize=40)
        self.history = deque(maxlen=100)
        self.channel_history = {}
        self.tabs = {}
        self.unread = {}
        self.frame = tk.Frame(parent, bg=BG)
        self.frame.pack(fill='both', expand=True, padx=18, pady=(10, 4))
        bar = tk.Frame(self.frame, bg=BG)
        bar.pack(fill='x')
        self.enabled_var = tk.BooleanVar(value=self.active)
        tk.Checkbutton(bar, text='Чаты EVE → русский', variable=self.enabled_var,
            command=self.toggle, fg=FG, bg=BG, selectcolor='#182833',
            activebackground=BG, activeforeground=FG, font=('Segoe UI', 11, 'bold')).pack(side='left')
        for label, callback in (('Настройки…', self.open_settings), ('Папка…', self.choose_folder), ('Выбрать чаты…', self.choose_channels)):
            tk.Button(bar, text=label, command=callback, fg=FG, bg='#182833',
                activebackground='#263e4c', activeforeground=FG, relief='flat').pack(side='right', padx=(6, 0))
        self.selection_label = tk.StringVar()
        tk.Label(self.frame, textvariable=self.selection_label, bg=BG, fg=ACCENT,
                 font=('Segoe UI', 10), anchor='w', wraplength=680).pack(fill='x', pady=(4, 0))
        self.update_selection_label()
        location_bar = tk.Frame(self.frame, bg=BG)
        location_bar.pack(fill='x', pady=(4, 0))
        self.origin_label = tk.StringVar()
        tk.Label(location_bar, textvariable=self.origin_label, bg=BG, fg=MUTED,
                 anchor='w', font=('Segoe UI', 9)).pack(side='left', fill='x', expand=True)
        tk.Button(location_bar, text='Исходная система…', command=self.open_origin_settings,
                  bg='#182833', fg=FG, relief='flat').pack(side='right')
        self.update_origin_label()
        self.status = tk.StringVar(value='Ищу чат-логи EVE. Переводится только новая переписка…')
        tk.Label(self.frame, textvariable=self.status, bg=BG, fg=MUTED,
                 font=('Segoe UI', 9), anchor='w', wraplength=680).pack(fill='x', pady=(4, 6))
        area = tk.Frame(self.frame, bg=BG)
        area.pack(fill='both', expand=True)
        style = ttk.Style(parent)
        if 'clam' in style.theme_names(): style.theme_use('clam')
        style.configure('Chat.TNotebook', background=BG, borderwidth=1, bordercolor='#334957', tabmargins=(0, 0, 0, 0))
        style.configure('Chat.TNotebook.Tab', background='#131e27', foreground=MUTED,
                        padding=(12, 6), font=('Bahnschrift', 10))
        style.map('Chat.TNotebook.Tab', background=[('selected', '#263e4c'), ('active', '#1d303c')],
                  foreground=[('selected', FG), ('active', FG)])
        self.notebook = ttk.Notebook(area, style='Chat.TNotebook')
        self.notebook.pack(fill='both', expand=True)
        self.notebook.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.empty = tk.Frame(self.notebook, bg='#111a22')
        tk.Label(self.empty, text='Выберите чаты — каждый откроется в отдельной вкладке.',
                 bg='#111a22', fg=MUTED, wraplength=540).pack(pady=30, padx=12)
        self.notebook.add(self.empty, text='Чаты EVE')
        self.text = None
        self.sync_tabs()
        self.apply_appearance(self.background_transparency, self.clear_background)
        active_tab = config.get('chat_active_tab')
        if active_tab in self.tabs:
            self.notebook.select(self.tabs[active_tab][0])
            self.tab_changed()
        self.post(('status', 'Ожидаю новые сообщения. Старые логи не переводятся.'))
        self.watcher_thread = threading.Thread(target=self.watch, daemon=True)
        self.translation_thread = threading.Thread(target=self.translate, daemon=True)
        self.watcher_thread.start()
        self.translation_thread.start()
        self.route_thread = threading.Thread(target=self.route_worker, daemon=True)
        self.route_thread.start()
        self.after_id = parent.after(100, self.drain)

    def create_chat_text(self, frame):
        text = tk.Text(frame, height=8, bg=BG if self.clear_background else '#111a22', fg=FG, insertbackground=FG,
            font=('Segoe UI', 11), wrap='word', relief='flat', padx=10, pady=8,
            state='disabled', selectbackground='#284754')
        scrollbar = tk.Scrollbar(frame, command=text.yview)
        scrollbar.pack(side='right', fill='y')
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side='left', fill='both', expand=True)
        text.tag_configure('meta', foreground=ACCENT, font=('Segoe UI', 9, 'bold'))
        text.tag_configure('original', foreground=MUTED, font=('Segoe UI', 9))
        text.tag_configure('translated', foreground=FG)
        text.tag_configure('match', foreground='#edc780', font=('Segoe UI', 9, 'bold'))
        menu = tk.Menu(text, tearoff=False)
        menu.add_command(label='Перевести выделенное на русский', command=lambda: self.open_message_translation(text))
        menu.add_command(label='Ответить с переводом…', command=lambda: self.open_reply(text))
        text.bind('<Button-3>', lambda event: menu.tk_popup(event.x_root, event.y_root))
        return text

    def sync_tabs(self):
        names = dict(self.available)
        for channel_id in list(self.tabs):
            if channel_id not in self.selected:
                frame, _ = self.tabs.pop(channel_id)
                self.notebook.forget(frame)
                frame.destroy()
                self.chat_toggles.pop(channel_id, None)
                self.chat_labels.pop(channel_id, None)
                self.sound_toggles.pop(channel_id, None)
                self.channel_history.pop(channel_id, None)
                self.route_buttons.pop(channel_id, None)
                self.unread.pop(channel_id, None)
        for channel_id in sorted(self.selected, key=lambda key: names.get(key, key).casefold()):
            if channel_id not in self.tabs:
                frame = tk.Frame(self.notebook, bg=BG)
                controls = tk.Frame(frame, bg=BG)
                controls.pack(fill='x', padx=8, pady=5)
                tk.Button(controls, text='Ответить ↗', command=self.open_reply,
                          bg='#182833', fg=FG, relief='flat', font=('Segoe UI', 9)).pack(side='right', padx=(8, 0))
                tk.Button(controls, text='Перевести', command=self.open_message_translation,
                          bg='#182833', fg=ACCENT, relief='flat', font=('Segoe UI', 9)).pack(side='right', padx=(6, 0))
                label = tk.Label(controls, text=names.get(channel_id, channel_id), bg=BG, fg=ACCENT,
                                 font=('Segoe UI', 10, 'bold'))
                label.pack(side='left')
                self.chat_labels[channel_id] = label
                sound = tk.BooleanVar(value=channel_id not in self.muted_channels)
                self.sound_toggles[channel_id] = sound
                tk.Checkbutton(controls, text='Оповещение', variable=sound,
                    command=lambda key=channel_id: self.toggle_chat_sound(key), bg=BG, fg=FG,
                    selectcolor='#182833', activebackground=BG, activeforeground=FG).pack(side='right')
                enabled = tk.BooleanVar(value=channel_id not in self.paused_channels)
                self.chat_toggles[channel_id] = enabled
                tk.Checkbutton(controls, text='Вкл перевод', variable=enabled,
                    command=lambda key=channel_id: self.toggle_chat(key), bg=BG, fg=FG,
                    selectcolor='#182833', activebackground=BG, activeforeground=FG).pack(side='right')
                body = tk.Frame(frame, bg=BG)
                body.pack(fill='both', expand=True)
                text = self.create_chat_text(body)
                self.tabs[channel_id] = (frame, text)
                self.channel_history[channel_id] = deque(maxlen=100)
                self.notebook.add(frame, text=names.get(channel_id, channel_id))
            self.chat_labels[channel_id].configure(text=names.get(channel_id, channel_id))
            self.update_tab_title(channel_id)
        if self.tabs:
            if str(self.empty) in self.notebook.tabs(): self.notebook.forget(self.empty)
        elif str(self.empty) not in self.notebook.tabs():
            self.notebook.add(self.empty, text='Чаты EVE')
        self.tab_changed()

    def active_channel(self):
        selected = self.notebook.select()
        return next((key for key, (frame, _) in self.tabs.items() if str(frame) == selected), None)

    def update_tab_title(self, channel_id):
        if channel_id not in self.tabs: return
        name = dict(self.available).get(channel_id, channel_id)
        count = self.unread.get(channel_id, 0)
        suffix = ' · без перевода' if channel_id in self.paused_channels else ''
        self.notebook.tab(self.tabs[channel_id][0], text=name + suffix + (f' ({count})' if count else ''))

    def channel_state(self, channel_id):
        with self.state_lock:
            return channel_id not in self.paused_channels, self.channel_epochs.get(channel_id, 0)

    def toggle_chat_sound(self, channel_id):
        enabled = self.sound_toggles[channel_id].get()
        if enabled: self.muted_channels.discard(channel_id)
        else: self.muted_channels.add(channel_id)
        name = dict(self.available).get(channel_id, channel_id)
        self.status.set(f'{name}: оповещение {"включено" if enabled else "выключено"}.')
        if self.on_preferences_changed: self.on_preferences_changed()

    def toggle_chat(self, channel_id):
        enabled = self.chat_toggles[channel_id].get()
        with self.state_lock:
            if enabled: self.paused_channels.discard(channel_id)
            else: self.paused_channels.add(channel_id)
            self.channel_epochs[channel_id] = self.channel_epochs.get(channel_id, 0) + 1
        self.update_tab_title(channel_id)
        name = dict(self.available).get(channel_id, channel_id)
        self.status.set(f'{name}: перевод включён.' if enabled else f'{name}: показ оригинала, перевод выключен.')
        if self.on_preferences_changed: self.on_preferences_changed()

    def tab_changed(self, event=None):
        channel_id = self.active_channel()
        self.text = self.tabs[channel_id][1] if channel_id in self.tabs else None
        if channel_id:
            self.unread[channel_id] = 0
            self.update_tab_title(channel_id)

    def snapshot(self):
        with self.state_lock:
            return self.folder, self.selected, self.active, self.revision

    def preferences(self):
        folder, selected, active, _ = self.snapshot()
        return {'chat_folder': str(folder), 'chat_channels': sorted(selected),
                'chat_enabled': active, 'chat_ui_version': 1,
                'chat_active_tab': self.active_channel(),
                'chat_paused_channels': sorted(self.paused_channels),
                'chat_muted_channels': sorted(self.muted_channels),
                'route_origin_mode': self.origin_mode, 'route_manual_origin': self.manual_origin,
                'route_pilot': self.route_pilot,
                'chat_nearby_sound': self.nearby_enabled,
                'background_transparency': self.background_transparency,
                'clear_chat_background': self.clear_background,
                'chat_filter_words': list(self.alerts.words), 'chat_sound_enabled': self.alerts.enabled}

    def open_message_translation(self, source=None):
        source = source or self.text
        try: selected = source.get('sel.first', 'sel.last').strip() if source is not None else ''
        except tk.TclError: selected = ''
        if not selected:
            self.status.set('Выделите текст сообщения и нажмите «Перевести».')
            return
        if len(selected) > 4000:
            self.status.set('Выделите фрагмент не длиннее 4000 символов.')
            return
        self.message_generation += 1
        generation = self.message_generation
        if self.message_dialog is not None and self.message_dialog.winfo_exists(): self.message_dialog.destroy()
        dialog = self.message_dialog = tk.Toplevel(self.parent)
        dialog.title('Перевод сообщения на русский')
        dialog.geometry('640x440')
        dialog.configure(bg=BG)
        dialog.transient(self.parent)
        tk.Label(dialog, text='ПЕРЕВОД НА РУССКИЙ', bg=BG, fg=ACCENT,
                 font=('Bahnschrift', 11, 'bold')).pack(anchor='w', padx=18, pady=(16, 8))
        output = tk.Text(dialog, height=8, bg='#111a22', fg=FG, wrap='word', font=('Segoe UI', 13), state='disabled')
        output.pack(fill='both', expand=True, padx=18)
        status = tk.StringVar(value='Перевожу выбранное сообщение…')
        tk.Label(dialog, textvariable=status, bg=BG, fg=MUTED, wraplength=600).pack(fill='x', padx=18, pady=8)
        tk.Label(dialog, text='Оригинал: ' + selected[:250], bg=BG, fg=MUTED,
                 wraplength=600, justify='left').pack(anchor='w', padx=18)
        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill='x', padx=18, pady=16)
        result = {'text': ''}
        def copy():
            self.parent.clipboard_clear()
            self.parent.clipboard_append(result['text'])
            status.set('Перевод скопирован.')
        copy_button = tk.Button(buttons, text='Скопировать перевод', command=copy, state='disabled')
        copy_button.pack(side='right')
        tk.Button(buttons, text='Ответить с переводом…', command=lambda: self.open_reply(selected_text=selected)).pack(side='left')
        self.message_widgets = (output, status, copy_button, result)
        def run():
            try: self.post(('message_translation', generation, TextTranslator().translate(selected), ''))
            except Exception: self.post(('message_translation', generation, '', 'Перевод недоступен. Закройте окно и повторите позже.'))
        threading.Thread(target=run, daemon=True).start()

    def open_reply(self, source=None, selected_text=None):
        source = source or self.text
        try: selected = selected_text if selected_text is not None else source.get('sel.first', 'sel.last').strip() if source is not None else ''
        except tk.TclError: selected = ''
        if not selected:
            self.status.set('Выделите исходный текст сообщения и нажмите «Ответить ↗».')
            return
        if len(selected) > 4000:
            self.status.set('Выделите фрагмент не длиннее 4000 символов.')
            return
        self.reply_generation += 1
        self.reply_dialog_generation += 1
        if self.reply_dialog is not None and self.reply_dialog.winfo_exists(): self.reply_dialog.destroy()
        dialog = self.reply_dialog = tk.Toplevel(self.parent)
        dialog.title('Ответить с переводом')
        dialog.geometry('640x680')
        dialog.minsize(560, 540)
        dialog.configure(bg=BG)
        dialog.transient(self.parent)
        tk.Label(dialog, text='ИСХОДНОЕ СООБЩЕНИЕ', bg=BG, fg=ACCENT,
                 font=('Bahnschrift', 10, 'bold')).pack(anchor='w', padx=18, pady=(16, 6))
        preview = tk.Text(dialog, height=3, bg='#111a22', fg=MUTED, wrap='word', font=('Segoe UI', 10))
        preview.insert('1.0', selected)
        preview.configure(state='disabled')
        preview.pack(fill='x', padx=18)
        incoming = tk.StringVar(value='Перевод сообщения: загружается…')
        tk.Label(dialog, textvariable=incoming, bg=BG, fg=FG, wraplength=600,
                 justify='left').pack(fill='x', padx=18, pady=(6, 2))
        self.reply_incoming = incoming
        incoming_generation = self.reply_dialog_generation
        def translate_incoming():
            try: value = TextTranslator().translate(selected)
            except Exception: value = 'Не удалось перевести исходное сообщение.'
            self.post(('reply_incoming', incoming_generation, value))
        threading.Thread(target=translate_incoming, daemon=True).start()
        tk.Label(dialog, text='ВАШ ОТВЕТ ПО-РУССКИ', bg=BG, fg=ACCENT,
                 font=('Bahnschrift', 10, 'bold')).pack(anchor='w', padx=18, pady=(14, 6))
        draft = tk.Text(dialog, height=5, bg='#111a22', fg=FG, insertbackground=FG, wrap='word', font=('Segoe UI', 12))
        draft.pack(fill='both', expand=True, padx=18)
        status = tk.StringVar(value='Язык ответа будет определён по выделенному тексту. Выделяйте оригинал сообщения.')
        tk.Label(dialog, textvariable=status, bg=BG, fg=MUTED, wraplength=600, justify='left').pack(fill='x', padx=18, pady=8)
        output = tk.Text(dialog, height=5, bg='#111a22', fg=FG, wrap='word', state='disabled', font=('Segoe UI', 12))
        output.pack(fill='both', expand=True, padx=18)
        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill='x', padx=18, pady=16)
        result_value = {'text': ''}
        def copy():
            self.parent.clipboard_clear()
            self.parent.clipboard_append(result_value['text'])
            status.set('Ответ скопирован. Вставьте его в чат EVE: Ctrl+V; отправьте самостоятельно.')
        copy_button = tk.Button(buttons, text='Скопировать ответ', command=copy, state='disabled')
        copy_button.pack(side='right')
        def translate():
            reply = draft.get('1.0', 'end').strip()
            if not reply:
                status.set('Введите ответ по-русски.')
                return
            if len(reply) > 4000 or len(selected) > 4000:
                status.set('Выделенный текст и ответ должны быть не длиннее 4000 символов.')
                return
            self.reply_generation += 1
            generation = self.reply_generation
            translate_button.configure(state='disabled')
            copy_button.configure(state='disabled')
            draft.configure(state='disabled')
            status.set('Определяю язык и перевожу ответ…')
            self.reply_widgets = (generation, draft, output, status, translate_button, copy_button, result_value)
            def run():
                try:
                    translated, language = translate_reply(selected, reply)
                    self.post(('reply_result', generation, translated, language, ''))
                except Exception as error:
                    message = str(error) if isinstance(error, ValueError) else 'Сервис перевода недоступен. Повторите позже.'
                    self.post(('reply_result', generation, '', '', message))
            threading.Thread(target=run, daemon=True).start()
        translate_button = tk.Button(buttons, text='Перевести ответ', command=translate)
        translate_button.pack(side='left')
        draft.focus_set()

    def apply_appearance(self, transparency, clear_background):
        self.background_transparency = max(0, min(65, int(float(transparency))))
        self.clear_background = bool(clear_background)
        # Color-key transparency removes only the fill. Global alpha would
        # also fade all text and controls, making an in-game chat unreadable.
        self.parent.attributes('-alpha', 1.0 if self.clear_background else (100 - self.background_transparency) / 100)
        try: self.parent.attributes('-transparentcolor', BG if self.clear_background else '')
        except tk.TclError: pass
        background = BG if self.clear_background else '#111a22'
        for _, text in self.tabs.values(): text.configure(bg=background)
        self.empty.configure(bg=background)
        for child in self.empty.winfo_children(): child.configure(bg=background)

    def open_settings(self):
        dialog = tk.Toplevel(self.parent)
        dialog.title('Настройки')
        dialog.geometry('560x600')
        dialog.configure(bg=BG)
        dialog.transient(self.parent)
        dialog.attributes('-topmost', False)
        enabled = tk.BooleanVar(value=self.alerts.enabled)
        nearby = tk.BooleanVar(value=self.nearby_enabled)
        old_appearance = (self.background_transparency, self.clear_background)
        transparency = tk.DoubleVar(value=self.background_transparency)
        clear = tk.BooleanVar(value=self.clear_background)
        appearance = tk.LabelFrame(dialog, text='Прозрачность фона', bg=BG, fg=FG,
                                   padx=10, pady=6)
        appearance.pack(fill='x', padx=16, pady=(12, 4))
        def preview(*args):
            self.apply_appearance(transparency.get(), clear.get())
            opacity_scale.configure(state='disabled' if clear.get() else 'normal')
        opacity_scale = tk.Scale(appearance, from_=0, to=65, orient='horizontal', variable=transparency,
            command=preview, label='Прозрачность окна, %', bg=BG, fg=FG,
            troughcolor='#182833', highlightthickness=0)
        opacity_scale.pack(fill='x')
        opacity_scale.configure(state='disabled' if clear.get() else 'normal')
        tk.Checkbutton(appearance, text='Убрать заливку фона чатов', variable=clear, command=preview,
            bg=BG, fg=FG, selectcolor='#182833', activebackground=BG, activeforeground=FG).pack(anchor='w')
        tk.Label(appearance, text='При убранной заливке текст и кнопки всегда полностью яркие.',
                 bg=BG, fg=MUTED, wraplength=500, anchor='w').pack(fill='x')
        def cancel():
            self.apply_appearance(*old_appearance)
            dialog.destroy()
        dialog.protocol('WM_DELETE_WINDOW', cancel)
        dialog.bind('<Escape>', lambda event: cancel())
        tk.Checkbutton(dialog, text='Звуковые оповещения включены', variable=enabled,
            bg=BG, fg=FG, selectcolor='#182833', activebackground=BG, activeforeground=FG,
            font=('Segoe UI', 11)).pack(anchor='w', padx=16, pady=(16, 8))
        tk.Checkbutton(dialog, text='Звук: система в 3 прыжках или ближе (включая 0)', variable=nearby,
            bg=BG, fg=FG, selectcolor='#182833', activebackground=BG, activeforeground=FG).pack(anchor='w', padx=16, pady=(0, 8))
        tk.Label(dialog, text='Слова и фразы фильтра', bg=BG, fg=FG, font=('Segoe UI', 11, 'bold')).pack(anchor='w', padx=16)
        tk.Label(dialog, text='Добавляйте по одному на строку или через запятую.\nПроверяются оригинал и перевод в выбранных чатах, без учёта регистра.',
            bg=BG, fg=MUTED, justify='left', wraplength=500).pack(anchor='w', padx=16, pady=(4, 8))
        words = tk.Text(dialog, height=8, wrap='word', bg='#111a22', fg=FG,
                        insertbackground=FG, font=('Segoe UI', 11), padx=8, pady=8)
        words.insert('1.0', '\n'.join(self.alerts.words))
        words.pack(fill='both', expand=True, padx=16)
        tk.Label(dialog, text='Пустой список — без сигналов по словам. Не более одного сигнала за 2 секунды.',
            bg=BG, fg=MUTED, wraplength=500).pack(anchor='w', padx=16, pady=8)
        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill='x', padx=16, pady=(0, 16))
        tk.Button(buttons, text='Проверить звук', command=play_alert).pack(side='left')
        def save():
            self.alerts.configure(parse_words(words.get('1.0', 'end')), enabled.get())
            self.nearby_enabled = nearby.get()
            self.apply_appearance(transparency.get(), clear.get())
            self.render_history()
            self.status.set(f'Звук {"включён" if self.alerts.enabled else "выключен"}. Слов и фраз в фильтре: {len(self.alerts.words)}.')
            if self.on_preferences_changed: self.on_preferences_changed()
            dialog.destroy()
        tk.Button(buttons, text='Сохранить', command=save).pack(side='right')
        dialog.grab_set()

    def current_origin(self):
        if self.origin_mode == 'manual':
            system = self.catalog.resolve(self.manual_origin)
            return (system[0], system[1], 'Указано вручную') if system else None
        location = next((location for location in self.locations if location.pilot == self.route_pilot), None)
        if location is None and not self.route_pilot and self.locations: location = self.locations[0]
        return ((location.system_id, location.system,
                 f'{location.pilot} · по журналу {location.timestamp} UTC') if location else None)

    def update_origin_label(self):
        origin = self.current_origin()
        self.origin_label.set(f'Откуда: {origin[1]} · {"вручную" if self.origin_mode == "manual" else "по журналу игрока"}'
                              if origin else 'Местоположение не найдено — укажите исходную систему вручную.')
        self.refresh_route_buttons()

    def request_inline(self, key):
        cached = self.inline_counts.get(key)
        if cached and time.monotonic() - cached[0] < (300 if cached[1] is not None else 15): return cached
        if key not in self.route_pending and not self.stopped.is_set():
            try:
                self.route_jobs.put_nowait(key)
                self.route_pending.add(key)
            except queue.Full: pass
        return None

    def route_worker(self):
        while not self.stopped.is_set():
            try: key = self.route_jobs.get(timeout=.2)
            except queue.Empty: continue
            try: count, error = self.route_client.jumps(*key), ''
            except Exception as exception: count, error = None, str(exception)
            self.post(('inline_route', key, count, error))
            self.stopped.wait(.2)

    def configure_route_button(self, button, destination):
        origin = self.current_origin()
        key = (origin[0], destination) if origin else None
        button.route_key = key
        if key is None:
            button.configure(text='Прыжки: ?')
            return
        cached = self.request_inline(key)
        button.configure(text=('Прыжки: …' if cached is None else
                               f'Прыжки: {cached[1]}' if cached[1] is not None else 'Прыжки: —'))

    def refresh_route_buttons(self):
        for buttons in self.route_buttons.values():
            for button in buttons:
                if button.winfo_exists(): self.configure_route_button(button, button.destination)

    @staticmethod
    def message_key(message):
        return (message.channel_id, message.timestamp, message.author, message.text)

    def nearby_alert(self, key, message, count):
        origin = self.current_origin()
        _, selected, active, _ = self.snapshot()
        if (count is not None and 0 <= count <= 3 and self.nearby_enabled and active and
                message.channel_id in selected and message.channel_id not in self.muted_channels and
                origin and key[0] == origin[0] and self.alerts.claim(self.message_key(message))):
            play_alert()

    def register_nearby_message(self, message):
        origin = self.current_origin()
        if not origin or not self.nearby_enabled: return
        for destination in {item[2] for item in self.catalog.mentions(message.text)}:
            key = (origin[0], destination)
            cached = self.request_inline(key)
            if cached is not None:
                self.nearby_alert(key, message, cached[1])
            else:
                self.nearby_waiters[(key, self.message_key(message))] = message
                if len(self.nearby_waiters) > 512: self.nearby_waiters.popitem(last=False)

    def open_origin_settings(self):
        dialog = tk.Toplevel(self.parent)
        dialog.title('Исходная система для расстояний')
        dialog.geometry('560x505')
        dialog.transient(self.parent)
        dialog.configure(bg=BG)
        mode = tk.StringVar(value=self.origin_mode)
        manual = tk.StringVar(value=self.manual_origin)
        pilot = tk.StringVar(value=self.route_pilot or (self.locations[0].pilot if self.locations else ''))
        tk.Radiobutton(dialog, text='Определять по локальному журналу игрока', variable=mode, value='auto',
            bg=BG, fg=FG, selectcolor='#182833', activebackground=BG).pack(anchor='w', padx=16, pady=(16, 5))
        ttk.Combobox(dialog, values=[location.pilot for location in self.locations], textvariable=pilot,
                     state='readonly').pack(fill='x', padx=30)
        tk.Label(dialog, text='Журнал хранит последнюю подтверждённую систему.\nПри нескольких персонажах выберите нужного игрока.',
                 bg=BG, fg=MUTED, justify='left').pack(anchor='w', padx=30, pady=8)
        tk.Radiobutton(dialog, text='Указать систему вручную', variable=mode, value='manual',
            bg=BG, fg=FG, selectcolor='#182833', activebackground=BG).pack(anchor='w', padx=16)
        entry = SystemEntry(dialog, self.catalog, manual, on_edit=lambda: mode.set('manual'))
        entry.pack(fill='both', expand=True, padx=30, pady=6)
        error = tk.StringVar()
        tk.Label(dialog, textvariable=error, bg=BG, fg='#edc780', wraplength=520).pack(fill='x', padx=16)
        def save():
            resolved = self.catalog.resolve(manual.get())
            if mode.get() == 'manual' and not resolved:
                error.set('Система не найдена. Введите точное название, например Jita или 4-HWWF.')
                return
            self.origin_mode = mode.get()
            if resolved: self.manual_origin = resolved[1]
            self.route_pilot = pilot.get()
            self.route_generation += 1
            self.update_origin_label()
            if self.on_preferences_changed: self.on_preferences_changed()
            dialog.destroy()
        tk.Button(dialog, text='Сохранить', command=save).pack(anchor='e', padx=16, pady=10)
        dialog.grab_set()

    def open_route(self, destination, name):
        if self.route_dialog is not None and self.route_dialog.winfo_exists(): self.route_dialog.destroy()
        dialog = self.route_dialog = tk.Toplevel(self.parent)
        dialog.title('Расстояние до ' + name)
        dialog.geometry('560x285')
        dialog.configure(bg=BG)
        dialog.transient(self.parent)
        origin = self.current_origin()
        tk.Label(dialog, text=name, bg=BG, fg=FG, font=('Segoe UI', 16, 'bold')).pack(anchor='w', padx=18, pady=(16, 8))
        result = tk.StringVar(value='Рассчитываю расстояние…' if origin else 'Исходная система неизвестна. Укажите её вручную.')
        tk.Label(dialog, text=(f'Откуда: {origin[1]}\n{origin[2]}' if origin else 'Нет подтверждённой позиции игрока.'),
                 bg=BG, fg=MUTED, justify='left', wraplength=520).pack(anchor='w', padx=18)
        tk.Label(dialog, textvariable=result, bg=BG, fg=ACCENT, font=('Segoe UI', 14),
                 wraplength=520, justify='left').pack(anchor='w', padx=18, pady=12)
        tk.Label(dialog, text='Кратчайший путь по звёздным вратам.\nБез Ansiblex, червоточин и личного списка избегаемых систем.',
                 bg=BG, fg=MUTED, justify='left').pack(anchor='w', padx=18)
        tk.Button(dialog, text='Изменить исходную систему…', command=lambda: (dialog.destroy(), self.open_origin_settings())).pack(anchor='e', padx=18, pady=10)
        self.route_generation += 1
        generation = self.route_generation
        self.route_result = result
        if origin:
            def calculate():
                try: value = f'{self.route_client.jumps(origin[0], destination)} прыжк. · {origin[1]} → {name}'
                except Exception as error: value = str(error) if isinstance(error, ValueError) else 'Не удалось получить маршрут ESI. Повторите позже.'
                self.post(('route_result', generation, value))
            threading.Thread(target=calculate, daemon=True).start()

    def insert_system_buttons(self, widget, value, tag, seen, buttons):
        offset = 0
        for start, end, system_id, name in self.catalog.mentions(value):
            widget.insert('end', value[offset:end], tag)
            if system_id not in seen and len(seen) < 8:
                seen.add(system_id)
                button = tk.Button(widget, text='Прыжки', font=('Segoe UI', 8), bg='#182833', fg=ACCENT,
                    relief='flat', padx=4, pady=0, command=lambda target=system_id, label=name: self.open_route(target, label))
                widget.insert('end', ' ')
                widget.window_create('end', window=button)
                button.destination = system_id
                self.configure_route_button(button, system_id)
                buttons.append(button)
            offset = end
        widget.insert('end', value[offset:], tag)

    def alert_for(self, message, translated=''):
        if message.channel_id in self.muted_channels: return
        key = (message.channel_id, message.timestamp, message.author, message.text)
        if self.alerts.should_play(key, message.text) or self.alerts.should_play(key, translated):
            play_alert()

    def post(self, event):
        if self.stopped.is_set(): return
        try: self.events.put_nowait(event)
        except queue.Full: pass

    def toggle(self):
        with self.state_lock:
            self.active = self.enabled_var.get()
            self.revision += 1
        self.status.set('Перевод новых сообщений включён.' if self.active else 'Перевод чата приостановлен. Новые сообщения пропускаются.')

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.parent, title='Папка EVE/logs/Chatlogs', initialdir=str(self.folder))
        if folder:
            with self.state_lock:
                self.folder = Path(folder)
                self.revision += 1
            self.status.set('Папка изменена. Отслеживаю только новые сообщения…')

    def choose_channels(self):
        dialog = tk.Toplevel(self.parent)
        dialog.title('Чат-каналы EVE')
        dialog.geometry('520x430')
        dialog.configure(bg=BG)
        dialog.transient(self.parent)
        dialog.attributes('-topmost', False)
        tk.Label(dialog, text='Выберите каналы для перевода новых сообщений', bg=BG, fg=FG,
                 font=('Segoe UI', 11)).pack(padx=16, pady=12, anchor='w')
        choices = list(self.available)
        list_frame = tk.Frame(dialog, bg=BG)
        listing = tk.Listbox(list_frame, selectmode='multiple', exportselection=False,
            bg='#111a22', fg=FG, selectbackground='#284754', relief='flat', font=('Segoe UI', 11))
        for index, (channel_id, name) in enumerate(choices):
            listing.insert('end', name)
            if channel_id in self.selected: listing.selection_set(index)
        tk.Label(dialog, text='Нажмите на нужные чаты. Остальные переводиться не будут.',
                 bg=BG, fg=MUTED, wraplength=480).pack(anchor='w', padx=16)
        list_frame.pack(fill='both', expand=True, padx=16, pady=8)
        scrollbar = tk.Scrollbar(list_frame, command=listing.yview)
        scrollbar.pack(side='right', fill='y')
        listing.configure(yscrollcommand=scrollbar.set)
        listing.pack(side='left', fill='both', expand=True)
        def clear():
            listing.selection_clear(0, 'end')
        def apply():
            selected = frozenset(choices[int(i)][0] for i in listing.curselection())
            with self.state_lock:
                self.selected = selected
                self.revision += 1
            self.history.clear()
            self.sync_tabs()
            self.update_selection_label()
            self.status.set(f'Выбрано чатов: {len(selected)}. Ожидаю новые сообщения…' if selected else 'Выберите хотя бы один чат для перевода.')
            dialog.destroy()
        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill='x', padx=16, pady=(4, 12))
        tk.Button(buttons, text='Снять выделение', command=clear).pack(side='left')
        tk.Button(buttons, text='Применить', command=apply).pack(side='right')
        dialog.grab_set()

    def update_selection_label(self):
        names = dict(self.available)
        selected_names = [names.get(channel_id, channel_id) for channel_id in sorted(self.selected)]
        if not selected_names:
            self.selection_label.set('Чат не выбран — нажмите «Выбрать чаты…»')
        else:
            label = ', '.join(selected_names[:3])
            if len(selected_names) > 3: label += f' … (всего {len(selected_names)})'
            self.selection_label.set('Переводить: ' + label)

    def watch(self):
        watcher = None
        current_folder = None
        old_channels = None
        missing = None
        tracker = LocationTracker(self.catalog)
        old_locations = None
        while not self.stopped.is_set():
            folder, selected, active, revision = self.snapshot()
            if folder != current_folder:
                watcher = ChatWatcher(folder)
                current_folder = folder
                old_channels = None
                missing = None
                tracker = LocationTracker(self.catalog)
                old_locations = None
            exists = folder.is_dir()
            if not exists:
                if missing is not True:
                    self.post(('status', 'Папка чат-логов не найдена. Нажмите «Папка…» и выберите EVE/logs/Chatlogs.'))
                missing = True
                if self.stopped.wait(1): break
                continue
            try:
                records = watcher.poll()
                locations = tracker.update(watcher.metadata)
                if locations != old_locations:
                    self.post(('locations', locations))
                    old_locations = locations
                channels = sorted(watcher.channels.items(), key=lambda item: (item[1].casefold(), item[0]))
                if channels != old_channels:
                    self.post(('channels', channels))
                    old_channels = channels
                    self.post(('status', f'Найдено каналов: {len(channels)}. Ожидаю новые сообщения…' if channels else
                               'Логи ещё не найдены. Включите запись чата в файлы в EVE и откройте канал.'))
                missing = False
                for message in records:
                    # Re-read selection after disk access: pause is immediate for queued jobs.
                    _, selected, active, revision = self.snapshot()
                    if active and message.channel_id in selected:
                        self.post(('alert', revision, message))
                        enabled, epoch = self.channel_state(message.channel_id)
                        if not enabled:
                            self.post(('caption', revision, message, message.text, epoch))
                            continue
                        try: self.jobs.put_nowait((revision, message, epoch))
                        except queue.Full:
                            self.post(('status', 'Много сообщений: очередь чата заполнена, часть сообщений пропущена.'))
            except OSError:
                self.post(('status', 'Не удалось прочитать чат-логи. Проверьте доступ к папке.'))
            if self.stopped.wait(.5): break

    def translate(self):
        translator = TextTranslator()
        backoff_until = 0
        while not self.stopped.is_set():
            try: revision, message, epoch = self.jobs.get(timeout=.2)
            except queue.Empty: continue
            _, selected, active, current_revision = self.snapshot()
            if not active or revision != current_revision: continue
            enabled, current_epoch = self.channel_state(message.channel_id)
            if not enabled or epoch != current_epoch: continue
            if time.monotonic() < backoff_until:
                translated = '[оригинал] ' + message.text
            else:
                try:
                    translated = translator.translate(message.text)
                except Exception:
                    translated = '[оригинал] ' + message.text
                    backoff_until = time.monotonic() + 15
                    self.post(('status', 'Google Translate недоступен. Временно показываю оригиналы сообщений.'))
            _, _, active, current_revision = self.snapshot()
            enabled, current_epoch = self.channel_state(message.channel_id)
            if active and enabled and epoch == current_epoch and revision == current_revision and not self.stopped.is_set():
                self.post(('caption', revision, message, translated, epoch))
            self.stopped.wait(.25)

    def drain(self):
        if self.stopped.is_set(): return
        for _ in range(256):
            try: event = self.events.get_nowait()
            except queue.Empty: break
            if event[0] == 'status': self.status.set(event[1])
            elif event[0] == 'message_translation':
                if event[1] == self.message_generation and self.message_dialog is not None and self.message_dialog.winfo_exists():
                    output, status, button, result = self.message_widgets
                    if event[3]: status.set(event[3])
                    else:
                        result['text'] = event[2]
                        output.configure(state='normal'); output.insert('1.0', event[2]); output.configure(state='disabled')
                        status.set('Сообщение переведено на русский.')
                        button.configure(state='normal')
            elif event[0] == 'reply_incoming':
                if event[1] == self.reply_dialog_generation and self.reply_dialog is not None and self.reply_dialog.winfo_exists():
                    self.reply_incoming.set('Перевод сообщения: ' + event[2][:350] + ('…' if len(event[2]) > 350 else ''))
            elif event[0] == 'reply_result':
                if event[1] != self.reply_generation or self.reply_dialog is None or not self.reply_dialog.winfo_exists(): continue
                _, draft, output, status, button, copy_button, result_value = self.reply_widgets
                draft.configure(state='normal')
                button.configure(state='normal')
                if event[4]:
                    status.set(event[4])
                else:
                    result_value['text'] = event[2]
                    output.configure(state='normal')
                    output.delete('1.0', 'end')
                    output.insert('end', event[2])
                    output.configure(state='disabled')
                    copy_button.configure(state='normal')
                    status.set(f'Язык выделенного текста: {event[3]}. Ответ готов — проверьте и скопируйте.')
            elif event[0] == 'locations':
                self.locations = event[1]
                self.update_origin_label()
            elif event[0] == 'inline_route':
                key, count, error = event[1:]
                self.route_pending.discard(key)
                self.inline_counts[key] = (time.monotonic(), count, error)
                if len(self.inline_counts) > 400: self.inline_counts.popitem(last=False)
                for waiter, message in list(self.nearby_waiters.items()):
                    if waiter[0] == key:
                        self.nearby_alert(key, message, count)
                        self.nearby_waiters.pop(waiter, None)
                self.refresh_route_buttons()
            elif event[0] == 'route_result':
                if event[1] == self.route_generation and self.route_dialog is not None and self.route_dialog.winfo_exists():
                    self.route_result.set(event[2])
            elif event[0] == 'channels':
                self.available = event[1]
                self.sync_tabs()
                self.update_selection_label()
            elif event[0] == 'alert':
                _, selected, active, revision = self.snapshot()
                if active and revision == event[1] and event[2].channel_id in selected:
                    self.alert_for(event[2])
                    self.register_nearby_message(event[2])
            elif event[0] == 'caption':
                _, _, active, revision = self.snapshot()
                if not active or revision != event[1]: continue
                message, translated = event[2:4]
                if len(event) > 4 and event[4] != self.channel_state(message.channel_id)[1]: continue
                self.alert_for(message, translated)
                self.register_nearby_message(message)
                self.history.append((message, translated))
                self.channel_history.setdefault(message.channel_id, deque(maxlen=100)).append((message, translated))
                if self.active_channel() != message.channel_id:
                    self.unread[message.channel_id] = self.unread.get(message.channel_id, 0) + 1
                    self.update_tab_title(message.channel_id)
                self.render_history(message.channel_id)
                if not translated.startswith('[оригинал]') and self.channel_state(message.channel_id)[0]:
                    self.status.set(f'Переведено: {message.channel} · {message.timestamp[-8:]} UTC')
        self.after_id = self.parent.after(100, self.drain)

    def stop(self):
        try: self.parent.after_cancel(self.after_id)
        except tk.TclError: pass
        self.watcher_thread.join(timeout=.3)
        self.translation_thread.join(timeout=.3)
        self.route_thread.join(timeout=.3)

    def render_history(self, channel_id=None):
        if channel_id is None:
            for key in self.tabs: self.render_history(key)
            return
        if channel_id not in self.tabs: return
        text = self.tabs[channel_id][1]
        at_bottom = text.yview()[1] >= .98
        old_scroll = text.yview()[0]
        text.configure(state='normal')
        for button in self.route_buttons.get(channel_id, []): button.destroy()
        self.route_buttons[channel_id] = buttons = []
        text.delete('1.0', 'end')
        for message, translated in self.channel_history.get(channel_id, ()):
            seen = set()
            matched = self.alerts.matches(message.text) or self.alerts.matches(translated)
            text.insert('end', f'{"[!] " if matched else ""}{message.timestamp[-8:]} UTC · {message.channel} · {message.author}\n', 'match' if matched else 'meta')
            self.insert_system_buttons(text, translated + '\n', 'translated', seen, buttons)
            if translated != message.text and not translated.startswith('[оригинал]'):
                self.insert_system_buttons(text, message.text + '\n', 'original', seen, buttons)
            text.insert('end', '\n')
        text.configure(state='disabled')
        if at_bottom: text.see('end')
        else: text.yview_moveto(old_scroll)
