# -*- coding: utf-8 -*-
"""Bootstrap da versão Android.

O usuário final só toca em "Entrar com Google". O APK usa o OAuth Device Flow
oficial do Google para o cliente do tipo "TVs e dispositivos de entrada
limitados" configurado pelo desenvolvedor.
"""
import os
import threading
import time
import urllib.parse

import flet as ft
import requests
from ytmusicapi import OAuthCredentials

try:
    from yt_oauth_config import YT_OAUTH_CLIENT_ID, YT_OAUTH_CLIENT_SECRET
except ImportError:
    YT_OAUTH_CLIENT_ID = ''
    YT_OAUTH_CLIENT_SECRET = ''

_REAL_FLET_RUN = ft.run
DEVICE_CODE_URL = 'https://oauth2.googleapis.com/device/code'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
YOUTUBE_SCOPE = 'https://www.googleapis.com/auth/youtube'


def _patch_app(target):
    g = target.__globals__
    Tela = g.get('Tela')
    nuc = g.get('nuc')
    if Tela is None or nuc is None:
        return

    client_id = str(YT_OAUTH_CLIENT_ID or '').strip()
    client_secret = str(YT_OAUTH_CLIENT_SECRET or '').strip()
    ytmusic_real = nuc.YTMusic

    def oauth_credentials():
        if not client_id or not client_secret:
            raise RuntimeError(
                'Este APK foi compilado sem as credenciais OAuth do aplicativo. '
                'Baixe novamente a versão oficial pela página de Releases.'
            )
        return OAuthCredentials(client_id=client_id, client_secret=client_secret)

    def criar_ytmusic(auth=None, *args, **kwargs):
        dados = None
        if isinstance(auth, str) and os.path.exists(auth):
            dados = nuc.ler_json(auth, {}) or {}
        elif isinstance(auth, dict):
            dados = auth

        if isinstance(dados, dict) and dados.get('refresh_token') and dados.get('access_token'):
            kwargs.setdefault('oauth_credentials', oauth_credentials())
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
        codigo = ft.Text('', size=18, weight=ft.FontWeight.BOLD, selectable=True)
        btn_vincular = ft.Button(content='Entrar com Google', icon=ft.Icons.LOGIN)
        btn_google = ft.Button(content='Abrir Google novamente', disabled=True)
        btn_copiar = ft.TextButton(content='Copiar código', disabled=True)
        estado = {'cancelar': threading.Event(), 'url': '', 'codigo': ''}

        def avisar(tipo, texto):
            status.value = texto
            status.color = {'ok': g['OK'], 'erro': g['ERRO'], 'aviso': g['AVISO']}.get(tipo, g['TEXTO2'])
            self.atualizar_status()

        def liberar_botao():
            btn_vincular.disabled = False
            btn_vincular.content = 'Entrar com Google'

        def mostrar_codigo(user_code, verification_url):
            codigo.value = f'Código: {user_code}'
            estado['codigo'] = user_code
            # O ytmusicapi também usa esse formato; normalmente o Google já abre
            # com o código preenchido. Se não preencher, o botão de copiar fica disponível.
            sep = '&' if '?' in verification_url else '?'
            estado['url'] = verification_url + sep + urllib.parse.urlencode({'user_code': user_code})
            btn_google.disabled = False
            btn_copiar.disabled = False
            avisar('aviso', 'Autorize sua conta no Google. Se ele pedir um código, ele já está copiado/visível aqui.')

        async def abrir_google(e):
            if estado.get('url'):
                await self.launcher.launch_url(estado['url'])

        async def copiar_codigo(e):
            if estado.get('codigo'):
                await self.clipboard.set(estado['codigo'])
                avisar('aviso', 'Código copiado. Cole na página do Google se ele pedir.')

        btn_google.on_click = abrir_google
        btn_copiar.on_click = copiar_codigo

        def vincular(e):
            if not client_id or not client_secret:
                avisar('erro', 'Este APK não contém as credenciais OAuth do aplicativo. Baixe novamente pela Release oficial.')
                return

            estado['cancelar'].set()
            estado['cancelar'] = threading.Event()
            cancelar = estado['cancelar']
            btn_vincular.disabled = True
            btn_vincular.content = 'Aguardando Google...'
            btn_google.disabled = True
            btn_copiar.disabled = True
            codigo.value = ''
            avisar('aviso', 'Gerando autorização segura do Google...')

            def trabalho():
                try:
                    resp = requests.post(
                        DEVICE_CODE_URL,
                        data={'client_id': client_id, 'scope': YOUTUBE_SCOPE},
                        timeout=30,
                    )
                    dados = resp.json()
                    if resp.status_code != 200 or dados.get('error'):
                        detalhe = dados.get('error_description') or dados.get('error') or resp.text[:200]
                        raise RuntimeError(f'O Google recusou o início do login: {detalhe}')

                    user_code = str(dados.get('user_code') or '').strip()
                    device_code = str(dados.get('device_code') or '').strip()
                    verification_url = str(
                        dados.get('verification_url') or dados.get('verification_uri') or 'https://www.google.com/device'
                    ).strip()
                    if not user_code or not device_code:
                        raise RuntimeError('O Google não devolveu um código de autorização válido.')

                    self.postar(mostrar_codigo, user_code, verification_url)

                    # Tenta deixar o código no clipboard antes de abrir o navegador.
                    try:
                        self.postar(lambda: None)
                    except Exception:
                        pass

                    sep = '&' if '?' in verification_url else '?'
                    url = verification_url + sep + urllib.parse.urlencode({'user_code': user_code})
                    self._abrir_url_de_thread(url)

                    intervalo = max(5, int(dados.get('interval') or 5))
                    limite = time.time() + max(60, int(dados.get('expires_in') or 1800))
                    token = None

                    while time.time() < limite:
                        if cancelar.wait(intervalo):
                            return

                        token_resp = requests.post(
                            TOKEN_URL,
                            data={
                                'client_id': client_id,
                                'client_secret': client_secret,
                                'device_code': device_code,
                                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                            },
                            timeout=30,
                        )
                        resposta = token_resp.json()
                        erro = resposta.get('error') if isinstance(resposta, dict) else None

                        if token_resp.status_code == 200 and resposta.get('access_token') and resposta.get('refresh_token'):
                            token = resposta
                            break
                        if erro == 'authorization_pending':
                            continue
                        if erro == 'slow_down':
                            intervalo += 5
                            continue
                        if erro == 'access_denied':
                            raise RuntimeError('A autorização foi cancelada no Google.')
                        if erro == 'expired_token':
                            raise RuntimeError('O código expirou. Toque em “Entrar com Google” e tente novamente.')
                        if erro:
                            detalhe = resposta.get('error_description') or erro
                            raise RuntimeError(f'O Google recusou o login: {detalhe}')

                    if token is None:
                        raise TimeoutError('O código expirou antes da autorização terminar. Tente novamente.')

                    expires_in = int(token.get('expires_in') or 3600)
                    token_limpo = {
                        'scope': str(token.get('scope') or YOUTUBE_SCOPE),
                        'token_type': str(token.get('token_type') or 'Bearer'),
                        'access_token': token['access_token'],
                        'refresh_token': token['refresh_token'],
                        'expires_in': expires_in,
                        'expires_at': int(time.time()) + expires_in,
                    }
                    nuc.gravar_json(nuc.YT_AUTH_PATH, token_limpo)

                    yt = ytmusic_real(nuc.YT_AUTH_PATH, oauth_credentials=oauth_credentials())
                    try:
                        info_conta = yt.get_account_info() or {}
                        nome = info_conta.get('accountName') or 'conta conectada'
                    except Exception:
                        yt.get_library_playlists(limit=1)
                        nome = 'conta conectada'

                    self.postar(avisar, 'ok', f'✓ YouTube Music vinculado: {nome}')
                    self.postar(setattr, codigo, 'value', '')
                    self.postar(setattr, btn_google, 'disabled', True)
                    self.postar(setattr, btn_copiar, 'disabled', True)
                    self.postar(liberar_botao)
                except Exception as ex:
                    nuc.registrar_erro_em_arquivo()
                    self.postar(avisar, 'erro', f'Não foi possível vincular: {ex}')
                    self.postar(liberar_botao)

            threading.Thread(target=trabalho, daemon=True).start()

        btn_vincular.on_click = vincular

        def desvincular(e):
            estado['cancelar'].set()
            if os.path.exists(nuc.YT_AUTH_PATH):
                try:
                    os.remove(nuc.YT_AUTH_PATH)
                except OSError:
                    pass
            codigo.value = ''
            btn_google.disabled = True
            btn_copiar.disabled = True
            avisar('aviso', 'YouTube Music desvinculado.')

        def fechar(e):
            estado['cancelar'].set()
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text('Vincular o YouTube Music'),
            content=ft.Column([
                ft.Text('Toque em “Entrar com Google”, escolha sua conta e autorize. Você não precisa configurar Google Cloud.', size=13),
                btn_vincular,
                codigo,
                ft.Row([btn_copiar, btn_google], wrap=True),
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
