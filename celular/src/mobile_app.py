# -*- coding: utf-8 -*-
"""Bootstrap da versão Android.

A autenticação do YouTube Music usa OAuth Device Flow direto no celular.
As credenciais OAuth do aplicativo são injetadas pelo GitHub Actions durante o build;
o usuário final só vê o botão "Entrar com Google".
"""
import os
import threading
import time
import urllib.parse

import flet as ft
from ytmusicapi import OAuthCredentials

try:
    from yt_oauth_config import YT_OAUTH_CLIENT_ID, YT_OAUTH_CLIENT_SECRET
except ImportError:
    # Em desenvolvimento local esse arquivo pode não existir. No APK oficial o
    # GitHub Actions gera o módulo antes de compilar.
    YT_OAUTH_CLIENT_ID = ''
    YT_OAUTH_CLIENT_SECRET = ''

_REAL_FLET_RUN = ft.run


def _patch_app(target):
    g = target.__globals__
    Tela = g.get('Tela')
    nuc = g.get('nuc')
    if Tela is None or nuc is None:
        return

    client_id = str(YT_OAUTH_CLIENT_ID or '').strip()
    client_secret = str(YT_OAUTH_CLIENT_SECRET or '').strip()
    ytmusic_real = nuc.YTMusic

    def credenciais_oauth():
        if not client_id or not client_secret:
            raise RuntimeError(
                'Este APK foi compilado sem as credenciais do YouTube Music. '
                'Baixe novamente a versão oficial pela página de Releases.'
            )
        return OAuthCredentials(client_id=client_id, client_secret=client_secret)

    def criar_ytmusic(auth=None, *args, **kwargs):
        """Faz o núcleo aceitar tanto cookies antigos quanto OAuth novo."""
        dados = None
        if isinstance(auth, str) and os.path.exists(auth):
            dados = nuc.ler_json(auth, {}) or {}
        elif isinstance(auth, dict):
            dados = auth

        if isinstance(dados, dict) and dados.get('refresh_token') and dados.get('access_token'):
            kwargs.setdefault('oauth_credentials', credenciais_oauth())
        return ytmusic_real(auth, *args, **kwargs)

    # O núcleo instancia YTMusic em vários pontos. Esse wrapper garante que o
    # refresh token OAuth continue funcionando durante toda a migração.
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
        codigo = ft.Text('', size=20, weight=ft.FontWeight.BOLD, selectable=True)
        status = ft.Text('', size=12)
        btn_google = ft.Button(content='Abrir página do Google', disabled=True)
        btn_vincular = ft.Button(content='Entrar com Google', icon=ft.Icons.LOGIN)
        estado = {'cancelar': threading.Event(), 'url': ''}

        def avisar(tipo, texto):
            status.value = texto
            status.color = {'ok': g['OK'], 'erro': g['ERRO'], 'aviso': g['AVISO']}.get(tipo, g['TEXTO2'])
            self.atualizar_status()

        def liberar_botao():
            btn_vincular.disabled = False
            btn_vincular.content = 'Entrar com Google'

        def mostrar_codigo(user_code, url):
            codigo.value = f'Código: {user_code}'
            estado['url'] = url
            btn_google.disabled = False
            avisar('aviso', 'Autorize sua conta no navegador. O app detecta sozinho quando terminar.')

        async def abrir_google(e):
            if estado.get('url'):
                await self.launcher.launch_url(estado['url'])

        btn_google.on_click = abrir_google

        def vincular(e):
            if not client_id or not client_secret:
                avisar('erro', 'Este APK não contém a configuração OAuth do aplicativo. Baixe novamente pela Release oficial.')
                return

            estado['cancelar'].set()
            estado['cancelar'] = threading.Event()
            cancelar = estado['cancelar']
            btn_vincular.disabled = True
            btn_vincular.content = 'Abrindo Google...'
            codigo.value = ''
            btn_google.disabled = True
            avisar('aviso', 'Preparando o login com Google...')

            def trabalho():
                try:
                    cred = credenciais_oauth()
                    info = cred.get_code()
                    if info.get('error'):
                        raise RuntimeError(str(info.get('error_description') or info['error']))

                    user_code = str(info.get('user_code', '')).strip()
                    device_code = str(info.get('device_code', '')).strip()
                    base_url = str(info.get('verification_url') or 'https://www.google.com/device').strip()
                    if not user_code or not device_code:
                        raise RuntimeError('O Google não devolveu um código de autorização válido.')

                    if 'user_code=' not in base_url:
                        sep = '&' if '?' in base_url else '?'
                        url = base_url + sep + urllib.parse.urlencode({'user_code': user_code})
                    else:
                        url = base_url

                    self.postar(mostrar_codigo, user_code, url)
                    self._abrir_url_de_thread(url)

                    intervalo = max(5, int(info.get('interval') or 5))
                    limite = time.time() + max(60, int(info.get('expires_in') or 600))
                    token = None
                    while time.time() < limite:
                        if cancelar.wait(intervalo):
                            return
                        resposta = cred.token_from_code(device_code)
                        erro = resposta.get('error') if isinstance(resposta, dict) else None
                        if not erro and resposta.get('access_token') and resposta.get('refresh_token'):
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
                            raise RuntimeError('O código expirou. Toque em “Entrar com Google” e tente de novo.')
                        if erro:
                            raise RuntimeError(str(resposta.get('error_description') or erro))

                    if token is None:
                        raise TimeoutError('O código expirou antes da autorização terminar. Tente de novo.')

                    permitidos = {
                        'scope', 'token_type', 'access_token', 'refresh_token',
                        'expires_at', 'expires_in',
                    }
                    token_limpo = {k: v for k, v in token.items() if k in permitidos}
                    token_limpo.setdefault('scope', 'https://www.googleapis.com/auth/youtube')
                    token_limpo.setdefault('token_type', 'Bearer')
                    token_limpo['expires_at'] = int(time.time()) + int(token_limpo.get('expires_in') or 3600)
                    nuc.gravar_json(nuc.YT_AUTH_PATH, token_limpo)

                    yt = ytmusic_real(nuc.YT_AUTH_PATH, oauth_credentials=credenciais_oauth())
                    try:
                        info_conta = yt.get_account_info() or {}
                        nome = info_conta.get('accountName') or 'conta conectada'
                    except Exception:
                        yt.get_library_playlists(limit=1)
                        nome = 'conta conectada'

                    self.postar(avisar, 'ok', f'✓ YouTube Music vinculado: {nome}')
                    self.postar(setattr, codigo, 'value', '')
                    self.postar(setattr, btn_google, 'disabled', True)
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
            avisar('aviso', 'YouTube Music desvinculado.')

        def fechar(e):
            estado['cancelar'].set()
            self.page.pop_dialog()

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text('Vincular o YouTube Music'),
            content=ft.Column([
                ft.Text('Toque em “Entrar com Google”, escolha sua conta e autorize o acesso. Só isso.', size=13),
                btn_vincular,
                codigo,
                btn_google,
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


# main.py chama ft.run(main) no final. Interceptamos essa chamada, aplicamos o
# patch acima e só então iniciamos o Flet normalmente.
ft.run = _patched_run
import main  # noqa: E402,F401
