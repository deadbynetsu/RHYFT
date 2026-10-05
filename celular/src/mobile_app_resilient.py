# -*- coding: utf-8 -*-
"""Entrada Android com rede OAuth e empacotamento mais resilientes.

Alguns aparelhos/redes perdem a resolução DNS por alguns segundos ao alternar
entre o app e o navegador durante o OAuth. Além disso, builds Android podem não
incluir os arquivos .mo de tradução do ytmusicapi. Este wrapper trata ambos os
casos sem exigir configuração extra do usuário.
"""
import gettext
import time

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
        # ytmusicapi usa o domínio "base" apenas para textos/localização.
        # Sem o .mo, os textos originais em inglês continuam funcionando.
        kwargs["fallback"] = True
        return _REAL_TRANSLATION(domain, *args, **kwargs)


# Precisa ser aplicado antes de importar mobile_app/ytmusicapi.
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
            # Ao abrir/fechar o navegador o Android pode ficar alguns segundos sem DNS.
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


# Cobre tanto requests.post usado pelo app quanto Session.post usado pelo ytmusicapi
# para renovar o token depois que o usuário já estiver conectado.
requests.post = _post_resiliente
requests.sessions.Session.post = _session_post_resiliente

# mobile_app aplica o patch de interface e inicia o Flet.
import mobile_app  # noqa: E402,F401
