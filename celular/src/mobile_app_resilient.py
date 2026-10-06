# -*- coding: utf-8 -*-
"""Entrada Android com rede OAuth, UI e YouTube autenticado mais resilientes.

No Android:
- trata falhas temporárias de DNS ao voltar do navegador;
- evita crash se o bundle não trouxer traduções do ytmusicapi;
- usa ytmusicapi sem login apenas para busca pública;
- usa YouTube Data API v3 oficial para operações autenticadas;
- aplica uma interface mobile mais compacta e organizada;
- aplica a identidade visual oficial do app.

A camada HybridYTMusic contorna o bug aberto do ytmusicapi em que chamadas
OAuth ao /youtubei podem retornar HTTP 400 "Request contains an invalid
argument".
"""
import gettext
import time

import flet as ft
import requests

_REAL_POST = requests.post
_REAL_SESSION_POST = requests.sessions.Session.post
_REAL_TRANSLATION = gettext.translation

_DEVICE_PRIMARY = "https://oauth2.googleapis.com/device/code"
_DEVICE_FALLBACK = "https://www.youtube.com/o/oauth2/device/code"
_TOKEN_PRIMARY = "https://oauth2.googleapis.com/token"
_TOKEN_FALLBACK = "https://www.googleapis.com/oauth2/v4/token"


def _translation_resiliente(domain, *args, **kwargs):
    """Evita crash se o bundle Android não contiver locales do ytmusicapi."""
    try:
        return _REAL_TRANSLATION(domain, *args, **kwargs)
    except FileNotFoundError:
        if domain != "base":
            raise
        kwargs["fallback"] = True
        return _REAL_TRANSLATION(domain, *args, **kwargs)


# Precisa ser aplicado antes de importar ytmusicapi.
gettext.translation = _translation_resiliente


def _candidatos(url):
    if url == _DEVICE_PRIMARY:
        return (_DEVICE_PRIMARY, _DEVICE_FALLBACK)
    if url == _TOKEN_PRIMARY:
        return (_TOKEN_PRIMARY, _TOKEN_FALLBACK)
    return (url,)


def _com_retentativa(chamar, url, args, kwargs):
    """Repete apenas erros reais de conexão/DNS; respostas HTTP passam direto."""
    candidatos = _candidatos(url)
    ultimo_erro = None
    limite = time.monotonic() + (75 if url in (_DEVICE_PRIMARY, _TOKEN_PRIMARY) else 20)
    tentativa = 0

    while time.monotonic() < limite:
        destino = candidatos[tentativa % len(candidatos)]
        tentativa += 1
        try:
            return chamar(destino, *args, **kwargs)
        except requests.RequestException as ex:
            ultimo_erro = ex
            time.sleep(min(5, 1 + tentativa))

    if ultimo_erro is not None:
        raise ultimo_erro
    raise requests.ConnectionError("Não foi possível conectar aos servidores do Google.")


def _post_resiliente(url, *args, **kwargs):
    return _com_retentativa(_REAL_POST, url, args, kwargs)


def _session_post_resiliente(self, url, *args, **kwargs):
    def chamar(destino, *a, **k):
        return _REAL_SESSION_POST(self, destino, *a, **k)

    return _com_retentativa(chamar, url, args, kwargs)


requests.post = _post_resiliente
requests.sessions.Session.post = _session_post_resiliente

# O núcleo importa `YTMusic` de ytmusicapi. Trocamos somente no entrypoint do
# Android, então a versão Windows continua exatamente como antes.
import ytmusicapi  # noqa: E402
from youtube_official import HybridYTMusic  # noqa: E402

HybridYTMusic.PUBLIC_CLASS = ytmusicapi.YTMusic
ytmusicapi.YTMusic = HybridYTMusic

# Garante que checagem de atualização e links de Release usem o repositório
# atual do projeto, mesmo enquanto identificadores legados ainda existirem no núcleo.
import nucleo as _nucleo  # noqa: E402
_nucleo.GITHUB_REPO = "deadbynetsu/RHYFT"

# O mobile_app também intercepta ft.run para instalar o OAuth. Colocamos nossas
# camadas ANTES dele: primeiro reorganizamos a UI e depois aplicamos a identidade.
from mobile_ui_refresh import apply_mobile_ui  # noqa: E402
from mobile_branding import apply_mobile_branding  # noqa: E402

_REAL_UI_RUN = ft.run


def _run_com_ui(target, *args, **kwargs):
    apply_mobile_ui(target)
    apply_mobile_branding(target)
    return _REAL_UI_RUN(target, *args, **kwargs)


ft.run = _run_com_ui

# mobile_app aplica o patch de OAuth e inicia o Flet.
import mobile_app  # noqa: E402,F401
