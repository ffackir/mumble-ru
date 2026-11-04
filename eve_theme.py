"""Shared EVE-inspired palette, compact controls and dark window chrome."""
import ctypes
import os
import tkinter as tk
from tkinter import ttk

BG = '#0b1015'
PANEL = '#111a22'
BUTTON = '#182833'
HOVER = '#263e4c'
BORDER = '#334957'
FG = '#edf3f6'
MUTED = '#b2c1cb'
ACCENT = '#82c8e8'
GREEN = '#80d5a8'
GOLD = '#edc780'

def dark_chrome(window):
    if os.name != 'nt' or not window.winfo_exists(): return
    try:
        user = ctypes.windll.user32
        user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user.GetAncestor.restype = ctypes.c_void_p
        handle = user.GetAncestor(window.winfo_id(), 2)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        enabled = ctypes.c_int(1)
        result = dwm.DwmSetWindowAttribute(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
        if result:
            dwm.DwmSetWindowAttribute(handle, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (OSError, AttributeError): pass

def install_theme(root):
    options = {
        '*Font': ('Segoe UI', 10), '*Background': BG, '*Foreground': FG,
        '*Button.Background': BUTTON, '*Button.Foreground': FG,
        '*Button.ActiveBackground': HOVER, '*Button.ActiveForeground': FG,
        '*Button.HighlightThickness': 1, '*Button.HighlightBackground': BORDER,
        '*Button.HighlightColor': ACCENT, '*Button.BorderWidth': 0,
        '*Button.Relief': 'flat', '*Button.PadX': 9, '*Button.PadY': 4,
        '*Button.DisabledForeground': '#617582', '*Button.Cursor': 'hand2',
        '*Checkbutton.SelectColor': BUTTON, '*Checkbutton.HighlightThickness': 0,
        '*Radiobutton.SelectColor': BUTTON, '*Radiobutton.HighlightThickness': 0,
        '*Entry.Background': PANEL, '*Entry.Foreground': FG, '*Entry.InsertBackground': FG,
        '*Entry.Relief': 'flat', '*Entry.HighlightThickness': 1,
        '*Entry.HighlightBackground': BORDER, '*Entry.HighlightColor': ACCENT,
        '*Text.HighlightThickness': 1, '*Text.HighlightBackground': BORDER,
        '*Text.HighlightColor': ACCENT, '*Text.SelectBackground': HOVER,
        '*Text.SelectForeground': FG, '*Text.BorderWidth': 0,
        '*Listbox.HighlightThickness': 1, '*Listbox.HighlightBackground': BORDER,
        '*Listbox.HighlightColor': ACCENT, '*Listbox.SelectForeground': FG,
        '*Scrollbar.Width': 11, '*Scrollbar.Background': BORDER,
        '*Scrollbar.ActiveBackground': ACCENT, '*Scrollbar.TroughColor': BG,
        '*Scrollbar.BorderWidth': 0, '*Scrollbar.Relief': 'flat',
        '*Scale.BorderWidth': 0, '*Scale.SliderRelief': 'flat',
        '*Menu.Background': PANEL, '*Menu.Foreground': FG,
        '*Menu.ActiveBackground': HOVER, '*Menu.ActiveForeground': FG,
        '*Menu.SelectColor': ACCENT, '*Menu.Relief': 'solid', '*Menu.BorderWidth': 1,
        '*LabelFrame.BorderWidth': 1, '*LabelFrame.Relief': 'solid',
    }
    for pattern, value in options.items(): root.option_add(pattern, value)
    style = ttk.Style(root)
    if 'clam' in style.theme_names(): style.theme_use('clam')
    style.configure('.', background=BG, foreground=FG, font=('Segoe UI', 10))
    style.configure('TCombobox', fieldbackground=PANEL, background=BUTTON,
                    foreground=FG, arrowcolor=ACCENT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, padding=5)
    style.map('TCombobox', fieldbackground=[('readonly', PANEL)],
              foreground=[('readonly', FG)], selectbackground=[('readonly', HOVER)],
              selectforeground=[('readonly', FG)])
    root.option_add('*TCombobox*Listbox.background', PANEL)
    root.option_add('*TCombobox*Listbox.foreground', FG)
    root.option_add('*TCombobox*Listbox.selectBackground', HOVER)
    def mapped(event):
        if isinstance(event.widget, (tk.Tk, tk.Toplevel)):
            event.widget.after_idle(lambda window=event.widget: dark_chrome(window))
    root.bind_all('<Map>', mapped, add='+')
    root.after_idle(lambda: dark_chrome(root))

def divider(parent):
    line = tk.Frame(parent, bg=BORDER, height=1)
    line.pack(fill='x', padx=18, pady=7)
    return line
