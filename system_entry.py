"""Local solar-system autocomplete: typing, arrows, Enter and double click."""
import tkinter as tk

class SystemEntry(tk.Frame):
    def __init__(self, parent, catalog, variable, on_edit=None):
        super().__init__(parent, bg='#0b1015')
        self.catalog, self.variable, self.on_edit = catalog, variable, on_edit
        self.entry = tk.Entry(self, textvariable=variable, bg='#111a22', fg='#edf3f6',
                              insertbackground='#edf3f6', font=('Segoe UI', 12))
        self.entry.pack(fill='x')
        row = tk.Frame(self, bg='#0b1015')
        row.pack(fill='both', expand=True, pady=(4, 0))
        self.listing = tk.Listbox(row, height=6, exportselection=False, bg='#111a22', fg='#edf3f6',
            selectbackground='#284754', relief='flat', font=('Segoe UI', 11))
        scroll = tk.Scrollbar(row, command=self.listing.yview)
        scroll.pack(side='right', fill='y')
        self.listing.configure(yscrollcommand=scroll.set)
        self.listing.pack(side='left', fill='both', expand=True)
        self.hint = tk.StringVar()
        tk.Label(self, textvariable=self.hint, bg='#0b1015', fg='#b2c1cb', anchor='w',
                 font=('Segoe UI', 9)).pack(fill='x', pady=(4, 0))
        self.entry.bind('<Down>', lambda event: self.move(1))
        self.entry.bind('<Up>', lambda event: self.move(-1))
        self.entry.bind('<Return>', self.choose)
        self.listing.bind('<Return>', self.choose)
        self.listing.bind('<Double-Button-1>', self.choose)
        self.trace = variable.trace_add('write', self.changed)
        self.bind('<Destroy>', self.destroyed)
        self.refresh()

    def changed(self, *args):
        if self.on_edit: self.on_edit()
        self.refresh()

    def refresh(self):
        self.matches = self.catalog.suggest(self.variable.get())
        self.listing.delete(0, 'end')
        for name in self.matches: self.listing.insert('end', name)
        if self.matches: self.listing.selection_set(0)
        self.hint.set('↑/↓ — выбрать, Enter или двойной щелчок — подставить' if self.matches else
                      ('Нет совпадений' if self.variable.get().strip() else 'Начните вводить название системы…'))

    def move(self, direction):
        if self.matches:
            selected = self.listing.curselection()
            index = max(0, min(len(self.matches) - 1, (int(selected[0]) if selected else -1) + direction))
            self.listing.selection_clear(0, 'end')
            self.listing.selection_set(index)
            self.listing.activate(index)
            self.listing.see(index)
        return 'break'

    def choose(self, event=None):
        selected = self.listing.curselection()
        if selected and self.matches:
            name = self.matches[int(selected[0])]
            self.variable.set(name)
            self.entry.focus_set()
            self.entry.icursor('end')
        return 'break'

    def destroyed(self, event):
        if event.widget is self:
            try: self.variable.trace_remove('write', self.trace)
            except tk.TclError: pass
