# -*- coding: utf-8 -*-
"""Bootstrap da versão Android.

O YouTube Music usa OAuth 2.0 para aplicativo instalado com PKCE. O usuário final
só toca em "Entrar com Google". O APK precisa apenas do Client ID público do app;
nenhuma chave secreta é embutida no aplicativo.
"""
import base64
import hashlib
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import flet as ft
import requests
from ytmusicapi import OAuthCredentials

try:
    from yt_oauth_config import YT_OAUTH_CLIENT_ID
except ImportError:
    YT_OAUTH_CLIENT_ID = ''

_REAL_FLET_RUN = ft.run
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
YOUTUBE_SCOPE = 'https://www.googleapis.com/auth/youtube'


def _patch_app(target):
    g = target.__globals__
    Tela = g.get('Tela')
    nuc = g.get('nuc')
    if Tela is None or nuc is None:
        return

    client_id = str(YT_OAUTH_CLIENT_ID or '').strip()
    ytmusic_real = nuc.YTMusic

    class PKCECredentials(OAuthCredentials):
        """Só cuida do refresh do token; o login inicial usa Authorization Code + PKCE."""

        def __init__(self):
            # O Google documenta client_secret como opcional para apps instalados.
            super().__init__(client_id=client_id, client_secret='')

        def refresh_token(self, refresh_token):
            resp = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    'client_id': client_id,
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                try:
                    detalhe = resp.json().get('error_description') or resp.json().get('error')
                except Exception:
                    detalhe = resp.text[:200]
                raise RuntimeError(f'Não consegui renovar a sessão do Google: {detalhe}')
            dados = resp.json()
            return {
                'access_token': dados['access_token'],
                'expires_in': int(dados.get('expires_in') or 3600),
            }

    def credenciais_refresh():
        if not client_id:
            raise RuntimeError(
                'Este APK foi compilado sem o Client ID do Google. '
                'Baixe novamente a versão oficial pela página de Releases.'
            )
        return PKCECredentials()

    def criar_ytmusic(auth=None, *args, **kwargs):
        dados = None
        if isinstance(auth, str) and os.path.exists(auth):
            dados = nuc.ler_json(auth, {}) or {}
        elif isinstance(auth, dict):
            dados = auth

        if isinstance(dados, dict) and dados.get('refresh_token') and dados.get('access_token'):
            kwargs.setdefault('oauth_credentials', credenciais_refresh())
        return ytmusic_real(auth, *args, **kwargs)

    nuc.YTMusic = criar_ytmusic

    def trocar_rotulo_importar(page):
        vistos = set()

        def visitar(obj):
            if obj is None or id(obj) in vistos:
                return
            vistos.add(id(obj))
            try:
                if getattr(obj, 'content', None) == 'Importar do computador':
                    obj.content = 'Vincular / gerenciar'
            except Exception:
                pass
            try:
                for c in (getattr(obj, 'controls', None) or []):
                    visitar(c)
            except Exception:
                pass
            try:
                conteudo = getattr(obj, 'content', None)
                if conteudo is not None and not isinstance(conteudo, str):
                    visitar(conteudo)
            except Exception:
                pass

        for c in getattr(page, 'controls', []) or []:
            visitar(c)

    montar_antigo = Tela.montar

    def montar_novo(self):
        montar_antigo(self)
        trocar_rotulo_importar(self.page)

    Tela.montar = montar_novo

    iniciar_antigo = Tela.iniciar

    def iniciar_novo(self, e):
        origem = (self.campo_origem.value or '').strip()
        if (self.controle is None and origem and nuc.spotify_vinculado()
                and not os.path.exists(nuc.YT_AUTH_PATH)):
            self.mensagem(
                'YouTube Music não vinculado',
                'Toque em “Vincular / gerenciar” no cartão do YouTube Music e entre com sua conta Google.'
            )
            return
        return iniciar_antigo(self, e)

    Tela.iniciar = iniciar_novo

    def abrir_yt_novo(self, e):
        status = ft.Text('', size=12)
        btn_vincular = ft.Button(content='Entrar com Google', icon=ft.Icons.LOGIN)
        estado = {'cancelar': threading.Event()}

        def avisar(tipo, texto):
            status.value = texto
            status.color = {'ok': g['OK'], 'erro': g['ERRO'], 'aviso': g['AVISO']}.get(tipo, g['TEXTO2'])
            self.atualizar_status()

        def liberar_botao():
            btn_vincular.disabled = False
            btn_vincular.content = 'Entrar com Google'

        def vincular(e):
            if not client_id:
                avisar('erro', 'Este APK não contém o Client ID do aplicativo. Baixe novamente pela Release oficial.')
                return

            estado['cancelar'].set()
            estado['cancelar'] = threading.Event()
            cancelar = estado['cancelar']
            btn_vincular.disabled = True
            btn_vincular.content = 'Aguardando Google...'
            avisar('aviso', 'Abrindo o Google. Escolha sua conta e autorize o acesso.')

            def trabalho():
                servidor = None
                try:
                    recebido = {}

                    class Handler(BaseHTTPRequestHandler):
                        def do_GET(self):
                            consulta = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                            if 'code' not in consulta and 'error' not in consulta:
                                self.send_response(404)
                                self.end_headers()
                                return
                            recebido.update(consulta)
                            ok = 'code' in consulta and 'error' not in consulta
                            corpo = (
                                '<html><head><meta charset="utf-8"></head><body style="font-family:Arial;text-align:center;margin-top:15%">'
                                + ('<h2>✅ YouTube Music vinculado</h2><p>Pode fechar esta aba e voltar ao aplicativo.</p>'
                                   if ok else '<h2>❌ Autorização não concluída</h2><p>Volte ao aplicativo e tente novamente.</p>')
                                + '</body></html>'
                            ).encode('utf-8')
                            self.send_response(200)
                            self.send_header('Content-Type', 'text/html; charset=utf-8')
                            self.send_header('Content-Length', str(len(corpo)))
                            self.end_headers()
                            self.wfile.write(corpo)

                        def log_message(self, *args):
                            pass

                    servidor = HTTPServer(('127.0.0.1', 0), Handler)
                    servidor.timeout = 1
                    porta = servidor.server_address[1]
                    redirect_uri = f'http://127.0.0.1:{porta}'

                    verifier = secrets.token_urlsafe(64)
                    challenge = base64.urlsafe_b64encode(
                        hashlib.sha256(verifier.encode('ascii')).digest()
                    ).rstrip(b'=').decode('ascii')
                    state = secrets.token_urlsafe(24)

                    url = GOOGLE_AUTH_URL + '?' + urllib.parse.urlencode({
                        'client_id': client_id,
                        'redirect_uri': redirect_uri,
                        'response_type': 'code',
                        'scope': YOUTUBE_SCOPE,
                        'access_type': 'offline',
                        'prompt': 'consent',
                        'state': state,
                        'code_challenge': challenge,
                        'code_challenge_method': 'S256',
                    })

                    if not self._abrir_url_de_thread(url):
                        raise RuntimeError('Não consegui abrir o navegador do celular.')

                    limite = time.time() + 300
                    while not recebido and time.time() < limite:
                        if cancelar.is_set():
                            return
                        servidor.handle_request()

                    if not recebido:
                        raise TimeoutError('Tempo esgotado esperando a autorização do Google. Tente novamente.')
                    if 'error' in recebido:
                        raise RuntimeError(f"O Google recusou a autorização: {recebido['error'][0]}")
                    if recebido.get('state', [None])[0] != state:
                        raise RuntimeError('Resposta inválida do Google. Tente novamente.')

                    codigo = recebido['code'][0]
                    resp = requests.post(
                        GOOGLE_TOKEN_URL,
                        data={
                            'client_id': client_id,
                            'code': codigo,
                            'code_verifier': verifier,
                            'redirect_uri': redirect_uri,
                            'grant_type': 'authorization_code',
                        },
                        timeout=30,
                    )
                    if resp.status_code != 200:
                        try:
                            detalhe = resp.json().get('error_description') or resp.json().get('error')
                        except Exception:
                            detalhe = resp.text[:200]
                        raise RuntimeError(f'O Google recusou o login: {detalhe}')

                    token = resp.json()
                    if not token.get('access_token') or not token.get('refresh_token'):
                        raise RuntimeError('O Google não devolveu uma sessão permanente. Tente autorizar novamente.')

                    token_limpo = {
                        'scope': str(token.get('scope') or YOUTUBE_SCOPE),
                        'token_type': str(token.get('token_type') or 'Bearer'),
                        'access_token': token['access_token'],
                        'refresh_token': token['refresh_token'],
                        'expires_in': int(token.get('expires_in') or 3600),
                        'expires_at': int(time.time()) + int(token.get('expires_in') or 3600),
                    }
                    nuc.gravar_json(nuc.YT_AUTH_PATH, token_limpo)

                    yt = ytmusic_real(nuc.YT_AUTH_PATH, oauth_credentials=credenciais_refresh())
                    try:
                        info_conta = yt.get_account_info() or {}
                        nome = info_conta.get('accountName') or 'conta conectada'
                    except Exception:
                        yt.get_library_playlists(limit=1)
                        nome = 'conta conectada'

                    self.postar(avisar, 'ok', f'✓ YouTube Music vinculado: {nome}')
                    self.postar(liberar_botao)
                except Exception as ex:
                    nuc.registrar_erro_em_arquivo()
                    self.postar(avisar, 'erro', f'Não foi possível vincular: {ex}')
                    self.postar(liberar_botao)
                finally:
                    if servidor is not None:
                        try:
                            servidor.server_close()
                        except Exception:
                            pass

            threading.Thread(target=trabalho, daemon=True).start()

        btn_vincular.on_click = vincular

        def desvincular(e):
            estado['cancelar'].set()
            if os.path.exists(nuc.YT_AUTH_PATH):
                try:
                    os.remove(nuc.YT_AUTH_PATH)
                except OSError:
                    pass
            avisar('aviso', 'YouTube Music desvinculado.')

        def fechar(e):
            estado['cancelar'].set()
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text('Vincular o YouTube Music'),
            content=ft.Column([
                ft.Text('Toque em “Entrar com Google”, escolha sua conta e autorize. Não precisa copiar códigos nem configurar nada.', size=13),
                btn_vincular,
                status,
            ], tight=True, spacing=12, scroll=ft.ScrollMode.AUTO),
            actions=[
                ft.TextButton(content='Desvincular', on_click=desvincular),
                ft.TextButton(content='Fechar', on_click=fechar),
            ],
        ))

    Tela.abrir_yt = abrir_yt_novo


def _patched_run(target, *args, **kwargs):
    _patch_app(target)
    return _REAL_FLET_RUN(target, *args, **kwargs)


ft.run = _patched_run
import main  # noqa: E402,F401
