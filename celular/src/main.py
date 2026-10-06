# -*- coding: utf-8 -*-
"""
RHYFT (celular): Spotify <-> YouTube Music.

Interface em Flet 1.x. TODA a lógica (login, validação, lotes conferidos, retomada) vive em
nucleo.py, o mesmo arquivo usado pelo app do computador.

Regra do Flet 1.x: nada que bloqueie pode rodar na thread da tela. A migração roda numa thread
própria e fala com a tela por uma fila (self.fila), esvaziada por uma tarefa da própria tela.
"""
from rhyft_i18n import tr
import asyncio
import glob
import os
import queue
import re
import shutil
import threading

import flet as ft

# Pasta privada do app. Precisa ser definida ANTES de importar o núcleo.
_storage_base = os.environ.get('FLET_APP_STORAGE_DATA') or os.path.expanduser('~')
_data_nova = os.path.join(_storage_base, 'RHYFT')
_data_antiga = os.path.join(_storage_base, 'MigradorPlaylists')
if not os.path.exists(_data_nova) and os.path.isdir(_data_antiga):
    try:
        shutil.copytree(_data_antiga, _data_nova)
    except OSError:
        pass
os.environ.setdefault('MIGRADOR_DATA_DIR', _data_nova)
import nucleo as nuc  # noqa: E402

FUNDO, CARTAO, BORDA = '#0D0E16', '#151726', '#2A2E48'
TEXTO, TEXTO2 = '#EDEFF7', '#9BA1C2'
TEAL, OK, AVISO, ERRO = '#2DD4BF', '#34D399', '#F5B544', '#F87171'
COR_LOG = {'normal': TEXTO, 'sucesso': OK, 'erro': ERRO, 'aviso': AVISO, 'info': TEAL, 'cinza': TEXTO2}
MAX_LINHAS_LOG = 400


class MotorCelular(nuc.MotorMigracao):
    """Liga o núcleo à tela: tudo que o núcleo pede passa pela fila e roda na thread da tela."""

    def __init__(self, tela):
        self.tela = tela

    def ui(self, fn, *args):
        self.tela.fila.put((fn, args))

    def log(self, mensagem, tipo='normal'):
        self.ui(self.tela.add_log, mensagem, tipo)

    def _ui_progresso(self, feitos, total, detalhe='', fase='faixas'):
        if fase == 'aprovacao':
            titulo = f'Aprovação manual: {min(feitos + 1, total)} de {total}'
        else:
            titulo = f'Item {min(feitos + 1, total)} de {total}'
        self.tela.set_progresso(titulo, detalhe, feitos / total if total else 0.0)

    def _ui_progresso_status(self, titulo, detalhe=''):
        self.tela.set_progresso(titulo, detalhe, None)

    def _ui_progresso_fim(self, titulo, detalhe=''):
        self.tela.set_progresso(titulo, detalhe, 1.0)

    def _ui_progresso_reset(self, titulo='Pronto para começar',
                            detalhe='Cole o link da playlist e toque em Iniciar migração.'):
        self.tela.set_progresso(titulo, detalhe, 0.0)

    def _ui_migracao_terminada(self, controle):
        self.tela.migracao_terminada(controle)

    def _ui_travar_controles(self):
        self.tela.btn_pausar.disabled = True
        self.tela.btn_cancelar.disabled = True

    def escolher_versao(self, n, total, faixa, opcoes, origem='Spotify'):
        """Chamado pela thread da migração: mostra as opções e ESPERA a escolha."""
        evento, resposta = threading.Event(), {}
        self.ui(self.tela.mostrar_escolha, n, total, faixa, opcoes, origem, resposta, evento)
        evento.wait()
        return resposta.get('v')


class Tela:
    def __init__(self, page):
        self.page = page
        self.fila = queue.Queue()
        self.motor = MotorCelular(self)
        self.controle = None
        self.modo = 'sp_yt'
        self.launcher = ft.UrlLauncher()
        self.clipboard = ft.Clipboard()
        nuc.abrir_url_externa = self._abrir_url_de_thread   # usado no login do Spotify

    # ---------------------------------------------------------------- utilidades
    def postar(self, fn, *args):
        self.fila.put((fn, args))

    def drenar(self):
        """Executa (na thread da tela) tudo que as outras threads pediram. True se fez algo."""
        fez = False
        while True:
            try:
                fn, args = self.fila.get_nowait()
            except queue.Empty:
                return fez
            fez = True
            try:
                fn(*args)
            except Exception:
                nuc.registrar_erro_em_arquivo()

    async def poller(self):
        while True:
            if self.drenar():
                self.page.update()
            await asyncio.sleep(0.15)

    def _abrir_url_de_thread(self, url):
        try:
            fut = asyncio.run_coroutine_threadsafe(self.launcher.launch_url(url), self.page.loop)
            fut.result(timeout=10)
            return True
        except Exception:
            nuc.registrar_erro_em_arquivo()
            return False

    def mensagem(self, titulo, texto):
        def fechar(e):
            self.page.pop_dialog()
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(titulo), content=ft.Text(texto),
            actions=[ft.TextButton(content='OK', on_click=fechar)]))

    def confirmar(self, titulo, texto, ao_confirmar):
        def sim(e):
            self.page.pop_dialog()
            ao_confirmar()

        def nao(e):
            self.page.pop_dialog()
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(titulo), content=ft.Text(texto),
            actions=[ft.TextButton(content=tr('Não'), on_click=nao), ft.Button(content=tr('Sim'), on_click=sim)]))

    # ---------------------------------------------------------------- tela
    def montar(self):
        p = self.page
        p.title = 'RHYFT'
        p.theme_mode = ft.ThemeMode.DARK
        p.bgcolor = FUNDO
        p.padding = 0

        self.lbl_sp = ft.Text('', size=12)
        self.lbl_yt = ft.Text('', size=12)
        self.sw_update = ft.Switch(label=tr('Buscar atualizações ao abrir'), value=nuc.checar_atualizacoes_habilitadas(),
                                   on_change=self.alternar_atualizacao)
        self.btn_update = ft.Button(content=tr('Verificar agora'), icon=ft.Icons.SYSTEM_UPDATE,
                                    on_click=self.verificar_agora)
        self.seg = ft.SegmentedButton(
            selected=['sp_yt'], allow_multiple_selection=False, on_change=self.trocar_modo,
            segments=[ft.Segment(value='sp_yt', label=ft.Text('Spotify ➔ YT Music')),
                      ft.Segment(value='yt_sp', label=ft.Text('YT Music ➔ Spotify'))])
        self.campo_origem = ft.TextField(label=tr('Link ou ID da playlist do Spotify'),
                                         hint_text='https://open.spotify.com/playlist/...')
        self.campo_destino = ft.TextField(label=tr('Nome da nova playlist no YouTube Music'),
                                          hint_text=tr('Minha Playlist Importada'))
        self.btn_iniciar = ft.Button(content=tr('Iniciar migração'), icon=ft.Icons.PLAY_ARROW, on_click=self.iniciar)
        self.btn_pausar = ft.Button(content=tr('Pausar'), icon=ft.Icons.PAUSE, on_click=self.pausar, disabled=True)
        self.btn_cancelar = ft.Button(content=tr('Cancelar'), icon=ft.Icons.STOP, on_click=self.cancelar, disabled=True)
        self.lbl_prog_titulo = ft.Text(tr('Pronto para começar'), size=15, weight=ft.FontWeight.BOLD)
        self.lbl_prog_detalhe = ft.Text(tr('Cole o link da playlist e toque em Iniciar migração.'),
                                        size=12, color=TEXTO2)
        self.barra = ft.ProgressBar(value=0.0, color=TEAL, bgcolor=BORDA)
        self.lista_log = ft.ListView(controls=[], spacing=2, auto_scroll=True, expand=True)

        conteudo = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, controls=[
            ft.Row([ft.Text('RHYFT', size=22, weight=ft.FontWeight.BOLD),
                    ft.Text(f'v{nuc.APP_VERSION}', size=12, color=TEXTO2)],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Text(tr('Passe suas playlists entre o Spotify e o YouTube Music'), size=12, color=TEXTO2),
            ft.Row([self.sw_update, self.btn_update], wrap=True),
            self.cartao(ft.Text('1. Spotify', weight=ft.FontWeight.BOLD), self.lbl_sp,
                        ft.Button(content=tr('Vincular / gerenciar'), on_click=self.abrir_spotify)),
            self.cartao(ft.Text('2. YouTube Music', weight=ft.FontWeight.BOLD), self.lbl_yt,
                        ft.Button(content=tr('Importar do computador'), on_click=self.abrir_yt)),
            self.cartao(self.seg, self.campo_origem, self.campo_destino,
                        ft.Row([self.btn_iniciar, self.btn_pausar], wrap=True),
                        ft.Row([self.btn_cancelar,
                                ft.TextButton(content=tr('Histórico'), icon=ft.Icons.HISTORY,
                                              on_click=self.abrir_historico)], wrap=True)),
            self.cartao(self.lbl_prog_titulo, self.barra, self.lbl_prog_detalhe),
            ft.Container(content=self.lista_log, height=300, padding=8, border_radius=12,
                         bgcolor='#0A0B12', border=ft.Border.all(1, color=BORDA)),
            ft.Text(tr('Feito por deadbynetsu'), size=11, color=TEXTO2, text_align=ft.TextAlign.CENTER),
        ])
        p.add(ft.SafeArea(expand=True, content=ft.Container(content=conteudo, padding=14, expand=True)))
        self.atualizar_status()

    def cartao(self, *controles):
        return ft.Container(content=ft.Column(list(controles), spacing=10), padding=14,
                            border_radius=14, bgcolor=CARTAO, border=ft.Border.all(1, color=BORDA))

    def atualizar_status(self):
        auth = nuc.carregar_auth_spotify()
        if auth and auth.vinculado():
            if auth.tem_escrita():
                self.lbl_sp.value, self.lbl_sp.color = '✓ Spotify vinculado', OK
            else:
                self.lbl_sp.value = '⚠ Vinculado, mas sem permissão para criar playlists. Autorize de novo.'
                self.lbl_sp.color = AVISO
        else:
            self.lbl_sp.value, self.lbl_sp.color = '⚠ Spotify ainda não vinculado', AVISO
        if os.path.exists(nuc.YT_AUTH_PATH):
            self.lbl_yt.value, self.lbl_yt.color = '✓ YouTube Music vinculado', OK
        else:
            self.lbl_yt.value, self.lbl_yt.color = '⚠ YouTube Music ainda não vinculado', AVISO

    def add_log(self, mensagem, tipo='normal'):
        self.lista_log.controls.append(
            ft.Text(mensagem, size=12, color=COR_LOG.get(tipo, TEXTO), selectable=True))
        if len(self.lista_log.controls) > MAX_LINHAS_LOG:
            del self.lista_log.controls[:100]

    def limpar_log(self):
        self.lista_log.controls.clear()

    def set_progresso(self, titulo, detalhe, pct):
        self.lbl_prog_titulo.value = tr(titulo)
        self.lbl_prog_detalhe.value = nuc._cortar(tr(detalhe or ''), 90)
        if pct is not None:
            self.barra.value = max(0.0, min(1.0, pct))

    # ---------------------------------------------------------------- migração
    def trocar_modo(self, e):
        sel = list(self.seg.selected or [])
        self.modo = sel[0] if sel else 'sp_yt'
        reverso = self.modo == 'yt_sp'
        self.campo_origem.label = (tr('Link ou ID da playlist do YouTube Music') if reverso
                                   else tr('Link ou ID da playlist do Spotify'))
        self.campo_origem.hint_text = ('https://music.youtube.com/playlist?list=...' if reverso
                                       else 'https://open.spotify.com/playlist/...')
        self.campo_destino.label = (tr('Nome da nova playlist no Spotify (vazio = mesmo nome)') if reverso
                                    else tr('Nome da nova playlist no YouTube Music'))
        self.campo_origem.value = ''
        self.campo_destino.value = ''
        self.motor._ui_progresso_reset()

    def iniciar(self, e):
        origem = (self.campo_origem.value or '').strip()
        destino = (self.campo_destino.value or '').strip()
        reverso = self.modo == 'yt_sp'
        if self.controle is not None:
            return
        if not origem:
            self.mensagem(tr('Aviso'), tr('Cole o link da playlist de origem.'))
            return
        if not nuc.spotify_vinculado():
            self.mensagem('Spotify não vinculado', 'Toque em "Vincular / gerenciar" no cartão do Spotify.')
            return
        if not os.path.exists(nuc.YT_AUTH_PATH):
            self.mensagem('YouTube Music não vinculado', 'Toque em "Importar do computador" no cartão do YouTube Music.')
            return
        if reverso:
            auth = nuc.carregar_auth_spotify()
            if not (auth and auth.tem_escrita()):
                self.mensagem('Permissão nova do Spotify', nuc.MSG_REAUTORIZAR)
                return
        self.controle = nuc.ControleMigracao()
        self.btn_iniciar.disabled, self.btn_iniciar.content = True, 'Migrando...'
        self.btn_pausar.disabled = self.btn_cancelar.disabled = False
        self.btn_pausar.content = tr('Pausar')
        self.seg.disabled = True
        self.limpar_log()
        self.motor._ui_progresso_reset('Conectando...', 'Lendo as músicas da playlist.')
        alvo = self.motor.processo_migracao_reversa if reverso else self.motor.processo_migracao
        threading.Thread(target=alvo, args=(origem, destino, self.controle), daemon=True).start()

    def migracao_terminada(self, controle):
        if self.controle is controle:
            self.controle = None
        self.btn_iniciar.disabled, self.btn_iniciar.content = False, tr('Iniciar migração')
        self.btn_pausar.disabled = self.btn_cancelar.disabled = True
        self.btn_pausar.content, self.btn_cancelar.content = tr('Pausar'), tr('Cancelar')
        self.seg.disabled = False

    def pausar(self, e):
        if self.controle is None:
            return
        self.controle.pausar()
        self.btn_pausar.disabled, self.btn_pausar.content = True, 'Pausando...'
        self.add_log('⏸️ Pausa pedida: termino a música atual e paro em seguida...', 'aviso')

    def cancelar(self, e):
        controle = self.controle
        if controle is None:
            return

        def confirmado():
            if self.controle is not controle:
                return
            controle.cancelar()
            self.btn_pausar.disabled, self.btn_cancelar.disabled = True, True
            self.btn_cancelar.content = 'Cancelando...'
            self.add_log('🛑 Cancelamento pedido: paro assim que possível...', 'aviso')
        self.confirmar(tr('Cancelar migração'),
                       'O progresso será APAGADO (não dá para retomar). A playlist já criada no destino '
                       'NÃO é apagada. Cancelar mesmo?', confirmado)

    def mostrar_escolha(self, n, total, faixa, opcoes, origem, resposta, evento):
        def fechar(escolhida):
            resposta['v'] = escolhida
            self.page.pop_dialog()
            evento.set()

        botoes = [ft.Button(content=ft.Text(f'{nuc._cortar(c["yt_title"], 46)}  —  {nuc._cortar(c["yt_artist"] or "?", 28)}',
                                            size=13),
                            on_click=lambda e, c=c: fechar(c)) for c in opcoes]
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(f'Qual versão adicionar? ({n} de {total})'),
            content=ft.Column([ft.Text(f'No {origem}: {nuc._cortar(faixa, 62)}', size=13,
                                       weight=ft.FontWeight.BOLD)] + botoes, tight=True, spacing=8,
                              scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton(content=tr('Nenhuma dessas (pular)'), on_click=lambda e: fechar(None))]))

    # ---------------------------------------------------------------- atualizações
    def alternar_atualizacao(self, e):
        nuc.salvar_config_atualizacao(bool(self.sw_update.value))
        self.add_log('🔄 Checagem automática de atualizações ATIVADA.' if self.sw_update.value
                     else '⏹️ Checagem automática de atualizações DESATIVADA.',
                     'info' if self.sw_update.value else 'aviso')

    def verificar_agora(self, e):
        if self.btn_update.disabled:
            return
        self.btn_update.disabled, self.btn_update.content = True, tr('Verificando...')
        self.checar_atualizacao(manual=True)

    def checar_atualizacao(self, manual):
        """Consulta o GitHub em segundo plano; o resultado volta pela fila da tela."""
        def trabalho():
            try:
                info = nuc.buscar_ultima_versao(nuc.GITHUB_REPO)
                self.postar(self.resultado_atualizacao, manual,
                            nuc.versao_mais_nova(info['tag'], nuc.APP_VERSION), info, None)
            except Exception as ex:
                nuc.registrar_erro_em_arquivo()
                self.postar(self.resultado_atualizacao, manual, False, None, str(ex))
        threading.Thread(target=trabalho, daemon=True).start()

    def resultado_atualizacao(self, manual, ha_nova, info, erro):
        if manual:
            self.btn_update.disabled, self.btn_update.content = False, tr('Verificar agora')
        if erro:
            if manual:   # na checagem automática o erro fica só no erros.log
                self.mensagem(tr('Atualizações'), erro)
            return
        if ha_nova:
            self.mostrar_atualizacao(info)
        elif manual:
            self.mensagem(tr('Atualizações'), f'Você já está na versão mais recente (v{nuc.APP_VERSION}).')

    def mostrar_atualizacao(self, info):
        async def baixar(e):
            self.page.pop_dialog()
            await self.launcher.launch_url(info['url'])

        def depois(e):
            self.page.pop_dialog()
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text('🎉 Nova versão disponível!'),
            content=ft.Column([
                ft.Text(f'Você está na v{nuc.APP_VERSION}. A versão {info["tag"]} já está disponível!',
                        weight=ft.FontWeight.BOLD, size=13),
                ft.Text('Toque em baixar, pegue o arquivo .apk na página da versão e instale por cima.',
                        size=12, color=TEXTO2),
                ft.Text(info['notas'] or 'Sem detalhes adicionais.', size=12)],
                tight=True, spacing=10, scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton(content=tr('Depois'), on_click=depois),
                     ft.Button(content=tr('Baixar atualização'), on_click=baixar)]))

    # ---------------------------------------------------------------- Spotify
    def abrir_spotify(self, e):
        cfg = nuc.ler_json(nuc.SPOTIFY_CONFIG_PATH, {}) or {}
        campo_id = ft.TextField(label='Client ID (32 caracteres)', value=cfg.get('client_id', ''))
        campo_url = ft.TextField(label='Plano B: endereço da barra do navegador', multiline=True,
                                 min_lines=2, max_lines=3, hint_text='http://127.0.0.1:8080/?code=...')
        status = ft.Text('', size=12)
        estado = {}

        def avisar(tipo, texto):
            status.value = texto
            status.color = {'ok': OK, 'erro': ERRO, 'aviso': AVISO}.get(tipo, TEXTO2)
            self.atualizar_status()

        async def abrir_painel(e):
            await self.launcher.launch_url('https://developer.spotify.com/dashboard')

        def vincular(e):
            cid = (campo_id.value or '').strip()
            if not re.fullmatch(r'[0-9a-fA-F]{32}', cid):
                avisar('erro', 'O Client ID tem 32 letras/números (copie de novo no painel do Spotify).')
                return
            if estado.get('ev'):
                estado['ev'].set()
            nuc.gravar_json(nuc.SPOTIFY_CONFIG_PATH, {'client_id': cid})
            if os.path.exists(nuc.SPOTIFY_TOKEN_PATH):
                os.remove(nuc.SPOTIFY_TOKEN_PATH)
            auth = nuc.SpotifyPKCE(cid, nuc.SPOTIFY_TOKEN_PATH)
            estado.update(auth=auth, p=auth.preparar_autorizacao(), ev=threading.Event())
            avisar('aviso', 'Abrindo o navegador... aceite no Spotify e volte para cá.')

            def trabalho(auth=auth, p=estado['p'], ev=estado['ev']):
                try:
                    auth.autorizar(cancelar=ev, preparado=p)
                    self.postar(avisar, 'ok', 'Spotify vinculado!')
                except InterruptedError:
                    pass
                except Exception as ex:
                    nuc.registrar_erro_em_arquivo()
                    self.postar(avisar, 'erro', f'{ex}\nSe o navegador mostrou erro de conexão, use o plano B abaixo.')
            threading.Thread(target=trabalho, daemon=True).start()

        def concluir_manual(e):
            if not estado.get('p'):
                avisar('erro', 'Toque primeiro em "Vincular e autorizar".')
                return
            try:
                codigo = nuc.SpotifyPKCE.extrair_codigo(campo_url.value, estado['p']['state'])
            except Exception as ex:
                avisar('erro', str(ex))
                return

            def trabalho(auth=estado['auth'], p=estado['p'], ev=estado['ev']):
                try:
                    auth.trocar_codigo(codigo, p['verifier'])
                    ev.set()
                    self.postar(avisar, 'ok', 'Spotify vinculado!')
                except Exception as ex:
                    nuc.registrar_erro_em_arquivo()
                    self.postar(avisar, 'erro', str(ex))
            threading.Thread(target=trabalho, daemon=True).start()

        def desvincular(e):
            for caminho in (nuc.SPOTIFY_TOKEN_PATH,):
                if os.path.exists(caminho):
                    os.remove(caminho)
            avisar('aviso', 'Spotify desvinculado.')

        def fechar(e):
            if estado.get('ev'):
                estado['ev'].set()
            self.page.pop_dialog()

        passos = ('1) No painel do Spotify para desenvolvedores (precisa de conta Premium), crie um app com o '
                  'Redirect URI  http://127.0.0.1:8080  e marque "Web API".\n'
                  '2) Copie o Client ID e cole abaixo.\n'
                  '3) Toque em "Vincular e autorizar", aceite no navegador e volte para o app. Se o navegador '
                  'mostrar erro de conexão, copie o endereço da barra e cole no plano B.')
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text('Vincular o Spotify'),
            content=ft.Column([ft.Text(passos, size=12), ft.TextButton(content=tr('Abrir painel do Spotify'), on_click=abrir_painel),
                               campo_id, ft.Button(content=tr('Vincular e autorizar'), on_click=vincular),
                               campo_url, ft.Button(content='Concluir (plano B)', on_click=concluir_manual),
                               status], tight=True, spacing=10, scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton(content=tr('Desvincular'), on_click=desvincular),
                     ft.TextButton(content=tr('Fechar'), on_click=fechar)]))

    # ---------------------------------------------------------------- YouTube Music
    def abrir_yt(self, e):
        campo = ft.TextField(label='Texto copiado do computador', multiline=True, min_lines=4, max_lines=6)
        status = ft.Text('', size=12)

        def avisar(tipo, texto):
            status.value = texto
            status.color = {'ok': OK, 'erro': ERRO, 'aviso': AVISO}.get(tipo, TEXTO2)
            self.atualizar_status()

        async def colar(e):
            campo.value = (await self.clipboard.get()) or ''

        def salvar(e):
            texto = campo.value or ''
            avisar('aviso', 'Testando a conexão com o YouTube Music...')

            def trabalho():
                try:
                    msgs = nuc.importar_vinculacao(texto)
                    self.postar(avisar, 'ok', ' '.join(msgs))
                    self.postar(setattr, campo, 'value', '')
                except Exception as ex:
                    nuc.registrar_erro_em_arquivo()
                    self.postar(avisar, 'erro', nuc.explicar_erro(ex) if not isinstance(ex, ValueError) else str(ex))
            threading.Thread(target=trabalho, daemon=True).start()

        def desvincular(e):
            if os.path.exists(nuc.YT_AUTH_PATH):
                os.remove(nuc.YT_AUTH_PATH)
            avisar('aviso', 'YouTube Music desvinculado.')

        def fechar(e):
            self.page.pop_dialog()

        passos = ('No computador: abra o RHYFT (v1.4 ou mais nova) > Vincular o YouTube Music > '
                  '"Copiar para o celular". Envie o texto para você mesmo (ex.: Mensagens salvas) e cole aqui. '
                  'Ele contém seu login do Google: trate como senha e apague a mensagem depois.')
        self.page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(tr('Importar do computador')),
            content=ft.Column([ft.Text(passos, size=12), campo,
                               ft.Row([ft.TextButton(content=tr('Colar'), on_click=colar),
                                       ft.Button(content=tr('Validar e salvar'), on_click=salvar)], wrap=True),
                               status], tight=True, spacing=10, scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton(content=tr('Desvincular'), on_click=desvincular),
                     ft.TextButton(content=tr('Fechar'), on_click=fechar)]))

    # ---------------------------------------------------------------- histórico
    def abrir_historico(self, e):
        coluna = ft.Column([], tight=True, spacing=6, scroll=ft.ScrollMode.AUTO)

        def nome_amigavel(caminho):
            bruto = os.path.basename(caminho)[len('progresso_'):-len('.json')]
            reverso = bruto.startswith('yt-sp_')
            if reverso:
                bruto = bruto[len('yt-sp_'):]
            return bruto.replace('_', ' ') + ('  (YT Music ➔ Spotify)' if reverso else '')

        def montar_lista():
            coluna.controls.clear()
            arquivos = sorted(glob.glob(os.path.join(nuc.DATA_DIR, 'progresso_*.json')))
            if not arquivos:
                coluna.controls.append(ft.Text('Nenhum progresso salvo.', size=12, color=TEXTO2))
            for arq in arquivos:
                coluna.controls.append(ft.Row([
                    ft.Text(nome_amigavel(arq), size=13, expand=True),
                    ft.TextButton(content=tr('Apagar'), on_click=lambda e, a=arq: apagar(a))]))

        def apagar(arq):
            try:
                os.remove(arq)
            except OSError:
                pass
            montar_lista()

        def fechar(e):
            self.page.pop_dialog()

        montar_lista()
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(tr('Progresso salvo')),
            content=ft.Column([ft.Text('Para retomar uma migração, inicie de novo com o mesmo link e o '
                                       'mesmo nome de playlist.', size=12, color=TEXTO2), coluna],
                              tight=True, spacing=10),
            actions=[ft.TextButton(content=tr('Fechar'), on_click=fechar)]))


async def main(page: ft.Page):
    from mobile_language import ensure_language
    await ensure_language(page, nuc.DATA_DIR)
    tela = Tela(page)
    tela.montar()
    page.run_task(tela.poller)
    if nuc.checar_atualizacoes_habilitadas():
        tela.checar_atualizacao(manual=False)


ft.run(main)
