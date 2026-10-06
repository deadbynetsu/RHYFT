"""Accessible first-run language chooser; the main UI is built only after selection."""
import customtkinter as ctk
from pathlib import Path
import sys
from rhyft_i18n import (LANGUAGES, LanguagePreference, detect_language,
                        language_order, set_language, tr,
                        FIRST_HOLD_MS, ROTATION_HOLD_MS, FADE_MS)


class LanguageDialog(ctk.CTkToplevel):
    def __init__(self, parent, preference, initial, on_done, first_run=False):
        super().__init__(parent)
        self.preference, self.on_done = preference, on_done
        self.selected = initial
        self.order = language_order(detect_language())
        self.rotation = language_order(initial)
        self.index = 0
        self.timer = None
        self.title('RHYFT · ' + tr('Idioma', language=initial))
        self.geometry('520x660')
        self.minsize(440, 540)
        self.configure(fg_color='#080a10')
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.first_run = first_run
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(self, text='RHYFT', font=('Segoe UI', 30, 'bold')).grid(row=0, pady=(24, 0))
        ctk.CTkLabel(self, text='Your music. No borders.', text_color='#a8afc0').grid(row=1, pady=(0, 16))
        self.heading = ctk.CTkLabel(self, text=tr('Escolha seu idioma', language=initial),
                                   font=('Segoe UI', 24, 'bold'), wraplength=410)
        self.heading.grid(row=2, padx=20, pady=8)
        choices = ctk.CTkScrollableFrame(self, fg_color='#11151e')
        choices.grid(row=3, sticky='nsew', padx=24, pady=12)
        choices.grid_columnconfigure(0, weight=1)
        self.value = ctk.StringVar(value=initial)
        for row, code in enumerate(self.order):
            ctk.CTkRadioButton(choices, text=LANGUAGES[code], value=code,
                              variable=self.value, command=self.select,
                              font=('Segoe UI', 16), fg_color='#27e4d3').grid(
                                  row=row, sticky='w', padx=14, pady=10)
        self.hint = ctk.CTkLabel(self, text=tr('Você pode trocar o idioma nas configurações.', language=initial),
                                wraplength=420, text_color='#a8afc0')
        self.hint.grid(row=4, padx=24, pady=6)
        self.motion = ctk.BooleanVar(value=True)
        self.motion_toggle = ctk.CTkCheckBox(self, text=tr('Animar saudação', language=initial),
                                            variable=self.motion, command=self.toggle_motion)
        self.motion_toggle.grid(row=5, padx=24, pady=8)
        self.error = ctk.CTkLabel(self, text='', text_color='#ff718c', wraplength=420)
        self.error.grid(row=6, padx=24)
        self.confirm = ctk.CTkButton(self, text=tr('Continuar' if first_run else 'Salvar', language=initial),
                                     command=self.save, height=42, fg_color='#27e4d3', text_color='#080a10')
        self.confirm.grid(row=7, sticky='ew', padx=24, pady=(0, 24))
        self.bind('<Escape>', lambda event: self.cancel())
        self.bind('<Return>', lambda event: self.save())
        self.timer = self.after(FIRST_HOLD_MS, self.rotate)
        self.grab_timer = self.after(150, self.grab_set)
        icon = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets/generated/rhyft_icon.ico'
        if sys.platform == 'win32' and icon.is_file():
            self.iconbitmap(str(icon))

    def destroy(self):
        self.stop_animation()
        if getattr(self, 'grab_timer', None):
            self.after_cancel(self.grab_timer)
            self.grab_timer = None
        super().destroy()

    def stop_animation(self):
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None

    def fade(self, step, direction, after):
        level = step / 10
        channels = [round(a + (b-a)*level) for a, b in zip((8, 10, 16), (247, 249, 255))]
        self.heading.configure(text_color='#' + ''.join(f'{c:02x}' for c in channels))
        following = step + direction
        if 0 <= following <= 10:
            self.timer = self.after(FADE_MS // 10, lambda: self.fade(following, direction, after))
        else:
            after()

    def rotate(self):
        def change():
            self.index = (self.index + 1) % len(self.rotation)
            self.heading.configure(text=tr('Escolha seu idioma', language=self.rotation[self.index]))
            self.fade(0, 1, lambda: setattr(self, 'timer', self.after(ROTATION_HOLD_MS, self.rotate)))
        self.fade(10, -1, change)

    def select(self):
        self.selected = self.value.get()
        self.stop_animation()
        self.heading.configure(text=tr('Escolha seu idioma', language=self.selected), text_color='#f7f9ff')
        self.hint.configure(text=tr('Você pode trocar o idioma nas configurações.', language=self.selected))
        self.confirm.configure(text=tr('Continuar' if self.first_run else 'Salvar', language=self.selected))
        self.motion_toggle.configure(text=tr('Animar saudação', language=self.selected))
        self.motion.set(False)

    def toggle_motion(self):
        self.stop_animation()
        self.heading.configure(text=tr('Escolha seu idioma', language=self.selected), text_color='#f7f9ff')
        if self.motion.get():
            self.rotation = language_order(self.selected)
            self.index = 0
            self.timer = self.after(FIRST_HOLD_MS, self.rotate)

    def save(self):
        try:
            self.preference.save(self.selected)
        except OSError:
            self.error.configure(text=tr('Não foi possível salvar. Tente novamente.', language=self.selected))
            return
        self.stop_animation()
        self.grab_release()
        self.destroy()
        self.on_done(self.selected)

    def cancel(self):
        self.stop_animation()
        self.grab_release()
        self.destroy()
        if self.first_run:
            self.on_done(None)


def ensure_language(root, directory):
    preference = LanguagePreference(directory)
    saved = preference.read()
    if saved:
        set_language(saved)
        return
    root.withdraw()
    result = []
    dialog = LanguageDialog(root, preference, detect_language(), result.append, first_run=True)
    root.wait_window(dialog)
    if not result or result[0] is None:
        root.destroy()
        raise SystemExit(0)
    set_language(result[0])
    root.deiconify()


def open_language_settings(root, directory):
    from tkinter import messagebox
    preference = LanguagePreference(directory)
    LanguageDialog(root, preference, preference.read() or detect_language(),
                   lambda code: messagebox.showinfo(tr('Idioma', language=code),
                       tr('Reinicie o app para aplicar o idioma.', language=code), parent=root))
