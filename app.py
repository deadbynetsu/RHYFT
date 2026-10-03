# -*- coding: utf-8 -*-
"""
Migrador de Playlists: Spotify -> YouTube Music

Cada pessoa usa as PRÓPRIAS contas:
  - Spotify: cria o próprio app no painel de desenvolvedores (grátis) e cola só o
    Client ID. A autorização usa PKCE, então NÃO existe Client Secret no programa.
  - YouTube Music: cola os cabeçalhos da requisição copiados do navegador. O app
    limpa, valida (faz uma chamada de teste) e só então salva.

Nada de credenciais é embutido no código. Tudo fica em %APPDATA%\MigradorPlaylists.
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

# ============================== CAMINHOS & CONFIGURAÇÕES ======================
APP_NAME = 'MigradorPlaylists'
APP_VERSION = '1.3.1'
TAMANHO_LOTE = 10          # quantas músicas por envio ao YouTube Music (cada lote é conferido depois)
TOLERANCIA_DURACAO = 15    # segundos de diferença aceitos entre Spotify e YouTube
PAUSA_BUSCA_SPOTIFY = 0.4  # segundos entre uma busca e outra no Spotify (YouTube ➔ Spotify)
SPOTIFY_MARKET = 'BR'      # o país da conta tem prioridade quando o token é de usuário
GITHUB_REPO = 'deadbynetsu/Spotify-Youtube-Music-Playlists-Migrator'  # "usuario/repositorio" onde ficam as Releases

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def obter_pasta_dados():
    """Pasta gravável por usuário (funciona mesmo se o .exe estiver em Program Files)."""
    base = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'), '.config')
    pasta = os.path.join(base, APP_NAME)
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
    '<html><head><meta charset="utf-8"><title>Migrador de Playlists</title></head>'
    '<body style="font-family:Segoe UI,Arial,sans-serif;text-align:center;margin-top:15%">'
    '<h2>\u2705 Spotify vinculado!</h2>'
    '<p>Pode fechar esta aba e voltar ao aplicativo.</p></body></html>'
)
PAGINA_ERRO = (
    '<html><head><meta charset="utf-8"><title>Migrador de Playlists</title></head>'
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
        if not webbrowser.open(url_autorizacao):
            raise RuntimeError('Não consegui abrir o navegador padrão do computador.')
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

    def autorizar(self, timeout=180, cancelar=None):
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
        codigo = aguardar_codigo_spotify(url, state, timeout, cancelar)
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
                'Espere alguns minutos e retome pelo mesmo nome de playlist.')
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
            headers={'User-Agent': 'MigradorPlaylistsApp', 'Accept': 'application/vnd.github+json'})
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
class MigradorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        definir_fontes(self)

        self.title(f'Migrador de Playlists (v{APP_VERSION})')
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
        ctk.CTkLabel(cab, text='Migrador de Playlists', font=fonte(22, 'bold'),
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
        btn_desvincular.pack(side='left')

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

        btn_salvar.configure(command=salvar)
        btn_desvincular.configure(command=desvincular)

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

    def processo_migracao(self, spotify_input, nome_playlist_destino, controle=None):
        controle = controle or ControleMigracao()
        ARQUIVO_ESTADO = None
        estado = None
        yt_playlist_id = None
        pendentes = []
        fila_lote = []        # músicas já escolhidas, ainda não enviadas
        enviar_ref = {}
        try:
            if not nome_playlist_destino:
                nome_playlist_destino = 'Minha Playlist Importada'

            if 'spotify.com/playlist/' in spotify_input:
                match = re.search(r'playlist/([a-zA-Z0-9]+)', spotify_input)
                SPOTIFY_PLAYLIST_ID = match.group(1) if match else spotify_input.split('/')[-1].split('?')[0]
            else:
                SPOTIFY_PLAYLIST_ID = spotify_input

            nome_arquivo_seguro = re.sub(r'[\\/*?:"<>|]', '', nome_playlist_destino).strip().replace(' ', '_') or 'playlist'
            ARQUIVO_ESTADO = os.path.join(DATA_DIR, f'progresso_{nome_arquivo_seguro}.json')

            def carregar_estado():
                estado = ler_json(ARQUIVO_ESTADO, None)
                if isinstance(estado, dict):
                    estado.setdefault('playlist_id', None)
                    estado.setdefault('adicionadas', [])
                    estado.setdefault('videos', [])
                    return estado
                return {'playlist_id': None, 'adicionadas': [], 'videos': []}

            def salvar_estado(estado):
                gravar_json(ARQUIVO_ESTADO, estado)

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
            estado = carregar_estado()
            yt_playlist_id = estado.get('playlist_id')
            criada_agora = False

            if not yt_playlist_id:
                self.log(f"🚀 Criando a playlist '{nome_playlist_destino}' no YouTube Music...", 'info')
                yt_playlist_id = yt.create_playlist(title=nome_playlist_destino, description='Importada via Migrador de Playlists')
                if not isinstance(yt_playlist_id, str):
                    raise RuntimeError(f'O YouTube Music recusou criar a playlist: {yt_playlist_id}')
                estado['playlist_id'] = yt_playlist_id
                salvar_estado(estado)
                criada_agora = True
            else:
                self.log('♻️ Playlist recuperada do histórico!', 'info')

            ja_adicionadas = set(estado.get('adicionadas', []))
            musicas_com_erro = []
            video_ids_sessao = set(estado.get('videos', []))
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
                    estado['videos'].append(it['video_id'])
                    estado['adicionadas'].append(it['chave'])
                if confirmadas:
                    salvar_estado(estado)
                for it in falhas:
                    video_ids_sessao.discard(it['video_id'])
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
                    self.log(f'[{i}/{len(tracks_info)}] ⏩ Já importada: {query}', 'aviso')
                    continue

                self.log(f'\n[{i}/{len(tracks_info)}] 🎵 Procurando: "{query}"')
                video_id = None
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

                            if candidate_id in video_ids_sessao:
                                continue

                            if self.validar_resultado(track_name, artist_name, yt_title, yt_artistas,
                                                      item.get('duracao'), top_result.get('duration_seconds'),
                                                      item.get('artistas')):
                                video_id = candidate_id
                                self.log(f'   🎯 Match automático -> "{yt_title}" - "{yt_artist_name}"', 'sucesso')
                                break
                        if video_id:
                            break

                    if not video_id and search_results:
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

                if video_id:
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
            self.ui(self._ui_progresso_fim, 'Migração concluída',
                    f'{len(estado["adicionadas"])} música(s) adicionada(s) à playlist.')
            self.log('🎉 PROCESSO FINALIZADO 🎉', 'sucesso')
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
            self.ui(self._ui_progresso_status, 'Migração pausada',
                    'O progresso foi salvo. Inicie de novo com o mesmo nome de playlist para retomar.')
            if estado is None:
                self.log('\n⏸️ Migração pausada antes de começar a adicionar músicas. Nada foi criado.', 'aviso')
            else:
                self.log(f'\n⏸️ Migração PAUSADA. Progresso salvo: {len(estado["adicionadas"])} '
                         'música(s) já adicionada(s).', 'aviso')
                if pendentes:
                    self.log(f'   {len(pendentes)} faixa(s) que esperavam aprovação serão reavaliadas ao retomar.', 'cinza')
                self.log(f"▶ Para retomar, inicie de novo com o MESMO nome de playlist ('{nome_playlist_destino}').", 'info')
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
            self.ui(self._ui_progresso_status, 'Migração interrompida', 'Veja os detalhes no registro abaixo.')
            self.log(f'\n❌ Ocorreu um erro: {explicar_erro(e)}', 'erro')
        finally:
            self.ui(self._ui_migracao_terminada, controle)


    def processo_migracao_reversa(self, yt_input, nome_playlist_destino, controle=None):
        """YouTube Music -> Spotify. A playlist nova no Spotify é criada como privada."""
        controle = controle or ControleMigracao()
        ARQUIVO_ESTADO = None
        estado = None
        sp_playlist_id = None
        pendentes = []
        fila_lote = []        # faixas já escolhidas, ainda não enviadas
        enviar_ref = {}
        try:
            id_yt = extrair_id_playlist_yt(yt_input)
            if not id_yt:
                raise RuntimeError('Não entendi o link da playlist do YouTube Music. Cole o endereço completo '
                                   '(music.youtube.com/playlist?list=...) ou só o código depois de "list=".')

            self.log('=' * 60)
            self.log('Conectando ao YouTube Music...', 'info')
            yt = YTMusic(YT_AUTH_PATH)

            self.log('🔍 Lendo as músicas da playlist do YouTube Music...', 'info')
            try:
                if id_yt == 'LM':
                    pl = yt.get_liked_songs(limit=None)
                else:
                    pl = yt.get_playlist(id_yt, limit=None)
            except Exception as e:
                registrar_erro_em_arquivo()
                raise RuntimeError(f'Não consegui ler a playlist do YouTube Music: {explicar_erro(e)}')

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

            nome_arquivo_seguro = re.sub(r'[\\/*?:"<>|]', '', nome_playlist_destino).strip().replace(' ', '_') or 'playlist'
            ARQUIVO_ESTADO = os.path.join(DATA_DIR, f'progresso_yt-sp_{nome_arquivo_seguro}.json')

            def carregar_estado():
                e = ler_json(ARQUIVO_ESTADO, None)
                if isinstance(e, dict):
                    e.setdefault('playlist_id', None)
                    e.setdefault('adicionadas', [])
                    e.setdefault('destino_ids', [])
                    e.setdefault('puladas', [])
                    return e
                return {'playlist_id': None, 'adicionadas': [], 'destino_ids': [], 'puladas': []}

            def salvar_estado(e):
                gravar_json(ARQUIVO_ESTADO, e)

            controle.checar()
            self.log('Conectando ao Spotify...', 'info')
            auth = carregar_auth_spotify()
            if auth is None or not auth.vinculado():
                raise RuntimeError("Spotify não vinculado. Clique em 'Vincular' no cartão do Spotify.")
            if not auth.tem_escrita():
                raise RuntimeError(MSG_REAUTORIZAR.replace('\n\n', ' '))
            sp = spotipy.Spotify(auth_manager=auth, requests_timeout=30, retries=5)

            estado = carregar_estado()
            sp_playlist_id = estado.get('playlist_id')
            criada_agora = False

            if not sp_playlist_id:
                self.log(f"🚀 Criando a playlist '{nome_playlist_destino}' no Spotify (privada)...", 'info')
                try:
                    criada = sp._post('me/playlists', payload={
                        'name': nome_playlist_destino, 'public': False,
                        'description': 'Importada via Migrador de Playlists'})
                except SpotifyException as e:
                    raise RuntimeError(explicar_erro_spotify(e))
                sp_playlist_id = (criada or {}).get('id')
                if not sp_playlist_id:
                    raise RuntimeError(f'O Spotify recusou criar a playlist: {criada}')
                estado['playlist_id'] = sp_playlist_id
                salvar_estado(estado)
                criada_agora = True
            else:
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

            ids_sessao = set(estado.get('destino_ids', []))
            if not criada_agora:
                # retomada: o que já está na playlist não pode ser adicionado de novo (o Spotify aceita duplicadas)
                try:
                    ids_sessao |= ids_na_playlist()
                except SpotifyException as e:
                    raise RuntimeError(explicar_erro_spotify(e))
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
                    estado['destino_ids'].append(it['sp_id'])
                    estado['adicionadas'].append(it['chave'])
                if confirmadas:
                    salvar_estado(estado)
                for it in falhas:
                    ids_sessao.discard(it['sp_id'])
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
                                    duplicada = True
                                else:
                                    sp_id = r['id']
                                    self.log(f'   🎯 Match automático -> "{r.get("name", "")}" - "{", ".join(nomes_sp)}"',
                                             'sucesso')
                                break
                        if sp_id or duplicada:
                            break

                    if not sp_id and not duplicada and encontrados:
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

                if sp_id:
                    ids_sessao.add(sp_id)  # reserva, para não repetir a mesma faixa no lote
                    fila_lote.append({'sp_id': sp_id, 'chave': chave, 'query': query})
                    self.log(f'   ➕ Na fila do lote ({len(fila_lote)}/{TAMANHO_LOTE})', 'sucesso')
                    if len(fila_lote) >= TAMANHO_LOTE:
                        enviar_lote()
                elif duplicada:
                    self.log('   ⏩ Essa faixa já está na playlist do Spotify (repetida no YouTube Music).', 'aviso')
                    repetidas.append(query)
                    estado['puladas'].append(chave)
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
            self.ui(self._ui_progresso_fim, 'Migração concluída',
                    f'{len(estado["adicionadas"])} música(s) adicionada(s) à playlist.')
            self.log('🎉 PROCESSO FINALIZADO 🎉', 'sucesso')
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
            self.ui(self._ui_progresso_status, 'Migração pausada',
                    'O progresso foi salvo. Inicie de novo com o mesmo nome de playlist para retomar.')
            if estado is None or not sp_playlist_id:
                self.log('\n⏸️ Migração pausada antes de começar a adicionar músicas. Nada foi criado.', 'aviso')
            else:
                self.log(f'\n⏸️ Migração PAUSADA. Progresso salvo: {len(estado["adicionadas"])} '
                         'música(s) já adicionada(s).', 'aviso')
                if pendentes:
                    self.log(f'   {len(pendentes)} faixa(s) que esperavam aprovação serão reavaliadas ao retomar.', 'cinza')
                self.log(f"▶ Para retomar, inicie de novo com o MESMO link e o MESMO nome de playlist ('{nome_playlist_destino}').", 'info')
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


if __name__ == '__main__':
    app = MigradorApp()
    app.mainloop()