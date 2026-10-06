# -*- coding: utf-8 -*-
"""Entrypoint do RHYFT para builds Windows com branding oficial."""
from pathlib import Path
import sys

import customtkinter as ctk
from PIL import Image

import app as base

# O núcleo ainda preserva alguns identificadores legados para compatibilidade de
# dados locais. O build oficial, porém, sempre consulta Releases no repositório RHYFT.
base.GITHUB_REPO = "deadbynetsu/RHYFT"

APP_USER_MODEL_ID = "dev.deadbynetsu.RHYFT"


def recurso(*partes):
    """Resolve arquivos tanto em desenvolvimento quanto dentro do PyInstaller onefile."""
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_dir.joinpath(*partes)


def configurar_app_id_windows():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def imagem_logo_oficial(tam=44):
    """Marca RHYFT usada dentro da interface desktop."""
    caminho = recurso("assets", "generated", "rhyft_mark.png")
    try:
        img = Image.open(caminho).convert("RGBA")
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(tam, tam))
    except Exception:
        return base.imagem_logo(tam)


def aplicar_icone_janela(janela):
    """Aplica o .ico oficial à janela e repete depois do init do CustomTkinter."""
    if sys.platform != "win32":
        return
    caminho = recurso("assets", "generated", "rhyft_icon.ico")

    def aplicar():
        try:
            if janela.winfo_exists():
                janela.iconbitmap(str(caminho))
        except Exception:
            pass

    aplicar()
    try:
        janela.after(250, aplicar)
        janela.after(1000, aplicar)
    except Exception:
        pass


# MigradorApp procura imagem_logo no namespace do módulo app em tempo de execução.
base.imagem_logo = imagem_logo_oficial


class RHYFTApp(base.MigradorApp):
    def __init__(self):
        super().__init__()
        aplicar_icone_janela(self)

    def _nova_janela(self, titulo, geometria):
        janela = super()._nova_janela(titulo, geometria)
        aplicar_icone_janela(janela)
        return janela


if __name__ == "__main__":
    configurar_app_id_windows()
    app = RHYFTApp()
    app.mainloop()
