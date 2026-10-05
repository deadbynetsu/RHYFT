# -*- coding: utf-8 -*-
"""
RHYFT: Spotify -> YouTube Music

Cada pessoa usa as PRÓPRIAS contas:
  - Spotify: cria o próprio app no painel de desenvolvedores (grátis) e cola só o
    Client ID. A autorização usa PKCE, então NÃO existe Client Secret no programa.
  - YouTube Music: cola os cabeçalhos da requisição copiados do navegador. O app
    limpa, valida (faz uma chamada de teste) e só então salva.

Nada de credenciais é embutido no código. Tudo fica em %APPDATA%\RHYFT.
"""
import base64
import glob
import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import sys
import threading
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, HTTPServer

import customtkinter as ctk
import requests
from PIL import Image, ImageDraw
import spotipy
import tkinter.messagebox as messagebox
from tkinter import font as tkfont
from spotipy.exceptions import SpotifyException
from ytmusicapi import YTMusic

# Toda a lógica (sem interface) vive em nucleo.py e é compartilhada com o app de celular.
from nucleo import *  # noqa: F401,F403
from nucleo import MotorMigracao, _cortar  # noqa: F401

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('green')

# ============================== TEMA ==========================================
COR_FUNDO = '#0D0E16'
COR_CARTAO = '#151726'
COR_CARTAO_2 = '#1C1F33'
COR_BORDA = '#2A2E48'
COR_TEXTO = '#EDEFF7'
COR_TEXTO_2 = '#8D93B0'
COR_TEAL = '#2DD4BF'
COR_TEAL_HOVER = '#5EEAD4'
COR_ROXO = '#8B5CF6'
COR_OK = '#34D399'
COR_AVISO = '#F5B544'
COR_ERRO = '#F87171'
COR_SOBRE_TEAL = '#05221E'
COR_DESATIVADO = '#555B78'

LINKS_SOCIAIS = [
    ('GitHub', 'github', 'https://github.com/deadbynetsu', '#E6EDF3', 108),
    ('Instagram', 'instagram', 'https://www.instagram.com/deadbynetsu.dev/', '#F472B6', 126),
    ('TikTok', 'tiktok', 'https://www.tiktok.com/@deadbynetsu', '#5EEAD4', 104),
    ('Discord', 'discord', 'https://discord.gg/s9b7R5F6Uh', '#818CF8', 112),
    ('YouTube', 'youtube', 'https://www.youtube.com/@DeadbyNeTsU', '#F87171', 114),
]

FONTE_UI = 'Segoe UI'
FONTE_MONO = 'Consolas'


def definir_fontes(raiz):
    global FONTE_UI, FONTE_MONO
    try:
        familias = {f.lower() for f in tkfont.families(raiz)}
    except Exception:
        return
    for cand in ('Segoe UI', 'SF Pro Text', 'Helvetica Neue', 'Roboto', 'DejaVu Sans'):
        if cand.lower() in familias:
            FONTE_UI = cand
            break
    for cand in ('Cascadia Mono', 'Consolas', 'Menlo', 'DejaVu Sans Mono'):
        if cand.lower() in familias:
            FONTE_MONO = cand
            break


def fonte(tamanho, peso='normal'):
    return (FONTE_UI, tamanho, peso)


def fonte_mono(tamanho):
    return (FONTE_MONO, tamanho)


def _hex_para_rgb(cor):
    cor = cor.lstrip('#')
    return tuple(int(cor[i:i + 2], 16) for i in (0, 2, 4))


RGB_TEAL = _hex_para_rgb(COR_TEAL)
RGB_ROXO = _hex_para_rgb(COR_ROXO)


def cor_do_degrade(t):
    t = min(1.0, max(0.0, t))
    r, g, b = (round(RGB_TEAL[k] + (RGB_ROXO[k] - RGB_TEAL[k]) * t) for k in range(3))
    return f'#{r:02x}{g:02x}{b:02x}'


def degrade_horizontal(largura, altura):
    linha = Image.new('RGB', (largura, 1))
    px = linha.load()
    for x in range(largura):
        t = x / max(largura - 1, 1)
        px[x, 0] = tuple(round(RGB_TEAL[k] + (RGB_ROXO[k] - RGB_TEAL[k]) * t) for k in range(3))
    return linha.resize((largura, altura))



# ============================== ÍCONES ========================================
_CACHE_ICONES = {}


def _desenhar_icone(nome, cor):
    S = 512
    u = S / 100.0
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    T = (0, 0, 0, 0)

    def P(*v):
        return [x * u for x in v]

    def W(x):
        return max(1, int(x * u))

    def linha(pontos, larg=7):
        pts = P(*pontos)
        xy = list(zip(pts[0::2], pts[1::2]))
        d.line(xy, fill=cor, width=W(larg), joint='curve')
        r = W(larg) / 2
        for x, y in (xy[0], xy[-1]):
            d.ellipse((x - r, y - r, x + r, y + r), fill=cor)

    def poligono(pontos, preencher=True, larg=6):
        pts = P(*pontos)
        xy = list(zip(pts[0::2], pts[1::2]))
        if preencher:
            d.polygon(xy, fill=cor)
        d.line(xy + [xy[0]], fill=cor, width=W(larg), joint='curve')

    if nome == 'play':
        poligono((32, 20, 80, 50, 32, 80), larg=8)
    elif nome == 'pause':
        d.rounded_rectangle(P(24, 18, 44, 82), radius=W(6), fill=cor)
        d.rounded_rectangle(P(56, 18, 76, 82), radius=W(6), fill=cor)
    elif nome == 'stop':
        d.rounded_rectangle(P(20, 20, 80, 80), radius=W(10), fill=cor)
    elif nome == 'check':
        linha((20, 52, 41, 73, 80, 28), 11)
    elif nome == 'x':
        linha((27, 27, 73, 73), 10)
        linha((73, 27, 27, 73), 10)
    elif nome == 'copy':
        d.rounded_rectangle(P(36, 14, 86, 64), radius=W(9), outline=cor, width=W(7))
        d.rounded_rectangle(P(14, 36, 64, 86), radius=W(9), fill=T)
        d.rounded_rectangle(P(14, 36, 64, 86), radius=W(9), outline=cor, width=W(7))
    elif nome == 'globe':
        d.ellipse(P(14, 14, 86, 86), outline=cor, width=W(7))
        d.ellipse(P(37, 14, 63, 86), outline=cor, width=W(6))
        linha((14, 50, 86, 50), 6)
    elif nome == 'link':
        camada = Image.new('RGBA', (S, S), (0, 0, 0, 0))
        dc = ImageDraw.Draw(camada)
        dc.rounded_rectangle(P(6, 36, 56, 64), radius=W(14), outline=cor, width=W(8))
        dc.rounded_rectangle(P(44, 36, 94, 64), radius=W(14), outline=cor, width=W(8))
        img.alpha_composite(camada.rotate(45, resample=Image.BICUBIC, center=(S / 2, S / 2)))
    elif nome == 'clock':
        d.ellipse(P(14, 14, 86, 86), outline=cor, width=W(7))
        linha((50, 29, 50, 52, 67, 62), 7)
    elif nome == 'trash':
        linha((18, 28, 82, 28), 7)
        linha((38, 28, 38, 18, 62, 18, 62, 28), 6)
        linha((26, 38, 32, 84, 68, 84, 74, 38), 7)
        linha((43, 50, 44, 72), 6)
        linha((57, 50, 56, 72), 6)
    elif nome == 'music':
        d.ellipse(P(16, 62, 46, 88), fill=cor)
        d.rectangle(P(38, 16, 47, 76), fill=cor)
        poligono((47, 16, 82, 28, 82, 46, 47, 34), larg=2)
    elif nome == 'swap':
        linha((20, 36, 80, 36), 9)
        linha((64, 20, 80, 36, 64, 52), 9)
        linha((80, 66, 20, 66), 9)
        linha((36, 50, 20, 66, 36, 82), 9)
    elif nome == 'spotify':
        d.ellipse(P(8, 8, 92, 92), fill=cor)
        d.arc(P(6, 36, 94, 124), 232, 308, fill=T, width=W(8))
        d.arc(P(16, 46, 84, 114), 232, 308, fill=T, width=W(7))
        d.arc(P(25, 55, 75, 105), 230, 310, fill=T, width=W(6))
    elif nome == 'ytmusic':
        d.ellipse(P(10, 10, 90, 90), outline=cor, width=W(8))
        poligono((40, 33, 68, 50, 40, 67), larg=4)
    elif nome == 'github':
        d.ellipse(P(10, 20, 90, 96), fill=cor)
        d.polygon(P(16, 44, 18, 8, 42, 26), fill=cor)
        d.polygon(P(84, 44, 82, 8, 58, 26), fill=cor)
        d.arc(P(24, 56, 76, 108), 20, 160, fill=T, width=W(6))
    elif nome == 'instagram':
        d.rounded_rectangle(P(10, 10, 90, 90), radius=W(24), outline=cor, width=W(8))
        d.ellipse(P(30, 30, 70, 70), outline=cor, width=W(8))
        d.ellipse(P(68, 21, 79, 32), fill=cor)
    elif nome == 'tiktok':
        d.ellipse(P(18, 56, 52, 90), fill=cor)
        d.rectangle(P(42, 14, 54, 74), fill=cor)
        linha((50, 16, 58, 32, 72, 38, 82, 38), 11)
    elif nome == 'discord':
        d.rounded_rectangle(P(12, 18, 88, 72), radius=W(20), fill=cor)
        d.polygon(P(12, 54, 30, 66, 16, 84), fill=cor)
        d.polygon(P(88, 54, 70, 66, 84, 84), fill=cor)
        d.ellipse(P(28, 36, 45, 58), fill=T)
        d.ellipse(P(55, 36, 72, 58), fill=T)
        d.arc(P(30, 52, 70, 90), 30, 150, fill=T, width=W(5))
    elif nome == 'youtube':
        d.rounded_rectangle(P(6, 22, 94, 78), radius=W(18), fill=cor)
        d.polygon(P(42, 36, 42, 64, 66, 50), fill=T)
    else:
        raise ValueError(f'Ícone desconhecido: {nome}')

    return img.resize((128, 128), Image.LANCZOS)


def icone(nome, cor='#EDEFF7', tam=18):
    chave = (nome, cor, tam)
    if chave not in _CACHE_ICONES:
        img = _desenhar_icone(nome, cor)
        _CACHE_ICONES[chave] = ctk.CTkImage(light_image=img, dark_image=img, size=(tam, tam))
    return _CACHE_ICONES[chave]


_ESTILOS_BOTAO = {
    'primario': dict(fg_color=COR_TEAL, hover_color=COR_TEAL_HOVER, text_color=COR_SOBRE_TEAL,
                     border_width=0, _icone=COR_SOBRE_TEAL),
    'secundario': dict(fg_color=COR_CARTAO_2, hover_color='#262A44', text_color=COR_TEXTO,
                       border_width=1, border_color=COR_BORDA, _icone=COR_TEXTO),
    'perigo': dict(fg_color='transparent', hover_color='#2E1A24', text_color=COR_ERRO,
                   border_width=1, border_color='#5A2A38', _icone=COR_ERRO),
    'fantasma': dict(fg_color='transparent', hover_color=COR_CARTAO_2, text_color=COR_TEXTO_2,
                     border_width=0, _icone=COR_TEXTO_2),
}


def botao(pai, texto, nome_icone=None, estilo='secundario', comando=None,
          altura=38, largura=0, cor_icone=None):
    cfg = dict(_ESTILOS_BOTAO[estilo])
    cor_padrao_icone = cfg.pop('_icone')
    btn = ctk.CTkButton(
        pai, text=texto, command=comando, height=altura, corner_radius=10,
        width=largura or max(96, int(46 + 7.4 * len(texto))),
        font=fonte(13, 'bold'), text_color_disabled=COR_DESATIVADO, compound='left', **cfg)
    btn._icone_nome = nome_icone
    btn._cor_icone = cor_icone or cor_padrao_icone
    if nome_icone:
        btn.configure(image=icone(nome_icone, btn._cor_icone))
    return btn


def definir_ativo(btn, ativo, texto=None):
    opcoes = {'state': 'normal' if ativo else 'disabled'}
    if texto is not None:
        opcoes['text'] = texto
    if btn._icone_nome:
        opcoes['image'] = icone(btn._icone_nome, btn._cor_icone if ativo else COR_DESATIVADO)
    btn.configure(**opcoes)


_CORES_STATUS = {'ok': COR_OK, 'aviso': COR_AVISO, 'erro': COR_ERRO, 'info': COR_TEXTO_2}
_SIMBOLOS_STATUS = {'ok': '✓', 'aviso': '⚠', 'erro': '✕', 'info': '●'}


def definir_status(rotulo, tipo, texto):
    rotulo.configure(text=f'{_SIMBOLOS_STATUS[tipo]}  {texto}', text_color=_CORES_STATUS[tipo])


def campo_texto(pai, placeholder):
    return ctk.CTkEntry(pai, placeholder_text=placeholder, height=38, corner_radius=10,
                        fg_color=COR_CARTAO_2, border_color=COR_BORDA, border_width=1,
                        text_color=COR_TEXTO, placeholder_text_color='#5E6485', font=fonte(13))


def cartao(pai, **kw):
    return ctk.CTkFrame(pai, fg_color=COR_CARTAO, corner_radius=14,
                        border_width=1, border_color=COR_BORDA, **kw)


def imagem_logo(tam=44):
    S = 176
    fundo = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    mascara = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, S - 1, S - 1), radius=S // 4, fill=255)
    fundo.paste(degrade_horizontal(S, S), (0, 0), mascara)
    simbolo = _desenhar_icone('swap', '#06201D').resize((S * 3 // 5, S * 3 // 5), Image.LANCZOS)
    fundo.alpha_composite(simbolo, ((S - simbolo.width) // 2, (S - simbolo.height) // 2))
    return ctk.CTkImage(light_image=fundo, dark_image=fundo, size=(tam, tam))


class BarraProgresso(ctk.CTkFrame):
    ESCALA = 2

    def __init__(self, pai, altura=14, cor_trilho=COR_CARTAO_2):
        super().__init__(pai, fg_color='transparent', height=altura)
        self.pack_propagate(False)
        self._h = altura
        self._cor_trilho = cor_trilho
        self._alvo = 0.0
        self._atual = 0.0
        self._largura = 0
        self._job = None
        self._degrade = None
        self._img = None
        self._rotulo = ctk.CTkLabel(self, text='', height=altura, fg_color='transparent')
        self._rotulo.pack(fill='both', expand=True)
        self.bind('<Configure>', self._ao_redimensionar)

    def _ao_redimensionar(self, evento):
        try:
            largura = round(self._reverse_widget_scaling(evento.width))
        except Exception:
            largura = evento.width
        if largura > 10 and largura != self._largura:
            self._largura = largura
            self._desenhar()

    def definir(self, valor, animar=True):
        self._alvo = min(1.0, max(0.0, float(valor)))
        if not animar:
            if self._job is not None:
                self.after_cancel(self._job)
                self._job = None
            self._atual = self._alvo
            self._desenhar()
        elif self._job is None:
            self._animar()

    def _animar(self):
        self._job = None
        dif = self._alvo - self._atual
        if abs(dif) < 0.002:
            self._atual = self._alvo
            self._desenhar()
            return
        self._atual += dif * 0.25
        self._desenhar()
        self._job = self.after(33, self._animar)

    def _desenhar(self):
        w = self._largura
        if w <= 10:
            return
        e = self.ESCALA
        W, H = w * e, self._h * e
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=self._cor_trilho)
        largura_fill = int(W * self._atual)
        if largura_fill > 0:
            largura_fill = max(largura_fill, H)
            if self._degrade is None or self._degrade.size != (W, H):
                self._degrade = degrade_horizontal(W, H)
            mascara = Image.new('L', (W, H), 0)
            ImageDraw.Draw(mascara).rounded_rectangle((0, 0, largura_fill - 1, H - 1), radius=H // 2, fill=255)
            img.paste(self._degrade, (0, 0), mascara)
        self._img = ctk.CTkImage(light_image=img, dark_image=img, size=(w, self._h))
        self._rotulo.configure(image=self._img)


# ============================== AUTO-UPDATER ==================================
def exibir_janela_atualizacao(parent, nova_versao, url_release, notas):
    """Cria uma janela modal customizada combinando com o tema do aplicativo."""
    aberta = getattr(parent, '_popup_atualizacao', None)
    if aberta is not None:
        try:
            if aberta.winfo_exists():
                aberta.lift()
                aberta.focus_force()
                return
        except Exception:
            pass

    popup = ctk.CTkToplevel(parent)
    parent._popup_atualizacao = popup
    popup.title("🚀 Nova Atualização Disponível!")
    popup.geometry("480x340")
    popup.configure(fg_color=COR_FUNDO)
    popup.resizable(False, False)
    popup.transient(parent)

    popup.attributes("-topmost", True)
    popup.focus_force()

    lbl_titulo = ctk.CTkLabel(
        popup,
        text="🎉 Nova Versão Disponível!",
        font=fonte(18, "bold"),
        text_color=COR_OK,
    )
    lbl_titulo.pack(pady=(20, 5))

    lbl_versao = ctk.CTkLabel(
        popup,
        text=f"Você está na v{APP_VERSION}. A versão {nova_versao} já está disponível!",
        font=fonte(12, "bold"),
        text_color=COR_TEXTO,
    )
    lbl_versao.pack(pady=5)

    txt_notas = ctk.CTkTextbox(
        popup,
        height=120,
        width=420,
        font=fonte(11),
        fg_color=COR_CARTAO,
        text_color=COR_TEXTO_2,
        corner_radius=10,
        border_width=1,
        border_color=COR_BORDA,
    )
    txt_notas.pack(pady=10)
    txt_notas.insert("1.0", f"Notas da versão:\n{notas if notas else 'Sem detalhes adicionais.'}")
    txt_notas.configure(state="disabled")

    def ir_para_download():
        webbrowser.open(url_release)
        popup.destroy()

    btn_download = botao(
        popup,
        "⬇️ Baixar Atualização no GitHub",
        "globe",
        "primario",
        ir_para_download,
        altura=40,
        largura=260,
    )
    btn_download.pack(pady=(5, 15))


def checar_atualizacao(app_root, versao_atual, repo, manual=False):
    """Roda em segundo plano. O resultado volta para a janela pela fila de UI (nunca direto da thread)."""
    try:
        info = buscar_ultima_versao(repo)
        app_root.ui(app_root._resultado_atualizacao, manual,
                    versao_mais_nova(info['tag'], versao_atual), info, None)
    except Exception as e:
        registrar_erro_em_arquivo()
        app_root.ui(app_root._resultado_atualizacao, manual, False, None, str(e))


def verificar_atualizacoes_auto(app_root):
    """Checagem silenciosa na abertura, se estiver ativada nas configurações."""
    if not checar_atualizacoes_habilitadas():
        return
    threading.Thread(target=checar_atualizacao, args=(app_root, APP_VERSION, GITHUB_REPO),
                     daemon=True).start()


# ============================== INTERFACE =====================================
class MigradorApp(MotorMigracao, ctk.CTk):
    def __init__(self):
        super().__init__()
        definir_fontes(self)

        self.title(f'RHYFT (v{APP_VERSION})')
        self.geometry('920x790')
        self.minsize(860, 720)
        self.configure(fg_color=COR_FUNDO)

        self._fila_ui = queue.Queue()
        self._controle = None
        self.modo = 'sp_yt'   # 'sp_yt' = Spotify ➔ YouTube Music | 'yt_sp' = YouTube Music ➔ Spotify
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # Linha 0: cabeçalho
        cab = ctk.CTkFrame(self, fg_color='transparent')
        cab.grid(row=0, column=0, padx=24, pady=(20, 4), sticky='ew')
        cab.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(cab, text='', image=imagem_logo(46), width=46, height=46).grid(
            row=0, column=0, rowspan=2, padx=(0, 14))
        ctk.CTkLabel(cab, text='RHYFT', font=fonte(22, 'bold'),
                     text_color=COR_TEXTO, anchor='w').grid(row=0, column=1, sticky='sw')
        ctk.CTkLabel(cab, text='Passe suas playlists entre o Spotify e o YouTube Music',
                     font=fonte(12), text_color=COR_TEXTO_2, anchor='w').grid(row=1, column=1, sticky='nw')

        # Topo Direito: Versão + Switch de Atualização Automática
        frame_top_right = ctk.CTkFrame(cab, fg_color='transparent')
        frame_top_right.grid(row=0, column=2, rowspan=2, sticky='e')

        self.switch_update_var = ctk.BooleanVar(value=checar_atualizacoes_habilitadas())
        self.switch_update = ctk.CTkSwitch(
            frame_top_right,
            text='Buscar atualizações',
            font=fonte(11),
            text_color=COR_TEXTO_2,
            progress_color=COR_TEAL,
            button_color=COR_TEXTO,
            button_hover_color=COR_TEAL_HOVER,
            variable=self.switch_update_var,
            command=self._ao_alternar_atualizacao
        )
        self.switch_update.pack(side='right', padx=(10, 0))

        self.btn_checar_update = botao(frame_top_right, 'Verificar agora', None, 'secundario',
                                       self.checar_atualizacao_manual, altura=28, largura=118)
        self.btn_checar_update.pack(side='right', padx=(10, 0))

        lbl_versao = ctk.CTkLabel(frame_top_right, text=f'v{APP_VERSION}', font=fonte(11, 'bold'),
                                  text_color=COR_TEXTO_2, fg_color=COR_CARTAO, corner_radius=8,
                                  width=56, height=24)
        lbl_versao.pack(side='right')

        # Linha 1: contas
        contas = ctk.CTkFrame(self, fg_color='transparent')
        contas.grid(row=1, column=0, padx=24, pady=(14, 0), sticky='ew')
        contas.grid_columnconfigure((0, 1), weight=1, uniform='contas')
        self.lbl_status_spotify, self.btn_spotify = self._criar_cartao_conta(
            contas, 0, 'spotify', '#1ED760', 'Spotify', self.abrir_janela_spotify)
        self.lbl_status_yt, self.btn_yt = self._criar_cartao_conta(
            contas, 1, 'ytmusic', '#FF4E45', 'YouTube Music', self.abrir_janela_yt)

        # Linha 2: nova migração
        form = cartao(self)
        form.grid(row=2, column=0, padx=24, pady=(12, 0), sticky='ew')
        form.grid_columnconfigure((0, 1), weight=1, uniform='campos')

        self._rotulos_modo = {'Spotify  ➔  YouTube Music': 'sp_yt', 'YouTube Music  ➔  Spotify': 'yt_sp'}
        self.seg_modo = ctk.CTkSegmentedButton(
            form, values=list(self._rotulos_modo), command=self._ao_trocar_modo, height=36,
            font=fonte(13, 'bold'), fg_color=COR_CARTAO_2, unselected_color=COR_CARTAO_2,
            unselected_hover_color='#262A44', selected_color=COR_ROXO, selected_hover_color='#7C4DEB',
            text_color=COR_TEXTO)
        self.seg_modo.set('Spotify  ➔  YouTube Music')
        self.seg_modo.grid(row=0, column=0, columnspan=2, sticky='w', padx=18, pady=(16, 0))

        # entry_spotify = link/ID da playlist de ORIGEM; entry_yt = nome da playlist nova (DESTINO)
        self.lbl_campo_origem = ctk.CTkLabel(form, text='Link ou ID da playlist do Spotify', font=fonte(12, 'bold'),
                                             text_color=COR_TEXTO_2, anchor='w')
        self.lbl_campo_origem.grid(row=1, column=0, sticky='w', padx=(18, 8), pady=(14, 4))
        self.entry_spotify = campo_texto(form, 'https://open.spotify.com/playlist/...')
        self.entry_spotify.grid(row=2, column=0, sticky='ew', padx=(18, 8))

        self.lbl_campo_destino = ctk.CTkLabel(form, text='Nome da nova playlist no YouTube Music',
                                              font=fonte(12, 'bold'), text_color=COR_TEXTO_2, anchor='w')
        self.lbl_campo_destino.grid(row=1, column=1, sticky='w', padx=(8, 18), pady=(14, 4))
        self.entry_yt = campo_texto(form, 'Minha Playlist Importada')
        self.entry_yt.grid(row=2, column=1, sticky='ew', padx=(8, 18))

        acoes = ctk.CTkFrame(form, fg_color='transparent')
        acoes.grid(row=3, column=0, columnspan=2, sticky='ew', padx=18, pady=(16, 18))
        acoes.grid_columnconfigure(3, weight=1)
        self.btn_iniciar = botao(acoes, 'Iniciar migração', 'play', 'primario',
                                 self.iniciar_thread, altura=42, largura=176)
        self.btn_iniciar.grid(row=0, column=0, padx=(0, 8))
        self.btn_pausar = botao(acoes, 'Pausar', 'pause', 'secundario',
                                self.pausar_migracao, altura=42, largura=116)
        self.btn_pausar.grid(row=0, column=1, padx=8)
        self.btn_cancelar = botao(acoes, 'Cancelar', 'stop', 'perigo',
                                  self.cancelar_migracao, altura=42, largura=122)
        self.btn_cancelar.grid(row=0, column=2, padx=8)
        self.btn_historico = botao(acoes, 'Histórico', 'clock', 'fantasma',
                                   self.abrir_janela_historico, altura=42, largura=122)
        self.btn_historico.grid(row=0, column=4, sticky='e')
        definir_ativo(self.btn_pausar, False)
        definir_ativo(self.btn_cancelar, False)

        # Linha 3: progresso
        prog = cartao(self)
        prog.grid(row=3, column=0, padx=24, pady=(12, 0), sticky='ew')
        prog.grid_columnconfigure(0, weight=1)
        self.lbl_prog_titulo = ctk.CTkLabel(prog, text='', font=fonte(15, 'bold'),
                                            text_color=COR_TEXTO, anchor='w')
        self.lbl_prog_titulo.grid(row=0, column=0, sticky='w', padx=(18, 8), pady=(14, 0))
        self.lbl_prog_detalhe = ctk.CTkLabel(prog, text='', font=fonte(12),
                                             text_color=COR_TEXTO_2, anchor='w')
        self.lbl_prog_detalhe.grid(row=1, column=0, sticky='w', padx=(18, 8))
        self.lbl_pct = ctk.CTkLabel(prog, text='0%', font=fonte(30, 'bold'),
                                    text_color=COR_TEAL, anchor='e')
        self.lbl_pct.grid(row=0, column=1, rowspan=2, sticky='e', padx=(8, 18), pady=(10, 0))
        self.barra = BarraProgresso(prog, altura=14)
        self.barra.grid(row=2, column=0, columnspan=2, sticky='ew', padx=18, pady=(10, 18))
        self._ui_progresso_reset()

        # Linha 4: registro
        self.log_box = ctk.CTkTextbox(self, state='disabled', font=fonte_mono(12), wrap='word',
                                      fg_color=COR_CARTAO, text_color='#C9CEE6', corner_radius=14,
                                      border_width=1, border_color=COR_BORDA)
        self.log_box.grid(row=4, column=0, padx=24, pady=(12, 0), sticky='nsew')
        self.log_box.tag_config('erro', foreground=COR_ERRO)
        self.log_box.tag_config('sucesso', foreground=COR_OK)
        self.log_box.tag_config('aviso', foreground=COR_AVISO)
        self.log_box.tag_config('info', foreground='#7DD3FC')
        self.log_box.tag_config('cinza', foreground='#7C829D')

        # Linha 5: rodapé com crédito e redes
        rodape = ctk.CTkFrame(self, fg_color='transparent')
        rodape.grid(row=5, column=0, padx=24, pady=(12, 16), sticky='ew')
        rodape.grid_columnconfigure(0, weight=1)
        self.lbl_rodape = ctk.CTkLabel(rodape, text='Feito por deadbynetsu', font=fonte(12),
                                       text_color=COR_TEXTO_2, anchor='w')
        self.lbl_rodape.grid(row=0, column=0, sticky='w')
        redes = ctk.CTkFrame(rodape, fg_color='transparent')
        redes.grid(row=0, column=1, sticky='e')
        for i, (nome, nome_icone, url, cor, largura) in enumerate(LINKS_SOCIAIS):
            botao(redes, nome, nome_icone, 'secundario', lambda u=url: webbrowser.open(u),
                  altura=32, largura=largura, cor_icone=cor).grid(row=0, column=i, padx=(0 if i == 0 else 6, 0))

        self.after(100, self._processar_fila)
        migrar_arquivos_antigos()
        self.atualizar_status_vinculos(mostrar_dicas=True)
        verificar_atualizacoes_auto(self)

    def _ao_trocar_modo(self, valor):
        self.modo = self._rotulos_modo.get(valor, 'sp_yt')
        reverso = self.modo == 'yt_sp'
        self.lbl_campo_origem.configure(
            text='Link ou ID da playlist do YouTube Music' if reverso else 'Link ou ID da playlist do Spotify')
        self.lbl_campo_destino.configure(
            text='Nome da nova playlist no Spotify (vazio = mesmo nome)' if reverso
            else 'Nome da nova playlist no YouTube Music')
        self.entry_spotify.configure(
            placeholder_text='https://music.youtube.com/playlist?list=...' if reverso
            else 'https://open.spotify.com/playlist/...')
        self.entry_yt.configure(placeholder_text='Mesmo nome da playlist original' if reverso
                                else 'Minha Playlist Importada')
        self.entry_spotify.delete(0, ctk.END)
        self.entry_yt.delete(0, ctk.END)
        self.focus_set()
        self._ui_progresso_reset()

    def checar_atualizacao_manual(self):
        if not self.btn_checar_update.cget('state') == 'normal':
            return
        definir_ativo(self.btn_checar_update, False, 'Verificando...')
        threading.Thread(target=checar_atualizacao, args=(self, APP_VERSION, GITHUB_REPO, True),
                         daemon=True).start()

    def _resultado_atualizacao(self, manual, ha_nova, info, erro):
        if manual:
            definir_ativo(self.btn_checar_update, True, 'Verificar agora')
        if erro:
            if manual:   # na checagem automática o erro fica só no erros.log, sem incomodar
                messagebox.showerror('Atualizações', erro, parent=self)
            return
        if ha_nova:
            exibir_janela_atualizacao(self, info['tag'], info['url'], info['notas'])
        elif manual:
            messagebox.showinfo('Atualizações', f'Você já está na versão mais recente (v{APP_VERSION}).',
                                parent=self)

    def _ao_alternar_atualizacao(self):
        salvar_config_atualizacao(self.switch_update_var.get())
        if self.switch_update_var.get():
            self.log('🔄 Checagem automática de atualizações ATIVADA.', 'info')
        else:
            self.log('⏹️ Checagem automática de atualizações DESATIVADA.', 'aviso')

    def _criar_cartao_conta(self, pai, coluna, nome_icone, cor_icone, titulo, comando):
        cx = cartao(pai)
        cx.grid(row=0, column=coluna, sticky='ew', padx=(0, 6) if coluna == 0 else (6, 0))
        cx.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(cx, text='', image=icone(nome_icone, cor_icone, 26), width=46, height=46,
                     fg_color=COR_CARTAO_2, corner_radius=12).grid(
            row=0, column=0, rowspan=2, padx=(14, 12), pady=14)
        ctk.CTkLabel(cx, text=titulo, font=fonte(14, 'bold'), text_color=COR_TEXTO,
                     anchor='w').grid(row=0, column=1, sticky='sw', pady=(14, 0))
        status = ctk.CTkLabel(cx, text='', font=fonte(12), anchor='w')
        status.grid(row=1, column=1, sticky='nw', pady=(0, 14))
        btn = botao(cx, 'Vincular', 'link', 'secundario', comando, altura=34, largura=112)
        btn.grid(row=0, column=2, rowspan=2, padx=(8, 14))
        return status, btn

    def ui(self, fn, *args):
        self._fila_ui.put((fn, args))

    def _processar_fila(self):
        try:
            while True:
                fn, args = self._fila_ui.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    registrar_erro_em_arquivo()
        except queue.Empty:
            pass
        self.after(100, self._processar_fila)

    def perguntar(self, titulo, mensagem):
        evento, resposta = threading.Event(), {}

        def _mostrar():
            try:
                resposta['v'] = messagebox.askyesno(titulo, mensagem)
            finally:
                evento.set()

        self.ui(_mostrar)
        evento.wait()
        return resposta.get('v', False)

    def escolher_versao(self, n, total, faixa_spotify, opcoes, origem='Spotify'):
        """Mostra até 3 candidatos do YouTube Music. Devolve o escolhido ou None (pular)."""
        evento, resposta = threading.Event(), {}

        def _mostrar():
            try:
                janela = self._nova_janela(f'Aprovação manual ({n} de {total})',
                                           f'600x{250 + 56 * len(opcoes)}')
            except Exception:
                evento.set()
                raise

            def fechar(valor=None):
                if 'v' in resposta:
                    return
                resposta['v'] = valor
                try:
                    janela.grab_release()
                    janela.destroy()
                finally:
                    evento.set()

            janela.protocol('WM_DELETE_WINDOW', fechar)
            janela.lift()
            self._titulo_janela(janela, 'Qual versão adicionar?',
                                f'Faixa {n} de {total} aguardando aprovação')
            ctk.CTkLabel(janela, text=f'No {origem}:  {_cortar(faixa_spotify, 62)}', font=fonte(13, 'bold'),
                         text_color=COR_TEXTO, anchor='w').pack(fill='x', padx=24, pady=(0, 10))
            for c in opcoes:
                rotulo = f'{_cortar(c["yt_title"], 46)}  \u2014  {_cortar(c["yt_artist"] or "?", 28)}'
                botao(janela, rotulo, 'music', 'secundario', lambda c=c: fechar(c),
                      altura=42).pack(fill='x', padx=24, pady=4)
            botao(janela, 'Nenhuma dessas (pular)', None, 'perigo', fechar,
                  altura=38).pack(fill='x', padx=24, pady=(12, 0))

        self.ui(_mostrar)
        evento.wait()
        return resposta.get('v')

    def log(self, mensagem, tipo='normal'):
        self.ui(self._log_ui, mensagem, tipo)

    def _log_ui(self, mensagem, tipo):
        self.log_box.configure(state='normal')
        if tipo == 'normal':
            self.log_box.insert(ctk.END, mensagem + '\n')
        else:
            self.log_box.insert(ctk.END, mensagem + '\n', tipo)
        self.log_box.see(ctk.END)
        self.log_box.configure(state='disabled')

    def _limpar_log(self):
        self.log_box.configure(state='normal')
        self.log_box.delete('1.0', ctk.END)
        self.log_box.configure(state='disabled')

    def _copiar(self, texto):
        self.clipboard_clear()
        self.clipboard_append(texto)

    def _nova_janela(self, titulo, geometria):
        janela = ctk.CTkToplevel(self)
        janela.title(titulo)
        janela.geometry(geometria)
        janela.configure(fg_color=COR_FUNDO)
        janela.transient(self)
        janela.after(200, lambda: janela.winfo_exists() and janela.grab_set())
        return janela

    @staticmethod
    def _titulo_janela(pai, titulo, subtitulo):
        ctk.CTkLabel(pai, text=titulo, font=fonte(20, 'bold'), text_color=COR_TEXTO,
                     anchor='w').pack(fill='x', padx=24, pady=(18, 0))
        ctk.CTkLabel(pai, text=subtitulo, font=fonte(12), text_color=COR_TEXTO_2,
                     anchor='w').pack(fill='x', padx=24, pady=(0, 12))

    @staticmethod
    def _lista_passos(pai, passos, largura_texto=560):
        quadro = cartao(pai)
        quadro.pack(fill='x', padx=24)
        for n, texto in enumerate(passos, 1):
            linha = ctk.CTkFrame(quadro, fg_color='transparent')
            linha.pack(fill='x', padx=14, pady=(14 if n == 1 else 7, 14 if n == len(passos) else 0))
            ctk.CTkLabel(linha, text=str(n), width=24, height=24, corner_radius=12,
                         fg_color=COR_CARTAO_2, text_color=COR_TEAL,
                         font=fonte(12, 'bold')).pack(side='left', anchor='n', padx=(0, 12))
            ctk.CTkLabel(linha, text=texto, font=fonte(12), text_color=COR_TEXTO, justify='left',
                         anchor='w', wraplength=largura_texto).pack(side='left', fill='x', expand=True)
        return quadro

    def atualizar_status_vinculos(self, mostrar_dicas=False):
        sp_ok = spotify_vinculado()
        yt_ok = os.path.exists(YT_AUTH_PATH)
        definir_status(self.lbl_status_spotify, 'ok' if sp_ok else 'aviso',
                       'Vinculado' if sp_ok else 'Não vinculado')
        self.btn_spotify.configure(text='Gerenciar' if sp_ok else 'Vincular')
        definir_status(self.lbl_status_yt, 'ok' if yt_ok else 'aviso',
                       'Vinculado' if yt_ok else 'Não vinculado')
        self.btn_yt.configure(text='Gerenciar' if yt_ok else 'Vincular')
        if mostrar_dicas:
            if sp_ok and yt_ok:
                self.log('✅ Spotify e YouTube Music vinculados. É só colar o link da playlist e iniciar!', 'sucesso')
            else:
                self.log('👋 Antes de migrar, vincule as suas contas (uma vez só):', 'aviso')
                if not sp_ok:
                    self.log("   • Clique em 'Vincular' no cartão do Spotify.", 'aviso')
                if not yt_ok:
                    self.log("   • Clique em 'Vincular' no cartão do YouTube Music.", 'aviso')

    def _mostrar_pct(self, fracao, animar=True):
        self.lbl_pct.configure(text=f'{int(fracao * 100)}%', text_color=cor_do_degrade(fracao))
        self.barra.definir(fracao, animar)

    def _ui_progresso_reset(self, titulo='Pronto para começar',
                            detalhe='Cole o link da playlist e clique em Iniciar migração.'):
        self.lbl_prog_titulo.configure(text=titulo)
        self.lbl_prog_detalhe.configure(text=detalhe)
        self._mostrar_pct(0.0, animar=False)

    def _ui_progresso(self, feitos, total, detalhe='', fase='faixas'):
        if fase == 'aprovacao':
            titulo = f'Aprovação manual: {min(feitos + 1, total)} de {total}'
        else:
            titulo = f'Item {min(feitos + 1, total)} de {total}'
        self.lbl_prog_titulo.configure(text=titulo)
        self.lbl_prog_detalhe.configure(text=_cortar(detalhe))
        self._mostrar_pct(feitos / total if total else 0.0)

    def _ui_progresso_status(self, titulo, detalhe=''):
        self.lbl_prog_titulo.configure(text=titulo)
        self.lbl_prog_detalhe.configure(text=_cortar(detalhe, 90))

    def _ui_progresso_fim(self, titulo, detalhe=''):
        self._ui_progresso_status(titulo, detalhe)
        self._mostrar_pct(1.0)

    def abrir_janela_spotify(self):
        janela = self._nova_janela('Vincular conta do Spotify', '700x780')
        cancelar = threading.Event()

        def fechar():
            cancelar.set()
            janela.destroy()

        janela.protocol('WM_DELETE_WINDOW', fechar)
        corpo = ctk.CTkScrollableFrame(janela, fg_color='transparent')
        corpo.pack(fill='both', expand=True, padx=4, pady=4)

        self._titulo_janela(corpo, 'Vincular o Spotify', 'Você só precisa fazer isso uma vez.')
        self._lista_passos(corpo, [
            'Clique no botão abaixo e entre com a sua conta do Spotify. O Spotify exige que a '
            'conta que cria o app seja Premium.',
            'Clique em “Create app”. Nome e descrição podem ser qualquer coisa.',
            'Em “Redirect URI”, adicione exatamente o endereço mostrado mais abaixo e clique em “Add”.',
            'Em “Which API/SDKs are you planning to use?”, marque “Web API”, aceite os termos e '
            'clique em “Save”.',
            'Abra o app criado, vá em “Settings” e copie o “Client ID” (o Client Secret não é necessário).',
            'Cole o Client ID abaixo e clique em “Vincular e autorizar”. No navegador, aceite: o app pede '
            'permissão para ler e criar playlists na sua conta.',
        ])

        botao(corpo, 'Abrir o painel do Spotify para desenvolvedores', 'globe', 'secundario',
              lambda: webbrowser.open('https://developer.spotify.com/dashboard'),
              altura=36, largura=340).pack(anchor='w', padx=24, pady=(12, 0))

        quadro_uri = cartao(corpo)
        quadro_uri.pack(fill='x', padx=24, pady=(12, 0))
        ctk.CTkLabel(quadro_uri, text='Redirect URI', font=fonte(12, 'bold'),
                     text_color=COR_TEXTO_2).pack(side='left', padx=(16, 12), pady=14)
        ctk.CTkLabel(quadro_uri, text=SPOTIFY_REDIRECT_URI, font=fonte_mono(13),
                     text_color=COR_TEAL).pack(side='left')
        botao(quadro_uri, 'Copiar', 'copy', 'secundario', lambda: self._copiar(SPOTIFY_REDIRECT_URI),
              altura=30, largura=96).pack(side='right', padx=12)

        ctk.CTkLabel(corpo, text='Client ID', font=fonte(12, 'bold'), text_color=COR_TEXTO_2,
                     anchor='w').pack(fill='x', padx=24, pady=(14, 4))
        entry_id = campo_texto(corpo, 'Cole aqui o Client ID (32 caracteres)')
        entry_id.pack(fill='x', padx=24)
        cfg = ler_json(SPOTIFY_CONFIG_PATH, {}) or {}
        if cfg.get('client_id'):
            entry_id.insert(0, cfg['client_id'])

        lbl_status = ctk.CTkLabel(corpo, text='', wraplength=600, justify='left',
                                  font=fonte(12), anchor='w')
        lbl_status.pack(fill='x', padx=24, pady=(12, 0))
        _auth_atual = carregar_auth_spotify()
        if spotify_vinculado() and _auth_atual and not _auth_atual.tem_escrita():
            definir_status(lbl_status, 'aviso',
                           'Vinculado, mas sem permissão para criar playlists. Para usar YouTube Music ➔ '
                           'Spotify, clique em “Vincular e autorizar” de novo.')
        elif spotify_vinculado():
            definir_status(lbl_status, 'ok', 'Spotify já vinculado.')
        else:
            definir_status(lbl_status, 'aviso', 'Ainda não vinculado.')

        frame_btns = ctk.CTkFrame(corpo, fg_color='transparent')
        frame_btns.pack(anchor='w', padx=24, pady=(12, 18))
        btn_vincular = botao(frame_btns, 'Vincular e autorizar', 'link', 'primario',
                             altura=40, largura=240)
        btn_vincular.pack(side='left', padx=(0, 8))
        btn_desvincular = botao(frame_btns, 'Desvincular', 'x', 'perigo', altura=40, largura=134)
        btn_desvincular.pack(side='left')

        def concluir(ok, info):
            self.atualizar_status_vinculos()
            if not janela.winfo_exists():
                return
            definir_ativo(btn_vincular, True, 'Vincular e autorizar')
            if ok:
                quem = f' como {info}' if info else ''
                definir_status(lbl_status, 'ok', f'Spotify vinculado{quem}!')
            else:
                definir_status(lbl_status, 'erro', str(info))

        def vincular():
            client_id = entry_id.get().strip()
            if not re.fullmatch(r'[0-9a-fA-F]{32}', client_id):
                messagebox.showwarning(
                    'Client ID inválido',
                    'O Client ID tem 32 caracteres (letras e números). Copie-o em Settings do seu '
                    'app no painel do Spotify.', parent=janela)
                return
            gravar_json(SPOTIFY_CONFIG_PATH, {'client_id': client_id})
            if os.path.exists(SPOTIFY_TOKEN_PATH):
                os.remove(SPOTIFY_TOKEN_PATH)
            cancelar.clear()
            definir_ativo(btn_vincular, False, 'Aguardando autorização...')
            definir_status(lbl_status, 'info', 'Autorize no navegador que abriu (você tem 3 minutos)...')

            def trabalho():
                try:
                    auth = SpotifyPKCE(client_id, SPOTIFY_TOKEN_PATH)
                    auth.autorizar(cancelar=cancelar)
                    nome = None
                    try:
                        me = spotipy.Spotify(auth_manager=auth, requests_timeout=20).current_user()
                        nome = me.get('display_name') or me.get('id')
                    except Exception:
                        pass
                    self.ui(concluir, True, nome)
                except InterruptedError:
                    pass
                except Exception as e:
                    registrar_erro_em_arquivo()
                    self.ui(concluir, False, explicar_erro(e))

            threading.Thread(target=trabalho, daemon=True).start()

        def desvincular():
            if not messagebox.askyesno('Desvincular', 'Remover a vinculação do Spotify deste computador?', parent=janela):
                return
            for caminho in (SPOTIFY_TOKEN_PATH, SPOTIFY_CONFIG_PATH):
                if os.path.exists(caminho):
                    os.remove(caminho)
            entry_id.delete(0, ctk.END)
            definir_status(lbl_status, 'aviso', 'Ainda não vinculado.')
            self.atualizar_status_vinculos()

        btn_vincular.configure(command=vincular)
        btn_desvincular.configure(command=desvincular)

    def abrir_janela_yt(self):
        janela = self._nova_janela('Vincular conta do YouTube Music', '720x800')
        corpo = ctk.CTkScrollableFrame(janela, fg_color='transparent')
        corpo.pack(fill='both', expand=True, padx=4, pady=4)

        self._titulo_janela(corpo, 'Vincular o YouTube Music', 'Você só precisa fazer isso uma vez.')
        self._lista_passos(corpo, [
            'Clique em “Abrir music.youtube.com” e entre na sua conta.',
            'Aperte F12 e abra a aba “Rede” (Network). Com ela aberta, clique em uma playlist ou na '
            'Biblioteca do site para gerar requisições.',
            'No filtro da aba, digite “browse”. Clique com o botão direito em uma requisição '
            '“browse?...” (método POST) > Copiar > “Copiar como cURL (bash)”.',
            'Cole tudo na caixa abaixo e clique em “Validar e salvar”. O app testa a conexão antes de guardar.',
        ], largura_texto=580)
        ctk.CTkLabel(corpo, text='Funciona no Brave, Chrome e Edge. Se preferir copiar na mão: aba '
                                 '“Cabeçalhos” > “Cabeçalhos da requisição”, do início até o final da '
                                 'lista (o “cookie” é o mais importante).',
                     font=fonte(11), text_color=COR_TEXTO_2, justify='left', anchor='w',
                     wraplength=620).pack(fill='x', padx=24, pady=(8, 0))

        botao(corpo, 'Abrir music.youtube.com', 'globe', 'secundario',
              lambda: webbrowser.open('https://music.youtube.com'),
              altura=36, largura=230).pack(anchor='w', padx=24, pady=(12, 0))

        txt_input = ctk.CTkTextbox(corpo, height=170, font=fonte_mono(10), fg_color=COR_CARTAO,
                                   text_color='#C9CEE6', corner_radius=12, border_width=1,
                                   border_color=COR_BORDA)
        txt_input.pack(fill='x', padx=24, pady=(12, 0))

        lbl_status = ctk.CTkLabel(corpo, text='', wraplength=620, justify='left',
                                  font=fonte(12), anchor='w')
        lbl_status.pack(fill='x', padx=24, pady=(12, 0))
        if os.path.exists(YT_AUTH_PATH):
            definir_status(lbl_status, 'ok', 'YouTube Music já vinculado.')
        else:
            definir_status(lbl_status, 'aviso', 'Ainda não vinculado.')

        frame_btns = ctk.CTkFrame(corpo, fg_color='transparent')
        frame_btns.pack(anchor='w', padx=24, pady=(12, 18))
        btn_salvar = botao(frame_btns, 'Validar e salvar', 'check', 'primario', altura=40, largura=200)
        btn_salvar.pack(side='left', padx=(0, 8))
        btn_desvincular = botao(frame_btns, 'Desvincular', 'x', 'perigo', altura=40, largura=134)
        btn_desvincular.pack(side='left', padx=(0, 8))
        btn_celular = botao(frame_btns, 'Copiar para o celular', 'copy', 'secundario', altura=40, largura=220)
        btn_celular.pack(side='left')

        def concluir(ok, info):
            self.atualizar_status_vinculos()
            if not janela.winfo_exists():
                return
            definir_ativo(btn_salvar, True, 'Validar e salvar')
            if ok:
                definir_status(lbl_status, 'ok', f'YouTube Music vinculado ({info})!')
                txt_input.delete('1.0', ctk.END)
            else:
                definir_status(lbl_status, 'erro', str(info))

        def salvar():
            try:
                headers = normalizar_cabecalhos_yt(txt_input.get('1.0', ctk.END))
            except ValueError as e:
                definir_status(lbl_status, 'erro', str(e))
                return
            definir_ativo(btn_salvar, False, 'Testando conexão...')
            definir_status(lbl_status, 'info', 'Testando a conexão com o YouTube Music...')

            def trabalho():
                try:
                    nome = validar_e_salvar_yt(headers)
                    self.ui(concluir, True, nome)
                except Exception as e:
                    registrar_erro_em_arquivo()
                    self.ui(concluir, False, explicar_erro(e))

            threading.Thread(target=trabalho, daemon=True).start()

        def desvincular():
            if not messagebox.askyesno('Desvincular', 'Remover a vinculação do YouTube Music deste computador?', parent=janela):
                return
            if os.path.exists(YT_AUTH_PATH):
                os.remove(YT_AUTH_PATH)
            definir_status(lbl_status, 'aviso', 'Ainda não vinculado.')
            self.atualizar_status_vinculos()

        def copiar_para_celular():
            if not messagebox.askyesno(
                    'Copiar para o celular',
                    'O texto copiado contém os cookies de login da sua conta Google. Trate como uma senha: '
                    'envie só para você mesmo (por exemplo, "Mensagens salvas" ou um e-mail para si) e apague '
                    'depois de importar no celular.\n\nCopiar agora?', parent=janela):
                return
            try:
                texto = exportar_vinculacao()
            except Exception as e:
                definir_status(lbl_status, 'erro', str(e))
                return
            self.clipboard_clear()
            self.clipboard_append(texto)
            definir_status(lbl_status, 'ok', 'Copiado! No app do celular, abra “Importar do computador” e cole.')

        btn_salvar.configure(command=salvar)
        btn_desvincular.configure(command=desvincular)
        btn_celular.configure(command=copiar_para_celular)

    def abrir_janela_historico(self):
        janela = self._nova_janela('Gerenciar histórico de progresso', '560x480')

        self._titulo_janela(janela, 'Histórico de progresso',
                            'Migrações pausadas ficam guardadas aqui. Apague as que não quer mais retomar.')
        frame_lista = ctk.CTkScrollableFrame(janela, fg_color=COR_CARTAO, corner_radius=14,
                                             border_width=1, border_color=COR_BORDA)
        frame_lista.pack(padx=24, pady=(0, 20), fill='both', expand=True)

        def atualizar_lista():
            for widget in frame_lista.winfo_children():
                widget.destroy()
            arquivos = glob.glob(os.path.join(DATA_DIR, 'progresso_*.json'))
            if not arquivos:
                ctk.CTkLabel(frame_lista, text='Nenhum histórico guardado.',
                             font=fonte(12), text_color=COR_TEXTO_2).pack(padx=10, pady=40)
                return
            for arquivo in arquivos:
                bruto = os.path.basename(arquivo)[len('progresso_'):-len('.json')]
                reverso_arq = bruto.startswith('yt-sp_')
                if reverso_arq:
                    bruto = bruto[len('yt-sp_'):]
                nome_playlist = bruto.replace('_', ' ') + ('  (YouTube Music ➔ Spotify)' if reverso_arq else '')
                row = ctk.CTkFrame(frame_lista, fg_color=COR_CARTAO_2, corner_radius=10)
                row.pack(fill='x', pady=4, padx=2)
                ctk.CTkLabel(row, text='', image=icone('music', COR_TEAL, 18), width=26).pack(
                    side='left', padx=(12, 6), pady=10)
                ctk.CTkLabel(row, text=nome_playlist, font=fonte(13, 'bold'), text_color=COR_TEXTO,
                             anchor='w').pack(side='left', padx=4, fill='x', expand=True)

                def apagar(arq=arquivo, nome=nome_playlist):
                    if messagebox.askyesno('Confirmar exclusão',
                                           f"Apagar o histórico de progresso da playlist '{nome}'?", parent=janela):
                        try:
                            if os.path.exists(arq):
                                os.remove(arq)
                            atualizar_lista()
                            self.log(f'🗑️ Histórico apagado: {nome}', 'aviso')
                        except Exception as e:
                            messagebox.showerror('Erro', f'Não foi possível apagar o arquivo: {e}', parent=janela)

                botao(row, 'Apagar', 'trash', 'perigo', apagar, altura=30, largura=100).pack(
                    side='right', padx=10)

        atualizar_lista()

    def iniciar_thread(self):
        origem = self.entry_spotify.get().strip()
        nome_destino = self.entry_yt.get().strip()
        reverso = self.modo == 'yt_sp'

        if not origem:
            messagebox.showwarning('Aviso', 'Por favor, insira o link do YouTube Music!' if reverso
                                   else 'Por favor, insira o link do Spotify!')
            return
        if not spotify_vinculado():
            messagebox.showerror('Spotify não vinculado', "Clique em 'Vincular' no cartão do Spotify primeiro.")
            return
        if not os.path.exists(YT_AUTH_PATH):
            messagebox.showerror('YouTube Music não vinculado', "Clique em 'Vincular' no cartão do YouTube Music primeiro.")
            return
        if reverso:
            auth = carregar_auth_spotify()
            if not (auth and auth.tem_escrita()):
                messagebox.showinfo('Permissão nova do Spotify', MSG_REAUTORIZAR)
                return

        if self._controle is not None:
            return

        controle = ControleMigracao()
        self._controle = controle
        definir_ativo(self.btn_iniciar, False, 'Migrando...')
        definir_ativo(self.btn_pausar, True, 'Pausar')
        definir_ativo(self.btn_cancelar, True, 'Cancelar')
        self.seg_modo.configure(state='disabled')
        self._limpar_log()
        if reverso:
            self._ui_progresso_reset('Conectando ao YouTube Music...', 'Lendo as músicas da playlist.')
            alvo = self.processo_migracao_reversa
        else:
            self._ui_progresso_reset('Conectando ao Spotify...', 'Lendo as músicas da playlist.')
            alvo = self.processo_migracao
        threading.Thread(target=alvo, args=(origem, nome_destino, controle), daemon=True).start()

    def pausar_migracao(self):
        controle = self._controle
        if controle is None:
            return
        controle.pausar()
        definir_ativo(self.btn_pausar, False, 'Pausando...')
        self.log('⏸️️ Pausa pedida: termino a música atual e paro em seguida...', 'aviso')

    def cancelar_migracao(self):
        controle = self._controle
        if controle is None:
            return
        if not messagebox.askyesno(
                'Cancelar migração',
                'Cancelar a migração em andamento?\n\n'
                '• O arquivo de progresso será APAGADO (não dá para retomar depois).\n'
                '• A playlist que já foi criada no YouTube Music NÃO será apagada: '
                'ela continua lá, com as músicas já adicionadas.'):
            return
        if self._controle is not controle:
            self.log('ℹ️ A migração já tinha terminado. Para apagar o progresso, use '
                     "o botão 'Histórico'.", 'aviso')
            return
        controle.cancelar()
        definir_ativo(self.btn_pausar, False)
        definir_ativo(self.btn_cancelar, False, 'Cancelando...')
        self.log('🛑 Cancelamento pedido: paro assim que possível...', 'aviso')

    def _ui_migracao_terminada(self, controle):
        if self._controle is controle:
            self._controle = None
        definir_ativo(self.btn_iniciar, True, 'Iniciar migração')
        definir_ativo(self.btn_pausar, False, 'Pausar')
        definir_ativo(self.btn_cancelar, False, 'Cancelar')
        self.seg_modo.configure(state='normal')

    def _ui_travar_controles(self):
        definir_ativo(self.btn_pausar, False)
        definir_ativo(self.btn_cancelar, False)









if __name__ == '__main__':
    app = MigradorApp()
    app.mainloop()