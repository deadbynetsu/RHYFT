# -*- coding: utf-8 -*-
"""
Núcleo do RHYFT (SEM interface): usado pelo app do computador (app.py)
e pelo app de celular (src/main.py).

Contém: caminhos/configuração, login do Spotify (PKCE), cabeçalhos do YouTube Music,
validação das músicas e os dois processos de migração (Spotify ➔ YouTube Music e o inverso).

Quem usa o núcleo implementa estes ganchos (veja a classe MotorMigracao):
  log(msg, tipo), ui(fn, *args), _ui_progresso(...), _ui_progresso_status(...),
  _ui_progresso_fim(...), _ui_progresso_reset(...), _ui_migracao_terminada(controle),
  _ui_travar_controles(), escolher_versao(n, total, faixa, opcoes, origem='Spotify')
"""
import base64
import glob
import hashlib
import json
import os
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

import requests
import spotipy
from spotipy.exceptions import SpotifyException
from ytmusicapi import YTMusic

# ============================== CAMINHOS & CONFIGURAÇÕES ======================
APP_NAME = 'RHYFT'
LEGACY_APP_NAME = 'MigradorPlaylists'
APP_VERSION = '1.6.0'
TAMANHO_LOTE = 10          # quantas músicas por envio ao YouTube Music (cada lote é conferido depois)
TOLERANCIA_DURACAO = 15    # segundos de diferença aceitos entre Spotify e YouTube
PAUSA_BUSCA_SPOTIFY = 0.4  # segundos entre uma busca e outra no Spotify (YouTube ➔ Spotify)
SPOTIFY_MARKET = 'BR'      # o país da conta tem prioridade quando o token é de usuário
GITHUB_REPO = 'deadbynetsu/RHYFT'  # "usuario/repositorio" onde ficam as Releases

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def obter_pasta_dados():
    """Pasta gravável por usuário (funciona mesmo se o .exe estiver em Program Files)."""
    # MIGRADOR_DATA_DIR: usada no celular (pasta privada do app). No PC vale o %APPDATA% de sempre.
    forcada = os.environ.get('MIGRADOR_DATA_DIR')
    if forcada:
        pasta = forcada
    else:
        base = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'), '.config')
        pasta = os.path.join(base, APP_NAME)
        antiga = os.path.join(base, LEGACY_APP_NAME)
        if not os.path.exists(pasta) and os.path.isdir(antiga):
            try:
                shutil.copytree(antiga, pasta)
            except OSError:
                pass
    try:
        os.makedirs(pasta, exist_ok=True)
        return pasta
    except OSError:
        return BASE_DIR


DATA_DIR = obter_pasta_dados()
SPOTIFY_CONFIG_PATH = os.path.join(DATA_DIR, 'spotify_config.json')   # só o client_id
SPOTIFY_TOKEN_PATH = os.path.join(DATA_DIR, 'spotify_token.json')
YT_AUTH_PATH = os.path.join(DATA_DIR, 'ytmusic_auth.json')
SETTINGS_PATH = os.path.join(DATA_DIR, 'settings.json')
LOG_ERROS_PATH = os.path.join(DATA_DIR, 'erros.log')


def checar_atualizacoes_habilitadas():
    """Retorna True se a checagem automática estiver ligada. Padrão: True."""
    cfg = ler_json(SETTINGS_PATH, {}) or {}
    return cfg.get('auto_update', True)


def salvar_config_atualizacao(habilitado):
    """Salva a preferência do usuário de checar atualizações."""
    cfg = ler_json(SETTINGS_PATH, {}) or {}
    cfg['auto_update'] = bool(habilitado)
    gravar_json(SETTINGS_PATH, cfg)


# ============================== SPOTIFY (PKCE) ================================
SPOTIFY_REDIRECT_URI = 'http://127.0.0.1:8080'
SPOTIFY_SCOPE = ('playlist-read-private playlist-read-collaborative '
                 'playlist-modify-private playlist-modify-public')
SPOTIFY_AUTH_URL = 'https://accounts.spotify.com/authorize'
SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'

PAGINA_OK = (
    '<html><head><meta charset="utf-8"><title>RHYFT</title></head>'
    '<body style="font-family:Segoe UI,Arial,sans-serif;text-align:center;margin-top:15%">'
    '<h2>\u2705 Spotify vinculado!</h2>'
    '<p>Pode fechar esta aba e voltar ao aplicativo.</p></body></html>'
)
PAGINA_ERRO = (
    '<html><head><meta charset="utf-8"><title>RHYFT</title></head>'
    '<body style="font-family:Segoe UI,Arial,sans-serif;text-align:center;margin-top:15%">'
    '<h2>\u274c A autorização não foi concluída</h2>'
    '<p>Volte ao aplicativo e tente novamente.</p></body></html>'
)


def ler_json(caminho, padrao=None):
    try:
        with open(caminho, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar_json(caminho, dados):
    """Grava de forma atômica (evita arquivo pela metade) e restringe permissões."""
    tmp = caminho + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass


def _id_origem_spotify(texto):
    texto = str(texto or '').strip()
    m = re.search(r'spotify\.com/(?:intl-[a-z]{2}/)?playlist/([A-Za-z0-9]{10,30})(?:[/?#]|$)', texto, re.I)
    if m:
        return m.group(1)
    m = re.fullmatch(r'(?:spotify:playlist:)?([A-Za-z0-9]{10,30})', texto)
    return m.group(1) if m else None


def _direcao_arquivo(caminho):
    return 'yt_sp' if os.path.basename(caminho).startswith('progresso_yt-sp_') else 'sp_yt'


def _ler_estado_migracao(caminho):
    """Lê um checkpoint explicitamente; um arquivo inválido nunca vira estado vazio."""
    try:
        with open(caminho, encoding='utf-8') as arquivo:
            estado = json.load(arquivo)
    except (OSError, ValueError) as erro:
        raise RuntimeError('O histórico selecionado está ausente ou inválido. Nenhuma playlist foi criada.') from erro
    if not isinstance(estado, dict) or not isinstance(estado.get('playlist_id'), str) or not estado['playlist_id']:
        raise RuntimeError('O histórico selecionado não possui uma playlist de destino válida.')
    direcao = estado.get('direcao', _direcao_arquivo(caminho))
    if direcao not in ('sp_yt', 'yt_sp'):
        raise RuntimeError('A direção do histórico selecionado é inválida.')
    for campo in ('adicionadas', 'videos', 'destino_ids', 'puladas'):
        valores = estado.get(campo, [])
        if not isinstance(valores, list) or any(not isinstance(valor, str) for valor in valores):
            raise RuntimeError('O histórico selecionado possui dados de progresso inválidos.')
    mapa = estado.get('mapeamento', {})
    if not isinstance(mapa, dict) or any(not isinstance(chave, str) or not isinstance(valor, str) or not valor
                                         for chave, valor in mapa.items()):
        raise RuntimeError('O histórico selecionado possui associações de músicas inválidas.')
    origem = estado.get('origem_id')
    if origem is not None and (not isinstance(origem, str) or not origem or
                              (direcao == 'sp_yt' and _id_origem_spotify(origem) != origem) or
                              (direcao == 'yt_sp' and extrair_id_playlist_yt(origem) != origem)):
        raise RuntimeError('A origem do histórico selecionado é inválida.')
    if estado.get('versao_estado', 0) == 2 and (not origem or 'direcao' not in estado):
        raise RuntimeError('O histórico selecionado está incompleto.')
    conta = estado.get('conta_destino')
    if conta is not None and (not isinstance(conta, str) or not conta):
        raise RuntimeError('A conta do histórico selecionado é inválida.')
    conta_origem = estado.get('conta_origem')
    if conta_origem is not None and (not isinstance(conta_origem, str) or not conta_origem):
        raise RuntimeError('A conta de origem do histórico selecionado é inválida.')
    return estado, direcao


def listar_historico_migracoes():
    """Históricos modernos podem ser atualizados; os antigos pedem a origem ao usuário."""
    registros, adotados, destinos_antigos = [], set(), {}
    for caminho in glob.glob(os.path.join(DATA_DIR, 'progresso_*.json')):
        bruto = os.path.basename(caminho)[len('progresso_'):-len('.json')]
        if bruto.startswith('yt-sp_'):
            bruto = bruto[len('yt-sp_'):]
        nome = bruto.replace('_', ' ')
        try:
            estado, direcao = _ler_estado_migracao(caminho)
            origem = estado.get('origem_id')
            legado = not origem
            if legado:
                destinos_antigos[os.path.abspath(caminho)] = estado['playlist_id']
            elif isinstance(estado.get('arquivo_legado'), str):
                adotados.add((estado['arquivo_legado'], estado['playlist_id']))
            nome = estado.get('nome_playlist') or nome
            if not isinstance(nome, str):
                nome = bruto.replace('_', ' ')
            origem_input = (f'https://open.spotify.com/playlist/{origem}' if direcao == 'sp_yt'
                            else 'LM' if origem == 'LM' else f'https://music.youtube.com/playlist?list={origem}') if origem else None
            if direcao == 'yt_sp' and estado.get('origem_input') == 'LM':
                origem_input = 'LM'
            status = 'legacy' if legado else estado.get('status', 'interrupted')
            if status not in ('running', 'completed', 'interrupted', 'legacy'):
                status = 'interrupted'
            registros.append({'arquivo': os.path.abspath(caminho), 'direcao': direcao, 'origem_input': origem_input,
                              'origem_id': origem, 'nome_playlist': nome, 'status': status,
                              'adicionadas': len(set(estado.get('adicionadas', []))), 'legado': legado, 'valido': True})
        except RuntimeError:
            registros.append({'arquivo': os.path.abspath(caminho), 'direcao': _direcao_arquivo(caminho),
                              'origem_input': None, 'origem_id': None, 'nome_playlist': nome,
                              'status': 'legacy', 'adicionadas': 0, 'legado': True, 'valido': False})
    registros = [item for item in registros if not (item['legado'] and
                 (os.path.basename(item['arquivo']), destinos_antigos.get(item['arquivo'])) in adotados)]
    registros.sort(key=lambda item: os.path.getmtime(item['arquivo']) if os.path.exists(item['arquivo']) else 0,
                   reverse=True)
    return registros


def _conta_spotify(sp):
    try:
        conta = sp.current_user().get('id')
        return conta if isinstance(conta, str) and conta else None
    except Exception:
        return None


def _conta_menu_youtube(resposta):
    """Own-channel links in the active account menu only, never account-switch lists.

    ytmusicapi 1.12.3 discards these endpoints in get_account_info(). The
    MultiPageMenu/CompactLink schema is also documented by YouTube.js; the
    ACCOUNT_BOX item is optional, so absence never invents an identity.
    """
    if not isinstance(resposta, dict) or not isinstance(resposta.get('actions'), list):
        return None
    ids = set()
    for acao in resposta['actions']:
        if not isinstance(acao, dict):
            continue
        popup = (acao.get('openPopupAction') or {}).get('popup') or {}
        menu = popup.get('multiPageMenuRenderer') or {}
        if not isinstance(menu, dict) or not isinstance((menu.get('header') or {}).get('activeAccountHeaderRenderer'), dict):
            continue
        for secao in menu.get('sections') or []:
            if not isinstance(secao, dict):
                continue
            itens = (secao.get('multiPageMenuSectionRenderer') or {}).get('items') or []
            for item in itens:
                if not isinstance(item, dict):
                    continue
                link = item.get('compactLinkRenderer') or {}
                if not isinstance(link, dict) or (link.get('icon') or {}).get('iconType') != 'ACCOUNT_BOX':
                    continue
                endpoint = (link.get('navigationEndpoint') or {}).get('browseEndpoint') or {}
                canal = endpoint.get('browseId')
                tipo = ((endpoint.get('browseEndpointContextSupportedConfigs') or {})
                        .get('browseEndpointContextMusicConfig') or {}).get('pageType')
                if isinstance(canal, str) and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', canal) and tipo in (
                        None, 'MUSIC_PAGE_TYPE_USER_CHANNEL', 'MUSIC_PAGE_TYPE_ARTIST'):
                    ids.add(canal)
    return next(iter(ids)) if len(ids) == 1 else None


def _conta_youtube(yt):
    try:
        info = yt.get_account_info() or {}
        conta = info.get('channelId') or info.get('accountId')
        if isinstance(conta, str) and conta:
            return conta
        enviar = getattr(yt, '_send_request', None)
        verificar = getattr(yt, '_check_auth', None)
        if callable(enviar) and callable(verificar):
            verificar()
            return _conta_menu_youtube(enviar('account/account_menu', {}))
        return None
    except Exception:
        return None


def _caminho_estado_migracao(direcao, origem_id, conta, legado_id=None, conta_origem=None):
    partes = [direcao, origem_id, conta, legado_id]
    if origem_id == 'LM':
        partes.append(conta_origem)
    identidade = json.dumps(partes, ensure_ascii=False)
    resumo = hashlib.sha256(identidade.encode('utf-8')).hexdigest()[:24]
    prefixo = 'yt-sp_' if direcao == 'yt_sp' else 'sp-yt_'
    return os.path.join(DATA_DIR, f'progresso_{prefixo}{resumo}.json')


def _preparar_estado_migracao(direcao, origem_id, nome, conta, arquivo_estado=None, conta_origem=None, origem_input=None):
    """Seleciona pela origem; nomes iguais e outras contas não misturam migrações."""
    estado = None
    arquivo_legado = None
    caminho = None
    if arquivo_estado is not None:
        caminho = os.path.realpath(os.path.abspath(os.fspath(arquivo_estado)))
        raiz = os.path.realpath(DATA_DIR)
        if os.path.dirname(caminho) != raiz or not re.fullmatch(r'progresso_.+\.json', os.path.basename(caminho)):
            raise RuntimeError('O histórico selecionado precisa estar na pasta de dados do RHYFT.')
        estado, direcao_salva = _ler_estado_migracao(caminho)
        if direcao_salva != direcao:
            raise RuntimeError('O histórico selecionado pertence à outra direção de migração.')
        origem_salva = estado.get('origem_id')
        if origem_salva == 'LM' and estado.get('conta_origem'):
            if not conta_origem:
                raise RuntimeError('Não consegui confirmar a conta de origem deste histórico de músicas curtidas. Conecte a conta original.')
            if estado['conta_origem'] != conta_origem:
                raise RuntimeError('O histórico de músicas curtidas pertence a outra conta do YouTube. Conecte a conta original.')
        transicao_lm = origem_salva == 'LM' and origem_input == 'LM'
        if origem_salva and origem_salva != origem_id and not transicao_lm:
            raise RuntimeError('O histórico selecionado pertence a outra playlist de origem. Confira o link.')
        if conta and estado.get('conta_destino') and estado['conta_destino'] != conta:
            raise RuntimeError('O histórico selecionado pertence a outra conta de destino. Conecte a conta original.')
        if not origem_salva or (origem_salva == 'LM' and origem_id != 'LM'):
            # Keep the user's old file intact; only the verified adopted copy
            # is saved with its newly supplied source identity.
            arquivo_legado = os.path.basename(caminho)
            caminho = _caminho_estado_migracao(direcao, origem_id, conta, estado['playlist_id'], conta_origem)
            if os.path.exists(caminho):
                adotado, direcao_adotada = _ler_estado_migracao(caminho)
                if direcao_adotada != direcao or adotado.get('origem_id') != origem_id or adotado['playlist_id'] != estado['playlist_id']:
                    raise RuntimeError('Já existe outro histórico nesse destino de armazenamento.')
                estado = adotado
    else:
        candidatos = []
        for registro in listar_historico_migracoes():
            if not registro['valido'] or registro['legado'] or registro['direcao'] != direcao or registro['origem_id'] != origem_id:
                continue
            salvo, _ = _ler_estado_migracao(registro['arquivo'])
            if conta and salvo.get('conta_destino') and salvo['conta_destino'] != conta:
                continue
            if origem_id == 'LM':
                if not conta_origem and salvo.get('conta_origem'):
                    raise RuntimeError('Não consegui confirmar a conta de origem deste histórico de músicas curtidas. Conecte a conta original; o progresso foi preservado.')
                if not conta_origem or salvo.get('conta_origem') != conta_origem:
                    continue
            candidatos.append((salvo, registro['arquivo']))
        if candidatos:
            preferido = next((c for c in candidatos if conta and c[0].get('conta_destino') == conta), candidatos[0])
            estado, caminho = preferido
        else:
            sem_identidade = origem_id == 'LM' and not conta_origem
            caminho = _caminho_estado_migracao(direcao, origem_id, conta,
                                             legado_id=f'exportacao-{secrets.token_hex(16)}' if sem_identidade else None,
                                             conta_origem=conta_origem)
            if os.path.exists(caminho):
                raise RuntimeError('O histórico dessa origem está inválido. Confira o histórico antes de continuar.')
    novo = estado is None
    estado = dict(estado or {'playlist_id': None, 'adicionadas': [], 'videos': [], 'destino_ids': [], 'puladas': []})
    mapa = dict(estado.get('mapeamento', {}))
    if not mapa:
        chaves = estado.get('adicionadas', [])
        ids = estado.get('videos' if direcao == 'sp_yt' else 'destino_ids', [])
        if len(chaves) == len(ids):
            mapa = dict(zip(chaves, ids))
    estado.update({'versao_estado': 2, 'direcao': direcao, 'origem_id': origem_id,
                   'origem_input': f'https://open.spotify.com/playlist/{origem_id}' if direcao == 'sp_yt'
                   else 'LM' if origem_id == 'LM' else f'https://music.youtube.com/playlist?list={origem_id}',
                   'nome_playlist': estado.get('nome_playlist') or nome, 'conta_destino': estado.get('conta_destino') or conta,
                   'mapeamento': mapa})
    if direcao == 'yt_sp' and origem_input == 'LM':
        estado['origem_input'] = 'LM'
        estado['origem_nao_verificada'] = origem_id == 'LM' and not conta_origem
        if conta_origem:
            estado['conta_origem'] = estado.get('conta_origem') or conta_origem
    if arquivo_legado:
        estado['arquivo_legado'] = arquivo_legado
    for campo in ('adicionadas', 'videos', 'destino_ids', 'puladas'):
        estado[campo] = list(estado.get(campo, []))
    return caminho, estado, novo


def _reconciliar_estado_migracao(estado, ids_destino):
    mapa = {chave: destino for chave, destino in estado['mapeamento'].items() if destino in ids_destino}
    estado['mapeamento'] = mapa
    estado['adicionadas'] = list(mapa)
    estado['videos' if estado['direcao'] == 'sp_yt' else 'destino_ids'] = list(dict.fromkeys(mapa.values()))
    # In older reverse checkpoints puladas meant "already in destination",
    # not a permanent user decision. Reevaluate those against the real list.
    estado['puladas'] = []


def _registrar_faixa_migracao(estado, chave, destino):
    estado['mapeamento'][chave] = destino
    if chave not in estado['adicionadas']:
        estado['adicionadas'].append(chave)
    campo = 'videos' if estado['direcao'] == 'sp_yt' else 'destino_ids'
    if destino not in estado[campo]:
        estado[campo].append(destino)


def _salvar_estado_migracao(caminho, estado, status=None):
    if status:
        estado['status'] = status
    estado['atualizado_em'] = time.time()
    gravar_json(caminho, estado)


def abrir_url_externa(url):
    """Abre um endereço no navegador. O app de celular troca esta função pela dele."""
    return webbrowser.open(url)


def aguardar_codigo_spotify(url_autorizacao, state_esperado, timeout=180, cancelar=None):
    """Abre o navegador e espera o Spotify redirecionar para o servidorzinho local."""
    partes = urllib.parse.urlparse(SPOTIFY_REDIRECT_URI)
    host, porta = partes.hostname, partes.port or 80
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
            corpo = (PAGINA_OK if ok else PAGINA_ERRO).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *args):
            pass

    try:
        servidor = HTTPServer((host, porta), Handler)
    except OSError as e:
        raise RuntimeError(
            f'Não consegui abrir a porta {porta} para receber a autorização do Spotify. '
            f'Feche outros programas que a usem (ou uma tentativa anterior) e tente de novo. ({e})'
        )
    servidor.timeout = 1
    try:
        if not abrir_url_externa(url_autorizacao):
            raise RuntimeError('Não consegui abrir o navegador.')
        limite = time.time() + timeout
        while not recebido and time.time() < limite:
            if cancelar is not None and cancelar.is_set():
                raise InterruptedError('Vinculação cancelada.')
            servidor.handle_request()
    finally:
        servidor.server_close()

    if not recebido:
        raise TimeoutError('Tempo esgotado esperando a autorização no navegador. Tente de novo.')
    if 'error' in recebido:
        raise RuntimeError(f"Autorização negada no Spotify ({recebido['error'][0]}).")
    if recebido.get('state', [None])[0] != state_esperado:
        raise RuntimeError('Resposta inesperada do Spotify (state diferente). Tente de novo.')
    return recebido['code'][0]


class SpotifyPKCE:
    """Gerenciador de autenticação compatível com spotipy (auth_manager), sem client secret."""

    def __init__(self, client_id, caminho_token):
        self.client_id = client_id
        self.caminho_token = caminho_token

    def _ler_token(self):
        return ler_json(self.caminho_token, None)

    def _gravar_token(self, info):
        info = dict(info)
        info['expires_at'] = int(time.time()) + int(info.get('expires_in', 3600))
        gravar_json(self.caminho_token, info)
        return info

    @staticmethod
    def _mensagem_erro(resp):
        try:
            d = resp.json()
            return d.get('error_description') or d.get('error') or resp.text
        except ValueError:
            return resp.text[:200]

    def vinculado(self):
        t = self._ler_token()
        return bool(t and t.get('refresh_token'))

    def tem_escrita(self):
        """True se a autorização guardada permite criar playlists (YouTube Music ➔ Spotify)."""
        t = self._ler_token() or {}
        return 'playlist-modify-private' in str(t.get('scope', '')).split()

    def preparar_autorizacao(self):
        """Gera o endereço de autorização (PKCE). Devolve {'url', 'verifier', 'state'}."""
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('ascii')).digest()
        ).rstrip(b'=').decode('ascii')
        state = secrets.token_urlsafe(16)
        url = SPOTIFY_AUTH_URL + '?' + urllib.parse.urlencode({
            'client_id': self.client_id,
            'response_type': 'code',
            'redirect_uri': SPOTIFY_REDIRECT_URI,
            'scope': SPOTIFY_SCOPE,
            'state': state,
            'code_challenge_method': 'S256',
            'code_challenge': challenge,
        })
        return {'url': url, 'verifier': verifier, 'state': state}

    @staticmethod
    def extrair_codigo(texto, state_esperado=None):
        """Plano B: aceita o endereço completo da barra do navegador (…/?code=…&state=…) ou só o código."""
        texto = (texto or '').strip()
        if not texto:
            raise ValueError('Cole o endereço da barra do navegador (ele contém "code=...").')
        if '?' in texto or texto.lower().startswith('http'):
            consulta = urllib.parse.parse_qs(urllib.parse.urlparse(texto).query)
            if 'error' in consulta:
                raise RuntimeError(f"Autorização negada no Spotify ({consulta['error'][0]}).")
            if 'code' not in consulta:
                raise ValueError('Esse endereço não tem "code=". Copie o endereço completo da barra do navegador.')
            estado = consulta.get('state', [None])[0]
            if state_esperado and estado and estado != state_esperado:
                raise RuntimeError('Esse endereço é de outra tentativa de vinculação. Toque em "Vincular" de novo.')
            return consulta['code'][0]
        if re.fullmatch(r'[A-Za-z0-9_\-]{20,}', texto):
            return texto
        raise ValueError('Não reconheci esse texto. Cole o endereço completo da barra do navegador.')

    def trocar_codigo(self, codigo, verifier):
        resp = requests.post(SPOTIFY_TOKEN_URL, data={
            'client_id': self.client_id,
            'grant_type': 'authorization_code',
            'code': codigo,
            'redirect_uri': SPOTIFY_REDIRECT_URI,
            'code_verifier': verifier,
        }, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f'O Spotify recusou a autorização: {self._mensagem_erro(resp)}')
        self._gravar_token(resp.json())

    def autorizar(self, timeout=180, cancelar=None, preparado=None):
        p = preparado or self.preparar_autorizacao()
        codigo = aguardar_codigo_spotify(p['url'], p['state'], timeout, cancelar)
        self.trocar_codigo(codigo, p['verifier'])

    def _renovar(self, token):
        resp = requests.post(SPOTIFY_TOKEN_URL, data={
            'grant_type': 'refresh_token',
            'refresh_token': token['refresh_token'],
            'client_id': self.client_id,
        }, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                'A sessão do Spotify expirou ou foi revogada. '
                'Clique em "Vincular" no cartão do Spotify e autorize novamente.'
            )
        novo = resp.json()
        novo.setdefault('refresh_token', token['refresh_token'])
        return self._gravar_token(novo)

    def get_access_token(self, as_dict=True, check_cache=True):
        token = self._ler_token()
        if not token or not token.get('refresh_token'):
            raise RuntimeError('Spotify não vinculado. Clique em "Vincular" no cartão do Spotify.')
        if token.get('expires_at', 0) - 60 < time.time():
            token = self._renovar(token)
        return token if as_dict else token['access_token']


def carregar_auth_spotify():
    cfg = ler_json(SPOTIFY_CONFIG_PATH, {}) or {}
    client_id = str(cfg.get('client_id', '')).strip()
    return SpotifyPKCE(client_id, SPOTIFY_TOKEN_PATH) if client_id else None


def spotify_vinculado():
    auth = carregar_auth_spotify()
    return bool(auth and auth.vinculado())


# ============================== YOUTUBE MUSIC =================================
HEADERS_PERMITIDOS = {
    'cookie', 'authorization', 'user-agent', 'accept-language',
    'x-goog-authuser', 'x-goog-visitor-id', 'x-goog-pageid',
    'x-youtube-client-name', 'x-youtube-client-version',
    'x-youtube-bootstrap-logged-in', 'origin', 'referer', 'x-origin',
}
USER_AGENT_PADRAO = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
)
_PADRAO_LINHA = re.compile(r'^(:?[A-Za-z][A-Za-z0-9-]*):\s+(.*)$')


def _valor_entre_aspas(m):
    return m.group(1) if m.group(1) is not None else m.group(2)


def cabecalhos_de_curl(texto):
    """Extrai os cabeçalhos de um 'Copy as cURL (bash)' do Chrome/Brave/Edge."""
    texto = texto.replace('\\\r\n', ' ').replace('\\\n', ' ')
    texto = re.split(r'\s--data(?:-raw|-binary|-urlencode)?\s', texto)[0]
    brutos = {}
    for m in re.finditer(r"""(?<!\S)(?:-H|--header)\s+(?:'([^']*)'|"([^"]*)")""", texto):
        h = _valor_entre_aspas(m)
        if ':' in h:
            nome, valor = h.split(':', 1)
            brutos[nome.strip().lower()] = valor.strip()
    m = re.search(r"""(?<!\S)(?:-b|--cookie)\s+(?:'([^']*)'|"([^"]*)")""", texto)
    if m:
        brutos['cookie'] = _valor_entre_aspas(m).strip()
    return brutos


def normalizar_cabecalhos_yt(texto):
    """Aceita JSON, 'nome: valor' (uma linha) ou nome/valor em linhas alternadas."""
    texto = (texto or '').strip()
    if not texto:
        raise ValueError('O campo está vazio. Cole os cabeçalhos copiados do navegador.')
    brutos = {}
    if texto.lower().startswith('curl'):
        if '^"' in texto:
            raise ValueError('Você copiou o cURL no formato do Windows (cmd). Clique com o botão '
                             'direito na requisição > Copiar > "Copiar como cURL (bash)" e cole de novo.')
        brutos = cabecalhos_de_curl(texto)
    elif texto.startswith('{'):
        try:
            dados = json.loads(texto)
        except ValueError as e:
            raise ValueError(f'JSON inválido: {e}')
        brutos = {str(k).strip().lower(): str(v).strip() for k, v in dados.items()}
    else:
        linhas = [l.strip() for l in texto.splitlines() if l.strip()]
        casam = [l for l in linhas if _PADRAO_LINHA.match(l)]
        if linhas and len(casam) >= len(linhas) / 2:
            for l in casam:
                nome, valor = _PADRAO_LINHA.match(l).groups()
                brutos[nome.strip().lower()] = valor.strip()
        else:
            i = 0
            while i < len(linhas) - 1:
                nome = linhas[i].lower().rstrip(':').strip()
                if nome in HEADERS_PERMITIDOS:
                    brutos[nome] = linhas[i + 1].strip()
                    i += 2
                else:
                    i += 1

    filtrados = {k: v for k, v in brutos.items() if k in HEADERS_PERMITIDOS and v}

    cookie = filtrados.get('cookie')
    if not cookie and 'authorization' in filtrados:
        raise ValueError(
            "O texto colado foi cortado antes do 'cookie' (termina no 'authorization'). Em vez de "
            "selecionar na mão, clique com o botão direito na requisição > Copiar > "
            "'Copiar como cURL (bash)' e cole aqui."
        )
    if not cookie:
        raise ValueError(
            "Não encontrei o cabeçalho 'cookie'. Copie os *Request Headers* (cabeçalhos da "
            "requisição) de uma requisição POST feita em music.youtube.com, com a conta logada."
        )
    if not re.search(r'(^|;\s*)__Secure-3PAPISID=', cookie):
        raise ValueError(
            'O cookie copiado não tem a sessão logada (falta __Secure-3PAPISID). '
            'Confirme que está logado no music.youtube.com e copie de novo.'
        )

    final = {
        'user-agent': USER_AGENT_PADRAO,
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate',
        'content-type': 'application/json',
        'origin': 'https://music.youtube.com',
        'x-goog-authuser': '0',
    }
    final.update(filtrados)
    return final


def testar_ytmusic(caminho):
    """Faz uma chamada autenticada de verdade. Levanta exceção se a sessão não servir."""
    yt = YTMusic(caminho)
    try:
        info = yt.get_account_info()
        nome = (info or {}).get('accountName')
        return nome or 'conta conectada'
    except Exception:
        pass
    yt.get_library_playlists(limit=1)
    return 'conta conectada'


def validar_e_salvar_yt(headers):
    tmp = os.path.join(DATA_DIR, 'ytmusic_auth_novo.json')
    gravar_json(tmp, headers)
    try:
        nome = testar_ytmusic(tmp)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, YT_AUTH_PATH)
    return nome


# ============================== UTILITÁRIOS ===================================
def registrar_erro_em_arquivo():
    try:
        with open(LOG_ERROS_PATH, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + traceback.format_exc() + '\n')
    except OSError:
        pass


def explicar_erro(e):
    texto = str(e).strip() or e.__class__.__name__
    baixo = texto.lower()
    if 'expecting value' in baixo:
        return ('O YouTube não aceitou a sessão copiada (respondeu algo que não é JSON). '
                'Copie os cabeçalhos de novo, de uma requisição POST "browse" no music.youtube.com, '
                'logado na conta.\n(detalhe técnico: ' + texto + ')')
    if isinstance(e, requests.exceptions.ConnectionError) or 'timed out' in baixo:
        return 'Sem conexão com a internet (ou o servidor não respondeu). Verifique a rede e tente de novo.'
    if any(s in baixo for s in ('401', '403', 'unauthorized', 'forbidden', 'authentication', 'sign in')):
        return ('O YouTube Music recusou a sessão. Ela pode ter expirado: vincule o YouTube Music '
                'de novo.\n(detalhe técnico: ' + texto + ')')
    return texto


def migrar_arquivos_antigos():
    """Traz os progresso_*.json que ficavam ao lado do .exe para a pasta de dados."""
    if os.path.abspath(BASE_DIR) == os.path.abspath(DATA_DIR):
        return
    for arq in glob.glob(os.path.join(BASE_DIR, 'progresso_*.json')):
        destino = os.path.join(DATA_DIR, os.path.basename(arq))
        if not os.path.exists(destino):
            try:
                shutil.move(arq, destino)
            except OSError:
                pass


class MigracaoCancelada(Exception):
    """Levantada dentro da migração quando o usuário cancela."""


class MigracaoPausada(Exception):
    """Levantada dentro da migração quando o usuário pausa (só entre uma música e outra)."""


class ControleMigracao:
    """Sinais de pausa/cancelamento de UMA migração (seguro entre threads)."""

    def __init__(self):
        self._pausar = threading.Event()
        self._cancelar = threading.Event()
        self._parar = threading.Event()

    def pausar(self):
        self._pausar.set()
        self._parar.set()

    def cancelar(self):
        self._cancelar.set()
        self._parar.set()

    def cancelada(self):
        return self._cancelar.is_set()

    def checar(self):
        if self._cancelar.is_set():
            raise MigracaoCancelada()
        if self._pausar.is_set():
            raise MigracaoPausada()

    def esperar(self, segundos, so_cancelamento=False):
        if so_cancelamento:
            return self._cancelar.wait(segundos)
        return self._parar.wait(segundos)


def _cortar(texto, limite=70):
    texto = ' '.join(str(texto).split())
    return texto if len(texto) <= limite else texto[:limite - 1].rstrip() + '…'




# ============================== YOUTUBE MUSIC ➔ SPOTIFY (AUXILIARES) ==========
def extrair_id_playlist_yt(texto):
    """Aceita o link completo (music.youtube.com/playlist?list=...) ou só o código da playlist."""
    texto = (texto or '').strip()
    m = re.search(r'[?&]list=([A-Za-z0-9_-]+)', texto)
    if m:
        return m.group(1)
    if texto == 'LM' or re.fullmatch(r'[A-Za-z0-9_-]{10,}', texto):
        return texto
    return None


def _nome_artista_busca(nome):
    """Tira ' - Topic' e 'VEVO' do nome do canal para usar na busca."""
    n = re.sub(r'(?i)\s*-\s*topic$', '', str(nome or '')).strip()
    n = re.sub(r'(?i)vevo$', '', n).strip()
    return n.replace('"', '')


def _titulo_para_busca(titulo, artistas, eh_video=False):
    """Título 'limpo' para pesquisar no Spotify. Devolve (título, artista_extra_ou_None)."""
    t = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', str(titulo or ''))
    t = re.sub(r'\s+(feat|ft|featuring)\.?\s.*$', '', t, flags=re.I)
    partes = [p.strip() for p in t.split(' - ') if p.strip()]
    if len(partes) >= 2:
        primeiro = _artista_limpo(partes[0])
        eh_artista = any(primeiro and primeiro == _artista_limpo(a) for a in artistas)
        if eh_video or eh_artista:
            return partes[1].replace('"', ''), partes[0].replace('"', '')   # "Artista - Título"
        return partes[0].replace('"', ''), None                              # "Título - Versão"
    return (' '.join(t.split()) or str(titulo or '')).replace('"', ''), None


def explicar_erro_spotify(e):
    status = getattr(e, 'http_status', None)
    texto = str(e).strip() or e.__class__.__name__
    if status == 401:
        return ('A sessão do Spotify expirou ou foi revogada. Abra "Gerenciar" no cartão do Spotify '
                'e autorize de novo.')
    if status == 403:
        return ('O Spotify recusou a operação (403). Confira se você autorizou de novo depois de atualizar '
                'o app (a permissão de criar playlists é nova) e se o app do painel de desenvolvedores é '
                'de uma conta Premium.\n(detalhe técnico: ' + texto + ')')
    if status == 429:
        return ('O Spotify pediu para ir mais devagar (limite de requisições). '
                'Espere alguns minutos e retome pelo mesmo link de origem ou pelo histórico.')
    if isinstance(e, requests.exceptions.ConnectionError) or 'timed out' in texto.lower():
        return 'Sem conexão com a internet (ou o servidor não respondeu). Verifique a rede e tente de novo.'
    return texto


MSG_REAUTORIZAR = (
    'Para criar playlists no Spotify o app precisa de uma permissão nova (escrita).\n\n'
    'Clique em "Gerenciar" no cartão do Spotify e depois em "Vincular e autorizar" de novo. '
    'Seu Client ID continua salvo; é só aceitar no navegador.')


# ============================== VALIDAÇÃO DE RESULTADOS =======================
def _normalizar(texto):
    """Minúsculas, sem acento e sem pontuação (mantém outros alfabetos: japonês, cirílico...)."""
    t = unicodedata.normalize('NFKD', str(texto or ''))
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r'[^\w\s]', ' ', t.lower())
    return ' '.join(t.split())


def _titulo_base(texto, do_youtube=False):
    """Título 'puro': sem (feat. ...), [HD], '- Remastered' (Spotify) etc."""
    t = str(texto or '')
    t = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', t)
    t = re.sub(r'\s+(feat|ft|featuring)\.?\s.*$', '', t, flags=re.I)
    if not do_youtube:
        t = t.split(' - ')[0]
    return _normalizar(t) or _normalizar(texto)


def _artista_limpo(nome):
    n = _normalizar(nome)
    n = re.sub(r'\s*(topic|vevo|official|oficial)$', '', n).strip()
    n = re.sub(r'^the ', '', n)
    return n


# Cada grupo é uma "versão" diferente da música. Se um lado tem e o outro não, não é a mesma faixa.
_MODIFICADORES = [
    ('instrumental',), ('slowed',), ('sped up', 'speed up', 'speedup'), ('nightcore',), ('reverb',),
    ('acapella', 'a cappella'), ('karaoke',), ('cover',), ('remix',), ('live', 'ao vivo', 'en vivo'),
    ('acoustic', 'acustico', 'acustica'), ('lofi', 'lo fi'), ('8d',), ('mashup',), ('tribute',), ('demo',),
]


def _tem_modificador(texto_normalizado, grupo):
    return any(re.search(r'(?<!\w)' + re.escape(m) + r'(?!\w)', texto_normalizado) for m in grupo)


# ============================== PC ➔ CELULAR: PACOTE DE VINCULAÇÃO ===========
def exportar_vinculacao():
    """Texto (JSON) para colar no app do celular. É SENSÍVEL: tem os cookies do YouTube Music.

    O Spotify entra só com o Client ID: o celular faz a própria autorização (o Spotify troca o
    token de renovação de tempos em tempos, então compartilhar o do PC derrubaria um dos dois).
    """
    pacote = {'migrador': 1}
    cfg = ler_json(SPOTIFY_CONFIG_PATH, {}) or {}
    if re.fullmatch(r'[0-9a-fA-F]{32}', str(cfg.get('client_id', ''))):
        pacote['spotify'] = {'client_id': cfg['client_id']}
    yt = ler_json(YT_AUTH_PATH, None)
    if isinstance(yt, dict) and yt:
        pacote['ytmusic'] = yt
    if len(pacote) == 1:
        raise RuntimeError('Ainda não há nada vinculado neste computador para exportar.')
    return json.dumps(pacote, ensure_ascii=False)


def importar_vinculacao(texto):
    """Lê o texto exportado pelo app do PC (ou só os cabeçalhos do YouTube Music) e vincula.
    Devolve a lista de mensagens do que foi feito."""
    texto = (texto or '').strip()
    if not texto:
        raise ValueError('O campo está vazio. Cole o texto copiado do app do computador.')
    pacote = None
    if texto.startswith('{'):
        try:
            dados = json.loads(texto)
        except ValueError as e:
            raise ValueError(f'JSON inválido: {e}')
        if isinstance(dados, dict) and 'migrador' in dados:
            pacote = dados
    if pacote is None:   # só os cabeçalhos do YouTube Music (cURL, JSON ou linhas)
        nome = validar_e_salvar_yt(normalizar_cabecalhos_yt(texto))
        return [f'YouTube Music vinculado ({nome}).']

    mensagens = []
    sp = pacote.get('spotify')
    if isinstance(sp, dict) and re.fullmatch(r'[0-9a-fA-F]{32}', str(sp.get('client_id', ''))):
        gravar_json(SPOTIFY_CONFIG_PATH, {'client_id': sp['client_id']})
        mensagens.append('Client ID do Spotify salvo (falta só autorizar a conta neste aparelho).')
    yt = pacote.get('ytmusic')
    if isinstance(yt, dict) and yt:
        nome = validar_e_salvar_yt(normalizar_cabecalhos_yt(json.dumps(yt)))
        mensagens.append(f'YouTube Music vinculado ({nome}).')
    if not mensagens:
        raise ValueError('Não encontrei nada para vincular nesse texto.')
    return mensagens


# ============================== ATUALIZAÇÕES (GitHub Releases) ===============
def parse_version(v_str):
    """'v1.2.3', '1.2.3' ou '1.2.3-beta' -> (1, 2, 3). Texto sem número -> ()."""
    m = re.match(r'\s*[vV]?(\d+(?:\.\d+)*)', str(v_str or ''))
    return tuple(int(x) for x in m.group(1).split('.')) if m else ()


def versao_mais_nova(a, b):
    """True se a versão 'a' é mais nova que 'b' (completa com zeros: 1.2 == 1.2.0)."""
    pa, pb = parse_version(a), parse_version(b)
    n = max(len(pa), len(pb))
    pa += (0,) * (n - len(pa))
    pb += (0,) * (n - len(pb))
    return pa > pb


def buscar_ultima_versao(repo, timeout=(6, 12)):
    """Consulta as Releases públicas do GitHub e devolve {'tag', 'url', 'notas'} da mais nova
    (ignora rascunhos e pré-lançamentos). Levanta RuntimeError com uma mensagem clara."""
    if not repo or 'seu-usuario' in repo:
        raise RuntimeError('O endereço do repositório no GitHub não está configurado no programa.')
    try:
        resp = requests.get(
            f'https://api.github.com/repos/{repo}/releases', params={'per_page': 30}, timeout=timeout,
            headers={'User-Agent': 'RHYFTApp', 'Accept': 'application/vnd.github+json'})
    except requests.exceptions.RequestException as e:
        raise RuntimeError('Não consegui falar com o GitHub (sem internet ou o servidor não respondeu).') from e
    if resp.status_code == 404:
        raise RuntimeError(f'O repositório "{repo}" não foi encontrado no GitHub (nome errado ou privado).')
    if resp.status_code in (403, 429):
        raise RuntimeError('O GitHub limitou as consultas por agora. Tente de novo daqui a alguns minutos.')
    if resp.status_code != 200:
        raise RuntimeError(f'O GitHub respondeu com erro {resp.status_code}.')
    try:
        lista = resp.json()
    except ValueError:
        raise RuntimeError('Resposta inesperada do GitHub.')
    candidatas = [r for r in (lista if isinstance(lista, list) else [])
                  if isinstance(r, dict) and not r.get('draft') and not r.get('prerelease')
                  and parse_version(r.get('tag_name'))]
    if not candidatas:
        raise RuntimeError('Nenhuma versão publicada foi encontrada no GitHub.')
    melhor = candidatas[0]
    for r in candidatas[1:]:
        if versao_mais_nova(r['tag_name'], melhor['tag_name']):
            melhor = r
    return {'tag': melhor['tag_name'],
            'url': melhor.get('html_url') or f'https://github.com/{repo}/releases/latest',
            'notas': (melhor.get('body') or '').strip()}


# ============================== MOTOR DE MIGRAÇÃO =============================
class MotorMigracao:
    """Mistura (mixin) com a lógica das migrações. A interface (PC ou celular) herda desta classe
    e implementa os ganchos listados no topo deste arquivo."""

    def similaridade(self, a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def melhores_candidatos(self, track_name, artist_name, resultados, maximo=3):
        """Os resultados do YouTube que mais se parecem com a faixa do Spotify (até 'maximo')."""
        pontuados = []
        for r in resultados:
            video_id = r.get('videoId')
            if not video_id:
                continue
            titulo = r.get('title', '') or ''
            artistas = r.get('artists') or []
            artista = artistas[0].get('name', '') if artistas else str(r.get('author', '') or '')
            pontos = self.similaridade(track_name, titulo)
            if artist_name:
                pontos += self.similaridade(artist_name, artista)
            pontuados.append((pontos, {'video_id': video_id, 'yt_title': titulo, 'yt_artist': artista}))
        pontuados.sort(key=lambda par: par[0], reverse=True)  # estável: empate mantém a ordem do YouTube
        return [c for _, c in pontuados[:maximo]]

    def validar_resultado(self, track_name, artist_name, yt_title, yt_artist_name,
                          dur_sp=None, dur_yt=None, artistas_sp=None, origem_yt=False):
        """Só aceita sozinho o que for claramente a MESMA faixa; o resto vai para aprovação manual.

        track_name/artist_name/dur_sp/artistas_sp descrevem a faixa de ORIGEM; yt_title/yt_artist_name/
        dur_yt descrevem o CANDIDATO. Normalmente a origem é o Spotify e o candidato é do YouTube.
        Com origem_yt=True é o inverso (origem = YouTube Music, candidato = resultado do Spotify): aí o
        título da origem pode vir como "Artista - Título" e o artista pode ser só o nome do canal.

        Regras: artista principal igual, título quase igual, mesma 'versão' (live, remix, cover...)
        e duração parecida. yt_artist_name pode ser um texto ou uma lista com todos os artistas.
        """
        if isinstance(yt_artist_name, (list, tuple)):
            yt_artistas = [a for a in yt_artist_name if a]
        else:
            yt_artistas = [yt_artist_name] if yt_artist_name else []
        yt_title = yt_title or ''
        partes_origem = track_name.split(' - ') if (origem_yt and ' - ' in track_name) else []

        # 1) artista principal da origem precisa aparecer entre os artistas do candidato
        if not artistas_sp and artist_name:
            artistas_sp = [a for a in re.split(r',\s*', artist_name) if a.strip()]
        if artistas_sp:
            if not yt_artistas:
                return False
            principais = [_artista_limpo(artistas_sp[0])]
            if partes_origem:   # vídeo "Artista - Título" num canal qualquer
                principais.append(_artista_limpo(re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', partes_origem[0])))
            if not any(p and self.similaridade(p, _artista_limpo(a)) >= 0.8
                       for p in principais for a in yt_artistas):
                return False

        # 2) título praticamente igual (aceita o formato "Artista - Título" dos vídeos)
        if origem_yt:
            bases_origem = [_titulo_base(track_name, do_youtube=True)] + \
                           [_titulo_base(p, do_youtube=True) for p in partes_origem]
            bases_cand = [_titulo_base(yt_title)]
        else:
            bases_origem = [_titulo_base(track_name)]
            variantes = [yt_title] + (yt_title.split(' - ') if ' - ' in yt_title else [])
            bases_cand = [_titulo_base(v, do_youtube=True) for v in variantes]
        melhor = max(self.similaridade(a, b) for a in bases_origem for b in bases_cand)
        if melhor < (0.85 if artistas_sp else 0.9):
            return False

        # 3) mesma versão: nenhum lado pode ter live/remix/cover/instrumental... que o outro não tem
        texto_sp = _normalizar(track_name + ' ' + ' '.join(artistas_sp or []))
        texto_yt = _normalizar(yt_title + ' ' + ' '.join(yt_artistas))
        for grupo in _MODIFICADORES:
            if _tem_modificador(texto_sp, grupo) != _tem_modificador(texto_yt, grupo):
                return False

        # 4) duração parecida (quando os dois lados informam)
        if dur_sp and dur_yt and abs(dur_sp - dur_yt) > TOLERANCIA_DURACAO:
            return False

        return True

    def melhores_candidatos_spotify(self, titulo, artista, resultados, maximo=3):
        """Os resultados do Spotify que mais se parecem com a faixa do YouTube Music.
        (As chaves yt_title/yt_artist são as mesmas que o diálogo de aprovação já usa.)"""
        pontuados = []
        for r in resultados:
            sp_id = r.get('id')
            if not sp_id:
                continue
            nomes = [a.get('name', '') for a in (r.get('artists') or []) if a.get('name')]
            pontos = self.similaridade(titulo, r.get('name', '') or '')
            if artista and nomes:
                pontos += self.similaridade(artista, nomes[0])
            pontuados.append((pontos, {'sp_id': sp_id, 'yt_title': r.get('name', '') or '',
                                       'yt_artist': ', '.join(nomes)}))
        pontuados.sort(key=lambda par: par[0], reverse=True)
        return [c for _, c in pontuados[:maximo]]

    def processo_migracao(self, spotify_input, nome_playlist_destino, controle=None, arquivo_estado=None):
        controle = controle or ControleMigracao()
        ARQUIVO_ESTADO = None
        estado = None
        yt_playlist_id = None
        pendentes = []
        fila_lote = []        # músicas já escolhidas, ainda não enviadas
        enviar_ref = {}
        checkpoint_preparado = False
        try:
            if not nome_playlist_destino:
                nome_playlist_destino = 'Minha Playlist Importada'

            SPOTIFY_PLAYLIST_ID = _id_origem_spotify(spotify_input)
            if not SPOTIFY_PLAYLIST_ID:
                raise RuntimeError('Não entendi o link da playlist do Spotify. Cole o link completo ou o ID da playlist.')

            def salvar_estado(estado):
                _salvar_estado_migracao(ARQUIVO_ESTADO, estado)

            self.log('=' * 60)
            self.log('Conectando ao Spotify...', 'info')

            auth = carregar_auth_spotify()
            if auth is None or not auth.vinculado():
                raise RuntimeError("Spotify não vinculado. Clique em 'Vincular' no cartão do Spotify.")
            sp = spotipy.Spotify(auth_manager=auth, requests_timeout=30)

            self.log('🔍 Puxando músicas da playlist do Spotify...', 'info')
            tracks_info = []
            musicas_indisponiveis_spotify = []
            vistas_no_spotify = set()
            total_itens_raw = 0
            offset = 0

            try:
                while True:
                    controle.checar()
                    response = sp.playlist_items(SPOTIFY_PLAYLIST_ID, offset=offset, market='BR')
                    items = response.get('items', [])
                    if not items:
                        break

                    for obj in items:
                        total_itens_raw += 1
                        try:
                            track = obj.get('track') or obj.get('item')
                            if track and 'name' in track and track['name']:
                                name = track['name']
                                artists = ', '.join([a.get('name', '') for a in track.get('artists', []) if 'name' in a])
                                chave_unica = f'{name} - {artists}' if artists else name

                                if track.get('is_playable') is False:
                                    if chave_unica not in musicas_indisponiveis_spotify:
                                        musicas_indisponiveis_spotify.append(chave_unica)
                                    continue

                                # Identifica pela faixa do Spotify (ID); arquivos locais não têm ID
                                id_spotify = track.get('id')
                                chave_faixa = f'sp:{id_spotify}' if id_spotify else chave_unica

                                if chave_faixa in vistas_no_spotify:
                                    continue

                                vistas_no_spotify.add(chave_faixa)
                                lista_artistas = [a.get('name', '') for a in track.get('artists', [])
                                                  if a.get('name')]
                                duracao = (track.get('duration_ms') or 0) / 1000 or None
                                tracks_info.append({'name': name, 'artist': artists, 'full': chave_unica,
                                                    'chave': chave_faixa, 'artistas': lista_artistas,
                                                    'duracao': duracao})
                        except Exception:
                            pass

                    offset += len(items)
                    if not response.get('next'):
                        break
            except SpotifyException as e:
                if e.http_status in (401, 403, 404):
                    raise RuntimeError(
                        'O Spotify não deixou ler esta playlist. Causas comuns: o link/ID está errado, ou '
                        'a playlist é de outra pessoa (hoje o Spotify só libera o conteúdo de playlists '
                        'suas ou colaborativas). Dica: crie uma playlist sua, copie as músicas para ela '
                        'e use o link da cópia.'
                    )
                raise

            self.log(f'✅ Total no Spotify: {total_itens_raw} itens')
            self.log(f'🎵 Músicas válidas a migrar: {len(tracks_info)}')
            if musicas_indisponiveis_spotify:
                self.log(f'🚫 Indisponíveis/Cinzentas no Spotify: {len(musicas_indisponiveis_spotify)}\n')
            else:
                self.log('')

            if len(tracks_info) == 0:
                self.log('❌ Nenhuma música válida encontrada.', 'erro')
                self.ui(self._ui_progresso_reset)
                return

            controle.checar()
            yt = YTMusic(YT_AUTH_PATH)
            conta_destino = _conta_youtube(yt)
            ARQUIVO_ESTADO, estado, _novo_estado = _preparar_estado_migracao(
                'sp_yt', SPOTIFY_PLAYLIST_ID, nome_playlist_destino, conta_destino, arquivo_estado)
            yt_playlist_id = estado.get('playlist_id')
            criada_agora = False
            ids_destino = set()

            if not yt_playlist_id:
                self.log(f"🚀 Criando a playlist '{nome_playlist_destino}' no YouTube Music...", 'info')
                yt_playlist_id = yt.create_playlist(title=nome_playlist_destino, description='Importada via RHYFT')
                if not isinstance(yt_playlist_id, str):
                    raise RuntimeError(f'O YouTube Music recusou criar a playlist: {yt_playlist_id}')
                estado['playlist_id'] = yt_playlist_id
                criada_agora = True
            else:
                try:
                    destino = yt.get_playlist(yt_playlist_id, limit=None)
                except Exception as e:
                    raise RuntimeError(f'Não consegui conferir a playlist salva no YouTube Music: {explicar_erro(e)}') from e
                if not isinstance(destino, dict) or not isinstance(destino.get('tracks'), list):
                    raise RuntimeError('Não consegui conferir todas as faixas da playlist salva no YouTube Music.')
                if destino.get('owned') is False:
                    raise RuntimeError('A playlist salva não pode ser editada pela conta do YouTube conectada. Conecte a conta original.')
                autor = destino.get('author') or {}
                dono = autor.get('id') if isinstance(autor, dict) else None
                if estado.get('conta_destino') and not conta_destino and not (
                        destino.get('owned') is True and dono == estado['conta_destino']):
                    raise RuntimeError('Não consegui confirmar a conta do YouTube deste histórico. Tente novamente; o progresso foi preservado.')
                if dono and ((conta_destino and dono != conta_destino) or
                             (estado.get('conta_destino') and dono != estado['conta_destino'])):
                    raise RuntimeError('A playlist salva pertence a outra conta do YouTube. Conecte a conta original.')
                if destino.get('owned') is True and isinstance(dono, str) and dono:
                    estado['conta_destino'] = dono
                if isinstance(destino.get('title'), str) and destino['title']:
                    estado['nome_playlist'] = destino['title']
                ids_destino = {t.get('videoId') for t in destino['tracks'] if isinstance(t, dict) and t.get('videoId')}
                self.log('♻️ Playlist recuperada do histórico!', 'info')
            _reconciliar_estado_migracao(estado, ids_destino)
            _salvar_estado_migracao(ARQUIVO_ESTADO, estado, 'running')
            checkpoint_preparado = True

            ja_adicionadas = set(estado.get('adicionadas', []))
            musicas_com_erro = []
            video_ids_sessao = set(ids_destino)
            ids_confirmados = set(ids_destino)
            aliases_lote = {}
            erros_busca_seguidos = 0

            baseline = {'n': None, 'ok': True}

            def contar_playlist():
                """Quantidade de faixas na playlist (1 chamada leve). None se o YouTube não informar."""
                try:
                    n = yt.get_playlist(yt_playlist_id, limit=1).get('trackCount')
                    if isinstance(n, str):
                        n = re.sub(r'\D', '', n) or None
                    return int(n) if n is not None else None
                except Exception:
                    return None

            def ids_na_playlist():
                """Lê a playlist inteira no YouTube Music (confirmação 100% confiável)."""
                pl = yt.get_playlist(yt_playlist_id, limit=None)
                return {t.get('videoId') for t in pl.get('tracks', []) if t.get('videoId')}

            def verificar_presenca(itens):
                """Confere na playlist real quais itens apareceram. Devolve (presentes, ausentes)."""
                presentes, ausentes = [], list(itens)
                for tentativa in range(2):
                    try:
                        ids = ids_na_playlist()
                    except Exception:
                        registrar_erro_em_arquivo()
                        ids = None
                    if ids is not None:
                        presentes += [i for i in ausentes if i['video_id'] in ids]
                        ausentes = [i for i in ausentes if i['video_id'] not in ids]
                    if not ausentes:
                        break
                    if tentativa == 0:
                        controle.esperar(3, so_cancelamento=True)  # dá tempo do YouTube atualizar
                return presentes, ausentes

            def adicionar_um(video_id):
                ultimo_erro = None
                for _ in range(3):
                    try:
                        yt.add_playlist_items(yt_playlist_id, [video_id])
                        return True, None
                    except Exception as e:
                        ultimo_erro = e
                        if controle.esperar(3, so_cancelamento=True):
                            break
                return False, ultimo_erro

            def enviar_lote():
                """Envia a fila, CONFERE na playlist o que realmente entrou e só então grava no progresso.

                O YouTube às vezes responde 'sucesso' sem adicionar a música. Por isso nada vira
                'adicionada' sem aparecer na playlist; o que sumir é reenviado uma a uma.
                """
                itens = list(fila_lote)
                del fila_lote[:]
                if not itens:
                    return [], []
                ids = [i['video_id'] for i in itens]
                self.log(f'   📦 Enviando lote de {len(itens)} música(s)...', 'info')
                try:
                    yt.add_playlist_items(yt_playlist_id, ids)
                except Exception as e:
                    registrar_erro_em_arquivo()
                    self.log(f'   ⚠️ O lote deu erro ({explicar_erro(e)}). Vou conferir o que entrou...', 'aviso')

                # Conferência rápida: a contagem da playlist subiu exatamente o que enviei?
                depois = contar_playlist() if baseline['ok'] else None
                if depois is not None and baseline['n'] is not None and depois - baseline['n'] == len(ids):
                    confirmadas, falhas = itens, []
                    baseline['n'] = depois
                else:
                    # Conferência completa: lê a playlist e vê quais músicas estão lá de verdade
                    confirmadas, ausentes = verificar_presenca(itens)
                    falhas = []
                    if ausentes:
                        self.log(f'   ⚠️ {len(ausentes)} de {len(itens)} não apareceram na playlist. '
                                 'Reenviando uma a uma...', 'aviso')
                        reenviadas = []
                        for it in ausentes:
                            ok, erro = adicionar_um(it['video_id'])
                            if ok:
                                reenviadas.append(it)
                            elif erro:
                                self.log(f'      {explicar_erro(erro)}', 'cinza')
                        ids_reenviadas = {i['video_id'] for i in reenviadas}
                        falhas = [i for i in ausentes if i['video_id'] not in ids_reenviadas]
                        if reenviadas:
                            ok2, ausentes2 = verificar_presenca(reenviadas)
                            confirmadas += ok2
                            falhas += ausentes2
                    baseline['n'] = contar_playlist()
                    baseline['ok'] = baseline['n'] is not None

                for it in confirmadas:
                    video_ids_sessao.add(it['video_id'])
                    ids_confirmados.add(it['video_id'])
                    _registrar_faixa_migracao(estado, it['chave'], it['video_id'])
                    for alias in aliases_lote.pop(it['video_id'], []):
                        _registrar_faixa_migracao(estado, alias['chave'], it['video_id'])
                if confirmadas:
                    salvar_estado(estado)
                for it in falhas:
                    video_ids_sessao.discard(it['video_id'])
                    for alias in aliases_lote.pop(it['video_id'], []):
                        musicas_com_erro.append((alias['query'], 'Não confirmada na playlist do YouTube Music'))
                    musicas_com_erro.append((it['query'], 'Não confirmada na playlist do YouTube Music'))
                    self.log(f'   ❌ Não consegui confirmar: {it["query"]}', 'erro')

                self.log(f'   ✅ Lote conferido: {len(confirmadas)} de {len(itens)} confirmada(s) na playlist.',
                         'sucesso' if not falhas else 'aviso')
                if falhas:
                    controle.esperar(6, so_cancelamento=True)
                return confirmadas, falhas

            enviar_ref['fn'] = enviar_lote
            baseline['n'] = contar_playlist()
            if baseline['n'] is None and criada_agora:
                baseline['n'] = 0
            baseline['ok'] = baseline['n'] is not None

            self.log('\n🔎 Sincronizando faixas com o YouTube Music...')
            for i, item in enumerate(tracks_info, 1):
                controle.checar()
                query = item['full']
                chave = item['chave']
                track_name = item['name']
                artist_name = item['artist']
                self.ui(self._ui_progresso, i - 1, len(tracks_info), query)

                # 'query in ...' mantém compatível o progresso salvo por versões anteriores
                if chave in ja_adicionadas or query in ja_adicionadas:
                    if chave not in estado['mapeamento'] and query in estado['mapeamento']:
                        destino_id = estado['mapeamento'].pop(query)
                        _registrar_faixa_migracao(estado, chave, destino_id)
                        estado['adicionadas'] = list(estado['mapeamento'])
                        salvar_estado(estado)
                    self.log(f'[{i}/{len(tracks_info)}] ⏩ Já importada: {query}', 'aviso')
                    continue

                self.log(f'\n[{i}/{len(tracks_info)}] 🎵 Procurando: "{query}"')
                video_id = None
                ja_destino_id = None
                alias_pendente_id = None
                search_results = []
                vistos_busca = set()
                em_aprovacao = False

                try:
                    buscas = []
                    for termo in (f'{track_name} {artist_name}'.strip(), query, track_name):
                        if termo and termo not in buscas:
                            buscas.append(termo)

                    # Para na primeira busca que já tiver um resultado válido
                    for termo in buscas:
                        resultados = yt.search(termo, limit=5)
                        erros_busca_seguidos = 0
                        novos = [r for r in resultados
                                 if r.get('videoId') and r['videoId'] not in vistos_busca]
                        for r in novos:
                            vistos_busca.add(r['videoId'])
                        search_results.extend(novos)

                        for top_result in novos:
                            yt_title = top_result.get('title', '')
                            yt_artists = top_result.get('artists') or []
                            yt_artistas = [a.get('name', '') for a in yt_artists if a.get('name')]
                            if not yt_artistas and top_result.get('author'):
                                yt_artistas = [str(top_result['author'])]
                            yt_artist_name = ', '.join(yt_artistas)
                            candidate_id = top_result['videoId']

                            if self.validar_resultado(track_name, artist_name, yt_title, yt_artistas,
                                                      item.get('duracao'), top_result.get('duration_seconds'),
                                                      item.get('artistas')):
                                if candidate_id in video_ids_sessao:
                                    if candidate_id in ids_confirmados:
                                        ja_destino_id = candidate_id
                                    else:
                                        alias_pendente_id = candidate_id
                                else:
                                    video_id = candidate_id
                                    self.log(f'   🎯 Match automático -> "{yt_title}" - "{yt_artist_name}"', 'sucesso')
                                break
                        if video_id or ja_destino_id or alias_pendente_id:
                            break

                    if not video_id and not ja_destino_id and not alias_pendente_id and search_results:
                        candidatos = self.melhores_candidatos(track_name, artist_name, search_results)
                        pendentes.append({'query': query, 'chave': chave, 'candidatos': candidatos})
                        em_aprovacao = True
                        self.log(f'   🕒 Sem certeza ({len(candidatos)} opção(ões) parecida(s)): '
                                 'vai para a fila de aprovação, pergunto no final.', 'aviso')

                except Exception as e:
                    erros_busca_seguidos += 1
                    registrar_erro_em_arquivo()
                    self.log(f'   ⚠️ Erro ao buscar no YouTube Music: {explicar_erro(e)}', 'aviso')
                    if erros_busca_seguidos >= 5:
                        raise RuntimeError(
                            'Muitas falhas seguidas ao buscar no YouTube Music. A sessão pode ter expirado: '
                            "clique em 'Vincular' no cartão do YouTube Music e cole os cabeçalhos de novo. "
                            'O progresso foi salvo e a migração continua de onde parou.')

                if alias_pendente_id:
                    aliases_lote.setdefault(alias_pendente_id, []).append({'chave': chave, 'query': query})
                    self.log('   ⏩ Essa faixa já está na fila e será confirmada junto com o lote.', 'aviso')
                elif ja_destino_id:
                    _registrar_faixa_migracao(estado, chave, ja_destino_id)
                    salvar_estado(estado)
                    self.log('   ⏩ Essa faixa já está na playlist do YouTube Music.', 'aviso')
                elif video_id:
                    video_ids_sessao.add(video_id)  # reserva, para não repetir o mesmo vídeo no lote
                    fila_lote.append({'video_id': video_id, 'chave': chave, 'query': query})
                    self.log(f'   ➕ Na fila do lote ({len(fila_lote)}/{TAMANHO_LOTE})', 'sucesso')
                    if len(fila_lote) >= TAMANHO_LOTE:
                        enviar_lote()
                elif not em_aprovacao:
                    if not any(query == q for q, motivo in musicas_com_erro):
                        self.log('   ⚠️ Não encontrada no YouTube.', 'aviso')
                        musicas_com_erro.append((query, 'Não encontrada no YouTube Music'))

                controle.esperar(1.5)

            if fila_lote:
                enviar_lote()

            self.ui(self._ui_progresso_fim, f'Item {len(tracks_info)} de {len(tracks_info)}',
                    'Todas as faixas foram processadas.')

            controle.checar()
            if pendentes:
                self.ui(self._ui_travar_controles)
                self.log('\n' + '=' * 60)
                self.log(f'🕒 {len(pendentes)} faixa(s) precisam da sua aprovação manual '
                         '(as demais já foram adicionadas).', 'info')
                for n, p in enumerate(pendentes, 1):
                    prefixo = f'[{n}/{len(pendentes)}]'
                    self.ui(self._ui_progresso, n - 1, len(pendentes), p['query'], 'aprovacao')
                    opcoes = [c for c in p['candidatos'] if c['video_id'] not in video_ids_sessao]
                    if not opcoes:
                        self.log(f'{prefixo} ⏩ Essas versões já foram adicionadas por outra faixa: {p["query"]}', 'aviso')
                        musicas_com_erro.append((p['query'], 'Versão encontrada já estava na playlist'))
                        continue

                    escolhida = self.escolher_versao(n, len(pendentes), p['query'], opcoes)

                    if escolhida is None:
                        self.log(f'{prefixo} ❌ Rejeitada pelo usuário: {p["query"]}', 'erro')
                        musicas_com_erro.append((p['query'], 'Rejeitada pelo usuário'))
                        continue

                    video_ids_sessao.add(escolhida['video_id'])
                    fila_lote.append({'video_id': escolhida['video_id'], 'chave': p['chave'],
                                      'query': p['query']})
                    confirmadas, _falhas = enviar_lote()
                    if confirmadas:
                        self.log(f'{prefixo} 👉 Aprovada manualmente e adicionada: {p["query"]} '
                                 f'("{escolhida["yt_title"]}")', 'sucesso')
                        controle.esperar(1.5, so_cancelamento=True)
                    else:
                        self.log(f'{prefixo} ⚠️ Não consegui adicionar: {p["query"]}', 'erro')

            self.log('\n' + '=' * 60)
            concluida = not musicas_com_erro
            _salvar_estado_migracao(ARQUIVO_ESTADO, estado, 'completed' if concluida else 'interrupted')
            self.ui(self._ui_progresso_fim, 'Migração concluída' if concluida else 'Migração com pendências',
                    f'{len(estado["adicionadas"])} música(s) adicionada(s) à playlist.')
            self.log('🎉 PROCESSO FINALIZADO 🎉' if concluida else
                     '⚠️ Processo finalizado com pendências. Use Atualizar no histórico para tentar novamente.',
                     'sucesso' if concluida else 'aviso')
            self.log(f'📊 Total no Spotify: {total_itens_raw}')
            self.log(f'✅ Adicionadas: {len(estado["adicionadas"])}', 'sucesso')

            total_falhas = len(musicas_com_erro) + len(musicas_indisponiveis_spotify)
            if total_falhas > 0:
                self.log(f'⚠️ Erros/Ignoradas: {total_falhas}', 'erro')
            else:
                self.log('⚠️ Erros/Ignoradas: 0', 'sucesso')
            self.log('=' * 60 + '\n')

            if musicas_com_erro:
                self.log('📄 ITENS REJEITADOS OU NÃO ENCONTRADOS:', 'aviso')
                for item, motivo in musicas_com_erro:
                    self.log(f' - {item} ({motivo})', 'erro')

            if musicas_indisponiveis_spotify:
                self.log('\n🚫 MÚSICAS INDISPONÍVEIS (CINZAS) NO SPOTIFY:', 'cinza')
                for item in musicas_indisponiveis_spotify:
                    self.log(f' - {item} (Indisponível no Spotify)', 'cinza')

        except MigracaoPausada:
            if fila_lote and enviar_ref.get('fn'):
                try:
                    self.log('📦 Enviando as músicas já escolhidas antes de pausar...', 'info')
                    enviar_ref['fn']()
                except Exception:
                    registrar_erro_em_arquivo()
            if checkpoint_preparado:
                _salvar_estado_migracao(ARQUIVO_ESTADO, estado, 'interrupted')
            self.ui(self._ui_progresso_status, 'Migração pausada',
                    'O progresso foi salvo. Inicie de novo com o mesmo link de origem ou use o histórico.')
            if estado is None:
                self.log('\n⏸️ Migração pausada antes de começar a adicionar músicas. Nada foi criado.', 'aviso')
            else:
                self.log(f'\n⏸️ Migração PAUSADA. Progresso salvo: {len(estado["adicionadas"])} '
                         'música(s) já adicionada(s).', 'aviso')
                if pendentes:
                    self.log(f'   {len(pendentes)} faixa(s) que esperavam aprovação serão reavaliadas ao retomar.', 'cinza')
                self.log('▶ Para retomar, use o MESMO link de origem ou Atualizar no histórico.', 'info')
                self.log('   Você também pode iniciar outra migração agora; esta continua guardada.', 'info')
        except MigracaoCancelada:
            self.ui(self._ui_progresso_reset, 'Migração cancelada', 'O progresso foi apagado.')
            id_playlist = yt_playlist_id
            if ARQUIVO_ESTADO and os.path.exists(ARQUIVO_ESTADO):
                if not id_playlist:
                    id_playlist = (ler_json(ARQUIVO_ESTADO, {}) or {}).get('playlist_id')
                try:
                    os.remove(ARQUIVO_ESTADO)
                except OSError as e:
                    self.log(f'⚠️ Não consegui apagar o arquivo de progresso: {e}', 'erro')
            self.log('\n🛑 Migração CANCELADA. O arquivo de progresso foi apagado.', 'aviso')
            if id_playlist:
                self.log(f"ℹ️ A playlist '{nome_playlist_destino}' já criada continua no YouTube Music "
                         '(com as músicas que já tinham sido adicionadas). O app não a apagou: '
                         'se não quiser mais, remova-a por lá.', 'aviso')
        except Exception as e:
            registrar_erro_em_arquivo()
            if checkpoint_preparado:
                try:
                    _salvar_estado_migracao(ARQUIVO_ESTADO, estado, 'interrupted')
                except OSError:
                    registrar_erro_em_arquivo()
            self.ui(self._ui_progresso_status, 'Migração interrompida', 'Veja os detalhes no registro abaixo.')
            self.log(f'\n❌ Ocorreu um erro: {explicar_erro(e)}', 'erro')
        finally:
            self.ui(self._ui_migracao_terminada, controle)

    def processo_migracao_reversa(self, yt_input, nome_playlist_destino, controle=None, arquivo_estado=None):
        """YouTube Music -> Spotify. A playlist nova no Spotify é criada como privada."""
        controle = controle or ControleMigracao()
        ARQUIVO_ESTADO = None
        estado = None
        sp_playlist_id = None
        pendentes = []
        fila_lote = []        # faixas já escolhidas, ainda não enviadas
        enviar_ref = {}
        checkpoint_preparado = False
        try:
            id_yt = extrair_id_playlist_yt(yt_input)
            if not id_yt:
                raise RuntimeError('Não entendi o link da playlist do YouTube Music. Cole o endereço completo '
                                   '(music.youtube.com/playlist?list=...) ou só o código depois de "list=".')

            self.log('=' * 60)
            self.log('Conectando ao YouTube Music...', 'info')
            yt = YTMusic(YT_AUTH_PATH)
            entrada_lm = id_yt == 'LM'
            conta_origem = None

            self.log('🔍 Lendo as músicas da playlist do YouTube Music...', 'info')
            try:
                if id_yt == 'LM':
                    pl = yt.get_liked_songs(limit=None)
                else:
                    pl = yt.get_playlist(id_yt, limit=None)
            except Exception as e:
                registrar_erro_em_arquivo()
                raise RuntimeError(f'Não consegui ler a playlist do YouTube Music: {explicar_erro(e)}')

            if entrada_lm:
                # Android supplies the real account-specific likes playlist ID.
                # Desktop may keep literal LM. Bind it to a verified channel
                # when available; otherwise only explicit history selection
                # can reuse an unbound export, never names or cookies.
                id_real = pl.get('id')
                if isinstance(id_real, str) and id_real != 'LM' and re.fullmatch(r'[A-Za-z0-9_-]{10,200}', id_real):
                    id_yt = id_real
                else:
                    conta_origem = _conta_youtube(yt)
                    if not conta_origem and pl.get('owned') is True:
                        autor = pl.get('author') or {}
                        dono = autor.get('id') if isinstance(autor, dict) else None
                        if isinstance(dono, str) and dono:
                            conta_origem = dono
                    if not conta_origem:
                        self.log('ℹ️ A conta das músicas curtidas não informa um identificador confiável. '
                                 'Uma nova exportação cria uma playlist independente; para atualizar a existente, '
                                 'selecione-a no histórico.', 'aviso')

            titulo_origem = pl.get('title') or 'Playlist do YouTube Music'
            nome_playlist_destino = (nome_playlist_destino or titulo_origem)[:100]

            faixas, indisponiveis, vistos, total_itens = [], [], set(), 0
            for t in pl.get('tracks') or []:
                total_itens += 1
                titulo = (t.get('title') or '').strip()
                artistas = [a.get('name') for a in (t.get('artists') or []) if a and a.get('name')]
                nomes = ', '.join(artistas)
                legivel = f'{titulo} - {nomes}' if nomes else titulo
                video_id = t.get('videoId')
                if not video_id or t.get('isAvailable') is False or not titulo:
                    indisponiveis.append(legivel or '(sem título)')
                    continue
                if video_id in vistos:
                    continue
                vistos.add(video_id)
                tipo = t.get('videoType')
                faixas.append({'name': titulo, 'artistas': artistas, 'artist': nomes, 'full': legivel,
                               'chave': f'yt:{video_id}', 'duracao': t.get('duration_seconds'),
                               'eh_video': bool(tipo) and tipo != 'MUSIC_VIDEO_TYPE_ATV'})

            self.log(f'✅ Total no YouTube Music: {total_itens} itens')
            self.log(f'🎵 Músicas válidas a migrar: {len(faixas)}')
            if indisponiveis:
                self.log(f'🚫 Indisponíveis no YouTube Music: {len(indisponiveis)}\n')
            else:
                self.log('')
            if not faixas:
                self.log('❌ Nenhuma música válida encontrada.', 'erro')
                self.ui(self._ui_progresso_reset)
                return

            def salvar_estado(e):
                _salvar_estado_migracao(ARQUIVO_ESTADO, e)

            controle.checar()
            self.log('Conectando ao Spotify...', 'info')
            auth = carregar_auth_spotify()
            if auth is None or not auth.vinculado():
                raise RuntimeError("Spotify não vinculado. Clique em 'Vincular' no cartão do Spotify.")
            if not auth.tem_escrita():
                raise RuntimeError(MSG_REAUTORIZAR.replace('\n\n', ' '))
            sp = spotipy.Spotify(auth_manager=auth, requests_timeout=30, retries=5)

            conta_destino = _conta_spotify(sp)
            ARQUIVO_ESTADO, estado, _novo_estado = _preparar_estado_migracao(
                'yt_sp', id_yt, nome_playlist_destino, conta_destino, arquivo_estado,
                conta_origem=conta_origem, origem_input='LM' if entrada_lm else None)
            sp_playlist_id = estado.get('playlist_id')
            criada_agora = False

            if not sp_playlist_id:
                self.log(f"🚀 Criando a playlist '{nome_playlist_destino}' no Spotify (privada)...", 'info')
                try:
                    criada = sp._post('me/playlists', payload={
                        'name': nome_playlist_destino, 'public': False,
                        'description': 'Importada via RHYFT'})
                except SpotifyException as e:
                    raise RuntimeError(explicar_erro_spotify(e))
                sp_playlist_id = (criada or {}).get('id')
                if not sp_playlist_id:
                    raise RuntimeError(f'O Spotify recusou criar a playlist: {criada}')
                estado['playlist_id'] = sp_playlist_id
                criada_agora = True
            else:
                if estado.get('conta_destino') and not conta_destino:
                    raise RuntimeError('Não consegui confirmar a conta do Spotify deste histórico. Tente novamente; o progresso foi preservado.')
                try:
                    destino = sp._get(f'playlists/{sp_playlist_id}')
                except Exception as e:
                    raise RuntimeError(f'Não consegui conferir a playlist salva no Spotify: {explicar_erro_spotify(e)}') from e
                dono = (destino.get('owner') or {}).get('id')
                if dono and conta_destino and dono != conta_destino and not destino.get('collaborative'):
                    raise RuntimeError('A playlist salva pertence a outra conta do Spotify. Conecte a conta original.')
                if isinstance(destino.get('name'), str) and destino['name']:
                    estado['nome_playlist'] = destino['name']
                self.log('♻️ Playlist recuperada do histórico!', 'info')

            # ---------- conferência do que realmente está na playlist do Spotify ----------
            baseline = {'n': None, 'ok': True}

            def contar_playlist():
                try:
                    n = sp._get(f'playlists/{sp_playlist_id}/items', limit=1).get('total')
                    return int(n) if n is not None else None
                except Exception:
                    return None

            def ids_na_playlist():
                ids, offset = set(), 0
                while True:
                    r = sp._get(f'playlists/{sp_playlist_id}/items', limit=50, offset=offset)
                    itens = r.get('items') or []
                    for o in itens:
                        f = o.get('item') or o.get('track') or {}
                        if f.get('id'):
                            ids.add(f['id'])
                    offset += len(itens)
                    if not itens or not r.get('next'):
                        break
                return ids

            def verificar_presenca(itens):
                presentes, ausentes = [], list(itens)
                for tentativa in range(2):
                    try:
                        ids = ids_na_playlist()
                    except Exception:
                        registrar_erro_em_arquivo()
                        ids = None
                    if ids is not None:
                        presentes += [i for i in ausentes if i['sp_id'] in ids]
                        ausentes = [i for i in ausentes if i['sp_id'] not in ids]
                    if not ausentes:
                        break
                    if tentativa == 0:
                        controle.esperar(3, so_cancelamento=True)
                return presentes, ausentes

            def postar_itens(sp_ids):
                sp._post(f'playlists/{sp_playlist_id}/items',
                         payload={'uris': [f'spotify:track:{i}' for i in sp_ids]})

            def adicionar_um(sp_id):
                ultimo_erro = None
                for _ in range(3):
                    try:
                        postar_itens([sp_id])
                        return True, None
                    except Exception as e:
                        ultimo_erro = e
                        if controle.esperar(3, so_cancelamento=True):
                            break
                return False, ultimo_erro

            def erro_legivel(e):
                return explicar_erro_spotify(e) if isinstance(e, SpotifyException) else explicar_erro(e)

            ids_sessao = set()
            if not criada_agora:
                # retomada: o que já está na playlist não pode ser adicionado de novo (o Spotify aceita duplicadas)
                try:
                    ids_sessao |= ids_na_playlist()
                except SpotifyException as e:
                    raise RuntimeError(explicar_erro_spotify(e))
            _reconciliar_estado_migracao(estado, ids_sessao)
            _salvar_estado_migracao(ARQUIVO_ESTADO, estado, 'running')
            checkpoint_preparado = True
            ids_confirmados = set(ids_sessao)
            aliases_lote = {}
            musicas_com_erro, repetidas = [], []

            def enviar_lote():
                """Envia a fila, CONFERE na playlist o que realmente entrou e só então grava no progresso."""
                itens = list(fila_lote)
                del fila_lote[:]
                if not itens:
                    return [], []
                ids = [i['sp_id'] for i in itens]
                self.log(f'   📦 Enviando lote de {len(itens)} música(s)...', 'info')
                try:
                    postar_itens(ids)
                except Exception as e:
                    registrar_erro_em_arquivo()
                    self.log(f'   ⚠️ O lote deu erro ({erro_legivel(e)}). Vou conferir o que entrou...', 'aviso')

                depois = contar_playlist() if baseline['ok'] else None
                if depois is not None and baseline['n'] is not None and depois - baseline['n'] == len(ids):
                    confirmadas, falhas = itens, []
                    baseline['n'] = depois
                else:
                    confirmadas, ausentes = verificar_presenca(itens)
                    falhas = []
                    if ausentes:
                        self.log(f'   ⚠️ {len(ausentes)} de {len(itens)} não apareceram na playlist. '
                                 'Reenviando uma a uma...', 'aviso')
                        reenviadas = []
                        for it in ausentes:
                            ok, erro = adicionar_um(it['sp_id'])
                            if ok:
                                reenviadas.append(it)
                            elif erro:
                                self.log(f'      {erro_legivel(erro)}', 'cinza')
                        ids_reenv = {i['sp_id'] for i in reenviadas}
                        falhas = [i for i in ausentes if i['sp_id'] not in ids_reenv]
                        if reenviadas:
                            ok2, ausentes2 = verificar_presenca(reenviadas)
                            confirmadas += ok2
                            falhas += ausentes2
                    baseline['n'] = contar_playlist()
                    baseline['ok'] = baseline['n'] is not None

                for it in confirmadas:
                    ids_sessao.add(it['sp_id'])
                    ids_confirmados.add(it['sp_id'])
                    _registrar_faixa_migracao(estado, it['chave'], it['sp_id'])
                    for alias in aliases_lote.pop(it['sp_id'], []):
                        _registrar_faixa_migracao(estado, alias['chave'], it['sp_id'])
                if confirmadas:
                    salvar_estado(estado)
                for it in falhas:
                    ids_sessao.discard(it['sp_id'])
                    for alias in aliases_lote.pop(it['sp_id'], []):
                        musicas_com_erro.append((alias['query'], 'Não confirmada na playlist do Spotify'))
                    musicas_com_erro.append((it['query'], 'Não confirmada na playlist do Spotify'))
                    self.log(f'   ❌ Não consegui confirmar: {it["query"]}', 'erro')
                self.log(f'   ✅ Lote conferido: {len(confirmadas)} de {len(itens)} confirmada(s) na playlist.',
                         'sucesso' if not falhas else 'aviso')
                if falhas:
                    controle.esperar(6, so_cancelamento=True)
                return confirmadas, falhas

            enviar_ref['fn'] = enviar_lote
            baseline['n'] = contar_playlist()
            if baseline['n'] is None and criada_agora:
                baseline['n'] = 0
            baseline['ok'] = baseline['n'] is not None

            ja_adicionadas = set(estado.get('adicionadas', []))
            ja_puladas = set(estado.get('puladas', []))
            erros_busca_seguidos = 0

            self.log('\n🔎 Procurando as faixas no Spotify...')
            for i, item in enumerate(faixas, 1):
                controle.checar()
                query = item['full']
                chave = item['chave']
                track_name = item['name']
                self.ui(self._ui_progresso, i - 1, len(faixas), query)

                if chave in ja_adicionadas or chave in ja_puladas:
                    self.log(f'[{i}/{len(faixas)}] ⏩ Já importada: {query}', 'aviso')
                    continue

                self.log(f'\n[{i}/{len(faixas)}] 🎵 Procurando: "{query}"')
                sp_id = None
                duplicada = False
                duplicada_id = None
                alias_pendente_id = None
                encontrados = []
                vistos_busca = set()
                em_aprovacao = False

                titulo_busca, artista_extra = _titulo_para_busca(track_name, item['artistas'], item['eh_video'])
                artista_busca = artista_extra or (_nome_artista_busca(item['artistas'][0]) if item['artistas'] else '')

                try:
                    consultas = []
                    for q in ((f'track:"{titulo_busca}" artist:"{artista_busca}"' if artista_busca
                               else f'track:"{titulo_busca}"'),
                              f'{titulo_busca} {artista_busca}'.strip(), titulo_busca):
                        if q and q not in consultas:
                            consultas.append(q)

                    for q in consultas:
                        resposta = sp.search(q=q, type='track', limit=10, market=SPOTIFY_MARKET)
                        erros_busca_seguidos = 0
                        novos = [r for r in ((resposta.get('tracks') or {}).get('items') or [])
                                 if r and r.get('id') and r['id'] not in vistos_busca
                                 and r.get('is_playable') is not False]
                        for r in novos:
                            vistos_busca.add(r['id'])
                        encontrados.extend(novos)

                        for r in novos:
                            nomes_sp = [a.get('name', '') for a in (r.get('artists') or []) if a.get('name')]
                            dur_sp_cand = (r.get('duration_ms') or 0) / 1000 or None
                            if self.validar_resultado(track_name, item['artist'], r.get('name', ''), nomes_sp,
                                                      item.get('duracao'), dur_sp_cand, item['artistas'],
                                                      origem_yt=True):
                                if r['id'] in ids_sessao:
                                    if r['id'] in ids_confirmados:
                                        duplicada = True
                                        duplicada_id = r['id']
                                    else:
                                        alias_pendente_id = r['id']
                                else:
                                    sp_id = r['id']
                                    self.log(f'   🎯 Match automático -> "{r.get("name", "")}" - "{", ".join(nomes_sp)}"',
                                             'sucesso')
                                break
                        if sp_id or duplicada or alias_pendente_id:
                            break

                    if not sp_id and not duplicada and not alias_pendente_id and encontrados:
                        candidatos = self.melhores_candidatos_spotify(titulo_busca, artista_busca, encontrados)
                        pendentes.append({'query': query, 'chave': chave, 'candidatos': candidatos})
                        em_aprovacao = True
                        self.log(f'   🕒 Sem certeza ({len(candidatos)} opção(ões) parecida(s)): '
                                 'vai para a fila de aprovação, pergunto no final.', 'aviso')

                except Exception as e:
                    registrar_erro_em_arquivo()
                    erros_busca_seguidos += 1
                    status = getattr(e, 'http_status', None)
                    self.log(f'   ⚠️ Erro ao buscar no Spotify: {erro_legivel(e)}', 'aviso')
                    if status == 401:
                        raise RuntimeError(explicar_erro_spotify(e))
                    if status == 429:
                        self.log('   ⏳ O Spotify pediu calma. Esperando 20 segundos...', 'aviso')
                        controle.esperar(20, so_cancelamento=True)
                    if erros_busca_seguidos >= 5:
                        raise RuntimeError(
                            'Muitas falhas seguidas ao buscar no Spotify. Veja a mensagem acima '
                            '(pode ser limite de requisições ou a sessão expirada). '
                            'O progresso foi salvo e a migração continua de onde parou.')

                if alias_pendente_id:
                    aliases_lote.setdefault(alias_pendente_id, []).append({'chave': chave, 'query': query})
                    self.log('   ⏩ Essa faixa já está na fila e será confirmada junto com o lote.', 'aviso')
                elif sp_id:
                    ids_sessao.add(sp_id)  # reserva, para não repetir a mesma faixa no lote
                    fila_lote.append({'sp_id': sp_id, 'chave': chave, 'query': query})
                    self.log(f'   ➕ Na fila do lote ({len(fila_lote)}/{TAMANHO_LOTE})', 'sucesso')
                    if len(fila_lote) >= TAMANHO_LOTE:
                        enviar_lote()
                elif duplicada:
                    self.log('   ⏩ Essa faixa já está na playlist do Spotify (repetida no YouTube Music).', 'aviso')
                    repetidas.append(query)
                    _registrar_faixa_migracao(estado, chave, duplicada_id)
                    salvar_estado(estado)
                elif not em_aprovacao:
                    if not any(query == q for q, motivo in musicas_com_erro):
                        self.log('   ⚠️ Não encontrada no Spotify.', 'aviso')
                        musicas_com_erro.append((query, 'Não encontrada no Spotify'))

                controle.esperar(PAUSA_BUSCA_SPOTIFY)

            if fila_lote:
                enviar_lote()

            self.ui(self._ui_progresso_fim, f'Item {len(faixas)} de {len(faixas)}',
                    'Todas as faixas foram processadas.')

            controle.checar()
            if pendentes:
                self.ui(self._ui_travar_controles)
                self.log('\n' + '=' * 60)
                self.log(f'🕒 {len(pendentes)} faixa(s) precisam da sua aprovação manual '
                         '(as demais já foram adicionadas).', 'info')
                for n, p in enumerate(pendentes, 1):
                    prefixo = f'[{n}/{len(pendentes)}]'
                    self.ui(self._ui_progresso, n - 1, len(pendentes), p['query'], 'aprovacao')
                    opcoes = [c for c in p['candidatos'] if c['sp_id'] not in ids_sessao]
                    if not opcoes:
                        self.log(f'{prefixo} ⏩ Essas versões já estão na playlist: {p["query"]}', 'aviso')
                        musicas_com_erro.append((p['query'], 'Versão encontrada já estava na playlist'))
                        continue

                    escolhida = self.escolher_versao(n, len(pendentes), p['query'], opcoes,
                                                     origem='YouTube Music')
                    if escolhida is None:
                        self.log(f'{prefixo} ❌ Rejeitada pelo usuário: {p["query"]}', 'erro')
                        musicas_com_erro.append((p['query'], 'Rejeitada pelo usuário'))
                        continue

                    ids_sessao.add(escolhida['sp_id'])
                    fila_lote.append({'sp_id': escolhida['sp_id'], 'chave': p['chave'], 'query': p['query']})
                    confirmadas, _falhas = enviar_lote()
                    if confirmadas:
                        self.log(f'{prefixo} 👉 Aprovada manualmente e adicionada: {p["query"]} '
                                 f'("{escolhida["yt_title"]}")', 'sucesso')
                        controle.esperar(0.5, so_cancelamento=True)
                    else:
                        self.log(f'{prefixo} ⚠️ Não consegui adicionar: {p["query"]}', 'erro')

            self.log('\n' + '=' * 60)
            concluida = not musicas_com_erro
            _salvar_estado_migracao(ARQUIVO_ESTADO, estado, 'completed' if concluida else 'interrupted')
            self.ui(self._ui_progresso_fim, 'Migração concluída' if concluida else 'Migração com pendências',
                    f'{len(estado["adicionadas"])} música(s) adicionada(s) à playlist.')
            self.log('🎉 PROCESSO FINALIZADO 🎉' if concluida else
                     '⚠️ Processo finalizado com pendências. Use Atualizar no histórico para tentar novamente.',
                     'sucesso' if concluida else 'aviso')
            self.log(f'📊 Total no YouTube Music: {total_itens}')
            self.log(f'✅ Adicionadas: {len(estado["adicionadas"])}', 'sucesso')
            if repetidas:
                self.log(f'⏩ Repetidas (já estavam na playlist): {len(repetidas)}', 'aviso')
            total_falhas = len(musicas_com_erro) + len(indisponiveis)
            if total_falhas > 0:
                self.log(f'⚠️ Erros/Ignoradas: {total_falhas}', 'erro')
            else:
                self.log('⚠️ Erros/Ignoradas: 0', 'sucesso')
            self.log('=' * 60 + '\n')

            if musicas_com_erro:
                self.log('📄 ITENS REJEITADOS OU NÃO ENCONTRADOS:', 'aviso')
                for item_erro, motivo in musicas_com_erro:
                    self.log(f' - {item_erro} ({motivo})', 'erro')
            if indisponiveis:
                self.log('\n🚫 MÚSICAS INDISPONÍVEIS NO YOUTUBE MUSIC:', 'cinza')
                for item_ind in indisponiveis:
                    self.log(f' - {item_ind} (Indisponível no YouTube Music)', 'cinza')

        except MigracaoPausada:
            if fila_lote and enviar_ref.get('fn'):
                try:
                    self.log('📦 Enviando as músicas já escolhidas antes de pausar...', 'info')
                    enviar_ref['fn']()
                except Exception:
                    registrar_erro_em_arquivo()
            if checkpoint_preparado:
                _salvar_estado_migracao(ARQUIVO_ESTADO, estado, 'interrupted')
            self.ui(self._ui_progresso_status, 'Migração pausada',
                    'O progresso foi salvo. Inicie de novo com o mesmo link de origem ou use o histórico.')
            if estado is None or not sp_playlist_id:
                self.log('\n⏸️ Migração pausada antes de começar a adicionar músicas. Nada foi criado.', 'aviso')
            else:
                self.log(f'\n⏸️ Migração PAUSADA. Progresso salvo: {len(estado["adicionadas"])} '
                         'música(s) já adicionada(s).', 'aviso')
                if pendentes:
                    self.log(f'   {len(pendentes)} faixa(s) que esperavam aprovação serão reavaliadas ao retomar.', 'cinza')
                self.log('▶ Para retomar, use o MESMO link de origem ou Atualizar no histórico.', 'info')
        except MigracaoCancelada:
            self.ui(self._ui_progresso_reset, 'Migração cancelada', 'O progresso foi apagado.')
            if ARQUIVO_ESTADO and os.path.exists(ARQUIVO_ESTADO):
                try:
                    os.remove(ARQUIVO_ESTADO)
                except OSError as e:
                    self.log(f'⚠️ Não consegui apagar o arquivo de progresso: {e}', 'erro')
            self.log('\n🛑 Migração CANCELADA. O arquivo de progresso foi apagado.', 'aviso')
            if sp_playlist_id:
                self.log(f"ℹ️ A playlist '{nome_playlist_destino}' já criada continua no Spotify "
                         '(com as músicas que já tinham sido adicionadas). O app não a apagou: '
                         'se não quiser mais, remova-a por lá.', 'aviso')
        except Exception as e:
            registrar_erro_em_arquivo()
            if checkpoint_preparado:
                try:
                    _salvar_estado_migracao(ARQUIVO_ESTADO, estado, 'interrupted')
                except OSError:
                    registrar_erro_em_arquivo()
            self.ui(self._ui_progresso_status, 'Migração interrompida', 'Veja os detalhes no registro abaixo.')
            if isinstance(e, RuntimeError):      # já vem com a mensagem pronta
                msg_erro = str(e)
            elif isinstance(e, SpotifyException):
                msg_erro = explicar_erro_spotify(e)
            else:
                msg_erro = explicar_erro(e)
            self.log(f'\n❌ Ocorreu um erro: {msg_erro}', 'erro')
        finally:
            self.ui(self._ui_migracao_terminada, controle)
