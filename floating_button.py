"""Independent transparent, draggable, topmost translation toggle."""
import ctypes
import os
import tkinter as tk

class FloatingTranslationButton:
    WIDTH, HEIGHT = 230, 68
    KEY = '#ff00ff'

    def __init__(self, app, config):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title('Перевод — плавающая кнопка')
        self.window.overrideredirect(True)
        self.window.configure(bg=self.KEY)
        self.topmost_var = tk.BooleanVar(value=bool(config.get('floating_topmost', False)))
        self.window.attributes('-topmost', self.topmost_var.get())
        if os.name == 'nt': self.window.attributes('-transparentcolor', self.KEY)
        position = config.get('floating_position')
        if not isinstance(position, list) or len(position) != 2 or not all(isinstance(v, int) for v in position):
            position = [app.root.winfo_screenwidth() - self.WIDTH - 30, 45]
        x, y = self.clamp(*position)
        self.window.geometry(f'{self.WIDTH}x{self.HEIGHT}+{x}+{y}')
        self.canvas = tk.Canvas(self.window, width=self.WIDTH, height=self.HEIGHT,
                                bg=self.KEY, highlightthickness=0, borderwidth=0, cursor='hand2')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.create_polygon(2, 11, 11, 2, self.WIDTH-12, 2, self.WIDTH-3, 11,
            self.WIDTH-3, self.HEIGHT-12, self.WIDTH-12, self.HEIGHT-3, 11, self.HEIGHT-3, 2, self.HEIGHT-12,
            outline='#82c8e8', fill=self.KEY, width=1)
        self.canvas.create_line(11, 6, 42, 6, fill='#82c8e8', width=2)
        self.canvas.create_text(16, 28, text='⋮', fill='#d3e2ec', font=('Segoe UI', 19))
        self.title = self.canvas.create_text(126, 23, text='Перевод', fill='#82c8e8', font=('Bahnschrift', 12, 'bold'))
        self.channel = self.canvas.create_text(126, 47, text='Выберите чат', fill='#edf3f6', font=('Segoe UI', 9))
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.menu = tk.Menu(self.window, tearoff=False)
        self.menu.add_checkbutton(label='Поверх окон', variable=self.topmost_var, command=self.toggle_topmost)
        self.menu.add_command(label='Показать чат', command=self.show_main)
        self.menu.add_command(label='Голосовой перевод', command=self.show_voice)
        self.menu.add_command(label='Выбрать чаты…', command=self.choose_chats)
        self.menu.add_separator()
        self.menu.add_command(label='Закрыть программу', command=app.close)
        self.canvas.bind('<Button-3>', lambda event: self.menu.tk_popup(event.x_root, event.y_root))
        self.origin = None
        self.moved = False
        self.last_state = None
        self.after_id = self.window.after(100, self.refresh)
        self.window.after_idle(self.no_activate)

    def clamp(self, x, y):
        return (max(0, min(x, self.app.root.winfo_screenwidth() - self.WIDTH)),
                max(0, min(y, self.app.root.winfo_screenheight() - self.HEIGHT)))

    def no_activate(self):
        if os.name != 'nt': return
        user = ctypes.windll.user32
        user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user.GetAncestor.restype = ctypes.c_void_p
        user.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        hwnd = user.GetAncestor(self.window.winfo_id(), 2)
        style = user.GetWindowLongPtrW(hwnd, -20)
        user.SetWindowLongPtrW(hwnd, -20, style | 0x80 | 0x08000000)

    def show_main(self):
        if hasattr(self.app, 'show_chat'):
            self.app.show_chat()
            return
        self.show_voice()

    def show_voice(self):
        self.app.root.deiconify()
        self.app.root.lift()

    def toggle_topmost(self):
        self.window.attributes('-topmost', self.topmost_var.get())
        self.app.save_preferences()

    def choose_chats(self):
        self.show_main()
        self.app.chat_panel.choose_channels()

    def click(self):
        panel = self.app.chat_panel
        channel_id = panel.active_channel()
        if channel_id is None:
            self.choose_chats()
            return
        _, _, active, _ = panel.snapshot()
        enabled, _ = panel.channel_state(channel_id)
        if not active:
            panel.enabled_var.set(True)
            panel.toggle()
            panel.chat_toggles[channel_id].set(True)
        else:
            panel.chat_toggles[channel_id].set(not enabled)
        panel.toggle_chat(channel_id)
        self.refresh(schedule=False)

    def press(self, event):
        self.origin = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())
        self.moved = False

    def drag(self, event):
        if self.origin is None: return
        start_x, start_y, old_x, old_y = self.origin
        dx, dy = event.x_root - start_x, event.y_root - start_y
        if abs(dx) + abs(dy) > 5: self.moved = True
        if self.moved:
            x, y = self.clamp(old_x + dx, old_y + dy)
            self.window.geometry(f'+{x}+{y}')

    def release(self, event):
        if self.origin is None: return
        if self.moved:
            self.app.save_preferences()
        elif event.x >= 28:
            self.click()
        self.origin = None

    def refresh(self, schedule=True):
        if self.app.stopped.is_set(): return
        panel = self.app.chat_panel
        channel_id = panel.active_channel()
        _, _, active, _ = panel.snapshot()
        enabled = bool(channel_id and active and panel.channel_state(channel_id)[0])
        name = dict(panel.available).get(channel_id, channel_id) if channel_id else 'Выберите чат'
        state = (name, enabled)
        if state != self.last_state:
            self.canvas.itemconfigure(self.title, text='Перевод · ВКЛ' if enabled else 'Перевод · ВЫКЛ',
                                       fill='#80d5a8' if enabled else '#edc780')
            self.canvas.itemconfigure(self.channel, text=name if len(name) <= 28 else name[:25] + '…')
            self.last_state = state
        if schedule: self.after_id = self.window.after(250, self.refresh)

    def preferences(self):
        return {'floating_position': [self.window.winfo_x(), self.window.winfo_y()],
                'floating_topmost': self.topmost_var.get()}

    def stop(self):
        try: self.window.after_cancel(self.after_id)
        except tk.TclError: pass
