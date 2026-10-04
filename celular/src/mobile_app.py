# -*- coding: utf-8 -*-
"""Bootstrap da versão Android.

Mantém a interface principal em main.py, mas troca o fluxo de autenticação do
YouTube Music por OAuth Device Flow direto no celular. O método antigo de
importar a sessão do computador continua disponível apenas como plano B.
"""
import os
import threading
import time
import urllib.parse

import flet as ft
from ytmusicapi import OAuthCredentials

_REAL_FLET_RUN = ft.run


def _patch_app(target):
    g = target.__globals__
    Tela = g.get('Tela')
    nuc = g.get('nuc')
    if Tela is None or nuc is None:
        return

    # O Google passou a exigir Client ID + Client Secret próprios para o OAuth
    # do ytmusicapi. Guardamos isso só na pasta privada do app.
    oauth_cfg_path = os.path.join(nuc.DATA_DIR, 'ytmusic_oauth_client.json')
    ytmusic_real = nuc.YTMusic

    def ler_cfg_oauth():
        cfg = nuc.ler_json(oauth_cfg_path, {}) or {}
        return {
            'client_id': str(cfg.get('client_id', '')).strip(),
            'client_secret': str(cfg.get('client_secret', '')).strip(),
        }

    def criar_ytmusic(auth=None, *args, **kwargs):
        """Faz o núcleo aceitar tanto cookies antigos quanto OAuth novo."""
        dados = None
        if isinstance(auth, str) and os.path.exists(auth):
            dados = nuc.ler_json(auth, {}) or {}
        elif isinstance(auth, dict):
            dados = auth

        if isinstance(dados, dict) and dados.get('refresh_token') and dados.get('access_token'):
            cfg = ler_cfg_oauth()
            if not cfg['client_id'] or not cfg['client_secret']:
                raise RuntimeError(
                    'A sessão do YouTube Music é OAuth, mas as credenciais do aplicativo sumiram. '
                    'Abra “Vincular / gerenciar” e vincule novamente.'
                )
            kwargs.setdefault(
                'oauth_credentials',
                OAuthCredentials(client_id=cfg['client_id'], client_secret=cfg['client_secret']),
            )
        return ytmusic_real(auth, *args, **kwargs)

    # O núcleo consulta YTMusic pelo próprio global, então esta troca também vale
    # para criar playlist, adicionar músicas, ler biblioteca e renovar o token.
    nuc.YTMusic = criar_ytmusic

    def trocar_rotulo_importar(page):
        """Troca o texto do botão antigo sem duplicar a tela inteira de main.py."""
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
                controles = getattr(obj, 'controls', None) or []
                for c in controles:
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
        cfg = ler_cfg_oauth()
        campo_id = ft.TextField(
            label='Google OAuth Client ID',
            value=cfg['client_id'],
            hint_text='...apps.googleusercontent.com',
        )
        campo_secret = ft.TextField(
            label='Google OAuth Client Secret',
            value=cfg['client_secret'],
            password=True,
            can_reveal_password=True,
        )
        codigo = ft.Text('', size=20, weight=ft.FontWeight.BOLD, selectable=True)
        status = ft.Text('', size=12)
        btn_google = ft.Button(content='Abrir página do Google', disabled=True)
        btn_vincular = ft.Button(content='Entrar com Google', icon=ft.Icons.LOGIN)
        estado = {'cancelar': threading.Event(), 'url': ''}

        # Plano B antigo: ainda útil se alguém já tiver uma sessão exportada.
        campo_importar = ft.TextField(
            label='Plano B: sessão copiada do computador',
            multiline=True,
            min_lines=2,
            max_lines=4,
        )

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
            avisar('aviso', 'Autorize sua conta no navegador. O app vai detectar sozinho quando terminar.')

        async def abrir_google(e):
            if estado.get('url'):
                await self.launcher.launch_url(estado['url'])

        btn_google.on_click = abrir_google

        async def abrir_cloud(e):
            await self.launcher.launch_url('https://console.cloud.google.com/apis/credentials')

        def vincular(e):
            client_id = (campo_id.value or '').strip()
            client_secret = (campo_secret.value or '').strip()
            if not client_id or not client_secret:
                avisar('erro', 'Preencha o Client ID e o Client Secret do Google.')
                return
            if 'apps.googleusercontent.com' not in client_id:
                avisar('erro', 'Esse Client ID não parece ser um OAuth Client ID do Google.')
                return

            estado['cancelar'].set()
            estado['cancelar'] = threading.Event()
            cancelar = estado['cancelar']
            btn_vincular.disabled = True
            btn_vincular.content = 'Gerando código...'
            codigo.value = ''
            btn_google.disabled = True
            avisar('aviso', 'Gerando um código seguro de autorização...')

            def trabalho():
                try:
                    cred = OAuthCredentials(client_id=client_id, client_secret=client_secret)
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

                    # Só guarda as credenciais depois que o Google aceitou o cliente OAuth.
                    nuc.gravar_json(oauth_cfg_path, {
                        'client_id': client_id,
                        'client_secret': client_secret,
                    })
                    self.postar(mostrar_codigo, user_code, url)

                    # Abre automaticamente no navegador do próprio celular.
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

                    # Remove campos extras que versões antigas do ytmusicapi não reconhecem.
                    permitidos = {
                        'scope', 'token_type', 'access_token', 'refresh_token',
                        'expires_at', 'expires_in',
                    }
                    token_limpo = {k: v for k, v in token.items() if k in permitidos}
                    token_limpo.setdefault('scope', 'https://www.googleapis.com/auth/youtube')
                    token_limpo.setdefault('token_type', 'Bearer')
                    token_limpo['expires_at'] = int(time.time()) + int(token_limpo.get('expires_in') or 3600)
                    nuc.gravar_json(nuc.YT_AUTH_PATH, token_limpo)

                    # Faz uma chamada real antes de dizer que terminou.
                    yt = ytmusic_real(
                        nuc.YT_AUTH_PATH,
                        oauth_credentials=OAuthCredentials(client_id=client_id, client_secret=client_secret),
                    )
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

        async def colar_antigo(e):
            campo_importar.value = (await self.clipboard.get()) or ''

        def importar_antigo(e):
            texto = campo_importar.value or ''
            avisar('aviso', 'Testando a sessão importada...')

            def trabalho():
                try:
                    msgs = nuc.importar_vinculacao(texto)
                    self.postar(avisar, 'ok', ' '.join(msgs))
                    self.postar(setattr, campo_importar, 'value', '')
                except Exception as ex:
                    nuc.registrar_erro_em_arquivo()
                    msg = nuc.explicar_erro(ex) if not isinstance(ex, ValueError) else str(ex)
                    self.postar(avisar, 'erro', msg)

            threading.Thread(target=trabalho, daemon=True).start()

        def desvincular(e):
            estado['cancelar'].set()
            if os.path.exists(nuc.YT_AUTH_PATH):
                try:
                    os.remove(nuc.YT_AUTH_PATH)
                except OSError:
                    pass
            codigo.value = ''
            btn_google.disabled = True
            avisar('aviso', 'YouTube Music desvinculado. As credenciais do app ficaram salvas para facilitar o próximo login.')

        def fechar(e):
            estado['cancelar'].set()
            self.page.pop_dialog()

        instrucoes = (
            'Login direto no celular. Na primeira vez, use um OAuth Client do Google do tipo '
            '“TVs and Limited Input devices”, com a YouTube Data API v3 ativada. Depois disso, '
            'o app lembra essas duas credenciais e o login vira só “Entrar com Google”.'
        )

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text('Vincular o YouTube Music'),
            content=ft.Column([
                ft.Text(instrucoes, size=12),
                ft.TextButton(content='Abrir credenciais no Google Cloud', on_click=abrir_cloud),
                campo_id,
                campo_secret,
                btn_vincular,
                codigo,
                btn_google,
                status,
                ft.Divider(),
                ft.Text('Plano B (antigo)', weight=ft.FontWeight.BOLD, size=12),
                ft.Text('Se preferir, ainda dá para importar a sessão copiada do computador.', size=11, color=g['TEXTO2']),
                campo_importar,
                ft.Row([
                    ft.TextButton(content='Colar', on_click=colar_antigo),
                    ft.Button(content='Validar sessão antiga', on_click=importar_antigo),
                ], wrap=True),
            ], tight=True, spacing=9, scroll=ft.ScrollMode.AUTO),
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
