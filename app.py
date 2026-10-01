# -*- coding: utf-8 -*-
"""
Migrador de Playlists: Spotify -> YouTube Music

Cada pessoa usa as PRÓPRIAS contas:
  - Spotify: cria o próprio app no painel de desenvolvedores (grátis) e cola só o
    Client ID. A autorização usa PKCE, então NÃO existe Client Secret no programa.
  - YouTube Music: cola os cabeçalhos da requisição copiados do navegador. O app
    limpa, valida (faz uma chamada de teste) e só então salva.

Nada de credenciais é embutido no código. Tudo fica em %APPDATA%\\MigradorPlaylists.
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
import urllib.parse
import webbrowser
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, HTTPServer

import customtkinter as ctk
import requests
import spotipy
import tkinter.messagebox as messagebox
from spotipy.exceptions import SpotifyException
from ytmusicapi import YTMusic

# ============================== CAMINHOS ======================================
APP_NAME = 'MigradorPlaylists'
APP_VERSION = '1.0.0'

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
LOG_ERROS_PATH = os.path.join(DATA_DIR, 'erros.log')

# ============================== SPOTIFY (PKCE) ================================
SPOTIFY_REDIRECT_URI = 'http://127.0.0.1:8080'
SPOTIFY_SCOPE = 'playlist-read-private playlist-read-collaborative'
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
                'Clique em "1. Vincular Spotify" e autorize novamente.'
            )
        novo = resp.json()
        novo.setdefault('refresh_token', token['refresh_token'])
        return self._gravar_token(novo)

    # Interface esperada pelo spotipy
    def get_access_token(self, as_dict=True, check_cache=True):
        token = self._ler_token()
        if not token or not token.get('refresh_token'):
            raise RuntimeError('Spotify não vinculado. Clique em "1. Vincular Spotify".')
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
# Só esses cabeçalhos são aproveitados. Todo o resto que o navegador copia
# (pseudo-cabeçalhos ":path", accept-encoding com br/zstd, content-length,
# content-encoding...) é descartado porque quebra a chamada.
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
    texto = re.split(r'\s--data(?:-raw|-binary|-urlencode)?\s', texto)[0]   # ignora o corpo
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
    """Aceita JSON, 'nome: valor' (uma linha) ou nome/valor em linhas alternadas.
    Devolve o dicionário pronto para o ytmusicapi ou levanta ValueError (PT-BR)."""
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
            # formato "nome: valor"
            for l in casam:
                nome, valor = _PADRAO_LINHA.match(l).groups()
                brutos[nome.strip().lower()] = valor.strip()
        else:
            # formato alternado: nome numa linha, valor na seguinte
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
        'accept-encoding': 'gzip, deflate',   # sem br/zstd: a biblioteca não decodifica
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
    yt.get_library_playlists(limit=1)   # se também falhar, a exceção sobe
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


ctk.set_appearance_mode('System')
ctk.set_default_color_theme('green')

COR_OK, COR_OK_HOVER = '#1f538d', '#14375e'
COR_PENDENTE, COR_PENDENTE_HOVER = '#b22222', '#8b0000'


# ============================== INTERFACE =====================================
class MigradorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(f'Migrador de Playlists: Spotify ➔ YT Music  (v{APP_VERSION})')
        self.geometry('780x720')
        self.minsize(700, 600)

        self._fila_ui = queue.Queue()   # tudo que mexe na tela passa por aqui (thread-safe)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(7, weight=1)

        # Linha 0: botões de vínculo das contas
        frame_vinculos = ctk.CTkFrame(self, fg_color='transparent')
        frame_vinculos.grid(row=0, column=0, padx=20, pady=(15, 0), sticky='ew')
        frame_vinculos.grid_columnconfigure((0, 1), weight=1, uniform='v')

        self.btn_spotify = ctk.CTkButton(
            frame_vinculos, text='🎧 1. Vincular Spotify', command=self.abrir_janela_spotify,
            font=('Roboto', 13, 'bold'), height=36)
        self.btn_spotify.grid(row=0, column=0, padx=(0, 5), sticky='ew')

        self.btn_yt = ctk.CTkButton(
            frame_vinculos, text='▶ 2. Vincular YouTube Music', command=self.abrir_janela_yt,
            font=('Roboto', 13, 'bold'), height=36)
        self.btn_yt.grid(row=0, column=1, padx=(5, 0), sticky='ew')

        # Linha 1: histórico
        self.btn_historico = ctk.CTkButton(
            self, text='🗑️ Gerenciar / Apagar Histórico de Progresso',
            command=self.abrir_janela_historico, font=('Roboto', 13, 'bold'),
            fg_color='#333333', hover_color='#444444', height=30)
        self.btn_historico.grid(row=1, column=0, padx=20, pady=(8, 0), sticky='ew')

        # Entradas
        self.lbl_spotify = ctk.CTkLabel(self, text='Link ou ID da Playlist do Spotify:', font=('Roboto', 14, 'bold'))
        self.lbl_spotify.grid(row=2, column=0, padx=20, pady=(15, 0), sticky='w')
        self.entry_spotify = ctk.CTkEntry(self, placeholder_text='Ex: https://open.spotify.com/playlist/...', width=500)
        self.entry_spotify.grid(row=3, column=0, padx=20, pady=5, sticky='ew')

        self.lbl_yt = ctk.CTkLabel(self, text='Nome para a nova Playlist no YouTube Music:', font=('Roboto', 14, 'bold'))
        self.lbl_yt.grid(row=4, column=0, padx=20, pady=(10, 0), sticky='w')
        self.entry_yt = ctk.CTkEntry(self, placeholder_text='Ex: Minha Playlist Importada', width=500)
        self.entry_yt.grid(row=5, column=0, padx=20, pady=5, sticky='ew')

        self.btn_iniciar = ctk.CTkButton(
            self, text='▶ Iniciar Migração', command=self.iniciar_thread,
            font=('Roboto', 14, 'bold'), height=40)
        self.btn_iniciar.grid(row=6, column=0, padx=20, pady=15)

        self.log_box = ctk.CTkTextbox(self, state='disabled', font=('Consolas', 12),
                                      fg_color='#1e1e1e', text_color='#d4d4d4')
        self.log_box.grid(row=7, column=0, padx=20, pady=(0, 20), sticky='nsew')
        self.log_box.tag_config('erro', foreground='#ff4d4d')
        self.log_box.tag_config('sucesso', foreground='#00ff00')
        self.log_box.tag_config('aviso', foreground='#ffcc00')
        self.log_box.tag_config('info', foreground='#00aaff')
        self.log_box.tag_config('cinza', foreground='#aaaaaa')

        self.after(100, self._processar_fila)
        migrar_arquivos_antigos()
        self.atualizar_status_vinculos(mostrar_dicas=True)

    # ---------- infraestrutura thread-safe ----------
    def ui(self, fn, *args):
        """Agenda fn(*args) para rodar na thread principal (seguro chamar de qualquer thread)."""
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
        """Pergunta sim/não a partir de uma thread de trabalho e espera a resposta."""
        evento, resposta = threading.Event(), {}

        def _mostrar():
            try:
                resposta['v'] = messagebox.askyesno(titulo, mensagem)
            finally:
                evento.set()

        self.ui(_mostrar)
        evento.wait()
        return resposta.get('v', False)

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
        janela.transient(self)
        janela.after(200, lambda: janela.winfo_exists() and janela.grab_set())
        return janela

    # ---------- status dos vínculos ----------
    def atualizar_status_vinculos(self, mostrar_dicas=False):
        sp_ok = spotify_vinculado()
        yt_ok = os.path.exists(YT_AUTH_PATH)
        self.btn_spotify.configure(
            text='✅ 1. Spotify vinculado' if sp_ok else '🎧 1. Vincular Spotify',
            fg_color=COR_OK if sp_ok else COR_PENDENTE,
            hover_color=COR_OK_HOVER if sp_ok else COR_PENDENTE_HOVER)
        self.btn_yt.configure(
            text='✅ 2. YouTube Music vinculado' if yt_ok else '▶ 2. Vincular YouTube Music',
            fg_color=COR_OK if yt_ok else COR_PENDENTE,
            hover_color=COR_OK_HOVER if yt_ok else COR_PENDENTE_HOVER)
        if mostrar_dicas:
            if sp_ok and yt_ok:
                self.log('✅ Spotify e YouTube Music vinculados. É só colar o link da playlist e iniciar!', 'sucesso')
            else:
                self.log('👋 Antes de migrar, vincule as suas contas (uma vez só):', 'aviso')
                if not sp_ok:
                    self.log("   • Clique em '1. Vincular Spotify'.", 'aviso')
                if not yt_ok:
                    self.log("   • Clique em '2. Vincular YouTube Music'.", 'aviso')

    # ---------- janela: Spotify ----------
    def abrir_janela_spotify(self):
        janela = self._nova_janela('Vincular Conta do Spotify', '680x700')
        cancelar = threading.Event()

        def fechar():
            cancelar.set()
            janela.destroy()

        janela.protocol('WM_DELETE_WINDOW', fechar)

        ctk.CTkLabel(janela, text='Como vincular o seu Spotify (uma vez só):',
                     font=('Roboto', 15, 'bold')).pack(padx=20, pady=(18, 6), anchor='w')
        instrucoes = (
            '1. Clique no botão abaixo e entre com a sua conta do Spotify. O Spotify exige que a '
            'conta que cria o app seja Premium.\n'
            "2. Clique em 'Create app'. Nome e descrição podem ser qualquer coisa.\n"
            "3. Em 'Redirect URI', adicione EXATAMENTE o endereço abaixo e clique em 'Add'.\n"
            "4. Em 'Which API/SDKs are you planning to use?' marque 'Web API', aceite os termos e 'Save'.\n"
            "5. Abra o app criado > 'Settings' e copie o 'Client ID' (não precisa do Client Secret).\n"
            "6. Cole o Client ID abaixo e clique em 'Vincular e autorizar'. No navegador, aceite."
        )
        ctk.CTkLabel(janela, text=instrucoes, font=('Roboto', 12), justify='left',
                     wraplength=630).pack(padx=20, pady=4, anchor='w')

        ctk.CTkButton(janela, text='🌐 Abrir painel do Spotify para Desenvolvedores',
                      command=lambda: webbrowser.open('https://developer.spotify.com/dashboard'),
                      height=32).pack(padx=20, pady=(8, 4), anchor='w')

        frame_uri = ctk.CTkFrame(janela, fg_color='transparent')
        frame_uri.pack(fill='x', padx=20, pady=(8, 0))
        ctk.CTkLabel(frame_uri, text='Redirect URI:', font=('Roboto', 12, 'bold')).pack(side='left')
        ctk.CTkLabel(frame_uri, text=SPOTIFY_REDIRECT_URI, font=('Consolas', 13, 'bold'),
                     text_color='#00aaff').pack(side='left', padx=10)
        ctk.CTkButton(frame_uri, text='Copiar', width=70, height=26,
                      command=lambda: self._copiar(SPOTIFY_REDIRECT_URI)).pack(side='left')

        ctk.CTkLabel(janela, text='Client ID:', font=('Roboto', 12, 'bold')).pack(padx=20, pady=(14, 0), anchor='w')
        entry_id = ctk.CTkEntry(janela, placeholder_text='Cole aqui o Client ID (32 caracteres)', width=630)
        entry_id.pack(padx=20, pady=4)
        cfg = ler_json(SPOTIFY_CONFIG_PATH, {}) or {}
        if cfg.get('client_id'):
            entry_id.insert(0, cfg['client_id'])

        lbl_status = ctk.CTkLabel(
            janela, wraplength=630, justify='left', font=('Roboto', 12),
            text='✅ Spotify já vinculado.' if spotify_vinculado() else '⚠️ Ainda não vinculado.')
        lbl_status.pack(padx=20, pady=(10, 0), anchor='w')

        frame_btns = ctk.CTkFrame(janela, fg_color='transparent')
        frame_btns.pack(pady=14)
        btn_vincular = ctk.CTkButton(frame_btns, text='🔗 Vincular e autorizar', height=36,
                                     font=('Roboto', 13, 'bold'), fg_color='#28a745', hover_color='#218838')
        btn_vincular.pack(side='left', padx=6)
        btn_desvincular = ctk.CTkButton(frame_btns, text='Desvincular', height=36,
                                        fg_color=COR_PENDENTE, hover_color=COR_PENDENTE_HOVER)
        btn_desvincular.pack(side='left', padx=6)

        def concluir(ok, info):
            self.atualizar_status_vinculos()
            if not janela.winfo_exists():
                return
            btn_vincular.configure(state='normal', text='🔗 Vincular e autorizar')
            if ok:
                quem = f' como {info}' if info else ''
                lbl_status.configure(text=f'✅ Spotify vinculado{quem}!', text_color='#28a745')
            else:
                lbl_status.configure(text=f'❌ {info}', text_color='#ff4d4d')

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
                os.remove(SPOTIFY_TOKEN_PATH)   # Client ID novo invalida o token antigo
            cancelar.clear()
            btn_vincular.configure(state='disabled', text='Aguardando autorização no navegador...')
            lbl_status.configure(text='🌐 Autorize no navegador que abriu (você tem 3 minutos)...',
                                 text_color='#ffcc00')

            def trabalho():
                try:
                    auth = SpotifyPKCE(client_id, SPOTIFY_TOKEN_PATH)
                    auth.autorizar(cancelar=cancelar)
                    nome = None
                    try:
                        me = spotipy.Spotify(auth_manager=auth, requests_timeout=20).current_user()
                        nome = me.get('display_name') or me.get('id')
                    except Exception:
                        pass   # autorizou, só não deu para ler o nome
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
            lbl_status.configure(text='⚠️ Ainda não vinculado.', text_color='#ffcc00')
            self.atualizar_status_vinculos()

        btn_vincular.configure(command=vincular)
        btn_desvincular.configure(command=desvincular)

    # ---------- janela: YouTube Music ----------
    def abrir_janela_yt(self):
        janela = self._nova_janela('Vincular Conta do YouTube Music', '700x720')

        ctk.CTkLabel(janela, text='Como vincular o seu YouTube Music (uma vez só):',
                     font=('Roboto', 15, 'bold')).pack(padx=20, pady=(18, 6), anchor='w')
        instrucoes = (
            "1. Clique em 'Abrir music.youtube.com' e entre na sua conta.\n"
            "2. Aperte F12 e abra a aba 'Rede' (Network). Com ela aberta, clique em uma playlist "
            "ou na Biblioteca do site para gerar requisições.\n"
            "3. No filtro da aba, digite 'browse'. Clique com o BOTÃO DIREITO em uma requisição "
            "'browse?...' (método POST) > Copiar > 'Copiar como cURL (bash)'.\n"
            "4. Cole tudo abaixo e clique em 'Validar e salvar'. O app testa a conexão antes de guardar.\n\n"
            "Funciona no Brave, Chrome e Edge. Se preferir copiar na mão: aba 'Cabeçalhos' > "
            "'Cabeçalhos da requisição', do início até o FINAL da lista (o 'cookie' é o mais importante)."
        )
        ctk.CTkLabel(janela, text=instrucoes, font=('Roboto', 12), justify='left',
                     wraplength=650).pack(padx=20, pady=4, anchor='w')

        ctk.CTkButton(janela, text='🌐 Abrir music.youtube.com',
                      command=lambda: webbrowser.open('https://music.youtube.com'),
                      height=32).pack(padx=20, pady=(8, 4), anchor='w')

        txt_input = ctk.CTkTextbox(janela, width=650, height=200, font=('Consolas', 10))
        txt_input.pack(padx=20, pady=10)

        lbl_status = ctk.CTkLabel(
            janela, wraplength=650, justify='left', font=('Roboto', 12),
            text='✅ YouTube Music já vinculado.' if os.path.exists(YT_AUTH_PATH) else '⚠️ Ainda não vinculado.')
        lbl_status.pack(padx=20, pady=(0, 0), anchor='w')

        frame_btns = ctk.CTkFrame(janela, fg_color='transparent')
        frame_btns.pack(pady=14)
        btn_salvar = ctk.CTkButton(frame_btns, text='✅ Validar e salvar', height=36,
                                   font=('Roboto', 13, 'bold'), fg_color='#28a745', hover_color='#218838')
        btn_salvar.pack(side='left', padx=6)
        btn_desvincular = ctk.CTkButton(frame_btns, text='Desvincular', height=36,
                                        fg_color=COR_PENDENTE, hover_color=COR_PENDENTE_HOVER)
        btn_desvincular.pack(side='left', padx=6)

        def concluir(ok, info):
            self.atualizar_status_vinculos()
            if not janela.winfo_exists():
                return
            btn_salvar.configure(state='normal', text='✅ Validar e salvar')
            if ok:
                lbl_status.configure(text=f'✅ YouTube Music vinculado ({info})!', text_color='#28a745')
                txt_input.delete('1.0', ctk.END)   # não deixa cookies à vista
            else:
                lbl_status.configure(text=f'❌ {info}', text_color='#ff4d4d')

        def salvar():
            try:
                headers = normalizar_cabecalhos_yt(txt_input.get('1.0', ctk.END))
            except ValueError as e:
                lbl_status.configure(text=f'❌ {e}', text_color='#ff4d4d')
                return
            btn_salvar.configure(state='disabled', text='Testando conexão...')
            lbl_status.configure(text='🔎 Testando a conexão com o YouTube Music...', text_color='#ffcc00')

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
            lbl_status.configure(text='⚠️ Ainda não vinculado.', text_color='#ffcc00')
            self.atualizar_status_vinculos()

        btn_salvar.configure(command=salvar)
        btn_desvincular.configure(command=desvincular)

    # ---------- janela: histórico ----------
    def abrir_janela_historico(self):
        janela = self._nova_janela('Gerenciar Histórico de Progresso', '520x450')

        ctk.CTkLabel(janela, text='Selecione o histórico que deseja apagar:',
                     font=('Roboto', 15, 'bold')).pack(padx=20, pady=(20, 10), anchor='w')
        frame_lista = ctk.CTkScrollableFrame(janela, width=470, height=310)
        frame_lista.pack(padx=20, pady=5, fill='both', expand=True)

        def atualizar_lista():
            for widget in frame_lista.winfo_children():
                widget.destroy()
            arquivos = glob.glob(os.path.join(DATA_DIR, 'progresso_*.json'))
            if not arquivos:
                ctk.CTkLabel(frame_lista, text='Nenhum arquivo de histórico encontrado.',
                             font=('Roboto', 12), text_color='gray').pack(padx=10, pady=30)
                return
            for arquivo in arquivos:
                nome_playlist = os.path.basename(arquivo).replace('progresso_', '').replace('.json', '').replace('_', ' ')
                row = ctk.CTkFrame(frame_lista, fg_color='transparent')
                row.pack(fill='x', pady=6)
                ctk.CTkLabel(row, text=f'📂 {nome_playlist}', font=('Roboto', 12, 'bold'),
                             anchor='w').pack(side='left', padx=5, fill='x', expand=True)

                def apagar(arq=arquivo, nome=nome_playlist):
                    if messagebox.askyesno('Confirmar Exclusão',
                                           f"Apagar o histórico de progresso da playlist '{nome}'?", parent=janela):
                        try:
                            if os.path.exists(arq):
                                os.remove(arq)
                            atualizar_lista()
                            self.log(f'🗑️ Histórico apagado: {nome}', 'aviso')
                        except Exception as e:
                            messagebox.showerror('Erro', f'Não foi possível apagar o arquivo: {e}', parent=janela)

                ctk.CTkButton(row, text='Apagar', command=apagar, font=('Roboto', 11, 'bold'),
                              fg_color=COR_PENDENTE, hover_color=COR_PENDENTE_HOVER,
                              width=80, height=28).pack(side='right', padx=5)

        atualizar_lista()

    # ---------- migração ----------
    def iniciar_thread(self):
        link_spotify = self.entry_spotify.get().strip()
        nome_yt = self.entry_yt.get().strip()

        if not link_spotify:
            messagebox.showwarning('Aviso', 'Por favor, insira o link do Spotify!')
            return
        if not spotify_vinculado():
            messagebox.showerror('Spotify não vinculado', "Clique em '1. Vincular Spotify' primeiro.")
            return
        if not os.path.exists(YT_AUTH_PATH):
            messagebox.showerror('YouTube Music não vinculado', "Clique em '2. Vincular YouTube Music' primeiro.")
            return

        self.btn_iniciar.configure(state='disabled', text='Processando...')
        self._limpar_log()
        threading.Thread(target=self.processo_migracao, args=(link_spotify, nome_yt), daemon=True).start()

    def similaridade(self, a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def validar_resultado(self, track_name, artist_name, yt_title, yt_artist_name):
        yt_artist_lower = yt_artist_name.lower().strip() if yt_artist_name else ''
        yt_title_lower = yt_title.lower().strip() if yt_title else ''

        if artist_name:
            artista_sp = artist_name.lower().strip()
            if not yt_artist_lower:
                return False
            score_artista = self.similaridade(artista_sp, yt_artist_lower)
            if score_artista < 0.35:
                if not (artista_sp in yt_artist_lower or yt_artist_lower in artista_sp):
                    return False
        else:
            if self.similaridade(track_name, yt_title) < 0.85:
                return False

        modifiers = ['instrumental', 'slowed', 'sped up', 'reverb', 'acapella']
        track_lower = track_name.lower()
        for mod in modifiers:
            if mod in track_lower and mod not in yt_title_lower:
                return False
            if mod not in track_lower and mod in yt_title_lower:
                return False

        score_titulo = self.similaridade(track_name, yt_title)
        if score_titulo < 0.2 and (artist_name and artist_name.lower() not in yt_title_lower):
            return False

        return True

    def processo_migracao(self, spotify_input, nome_playlist_destino):
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
                    return estado
                return {'playlist_id': None, 'adicionadas': []}

            def salvar_estado(estado):
                gravar_json(ARQUIVO_ESTADO, estado)

            self.log('=' * 60)
            self.log('Conectando ao Spotify...', 'info')

            auth = carregar_auth_spotify()
            if auth is None or not auth.vinculado():
                raise RuntimeError("Spotify não vinculado. Clique em '1. Vincular Spotify'.")
            sp = spotipy.Spotify(auth_manager=auth, requests_timeout=30)

            self.log('🔍 Puxando músicas da playlist do Spotify...', 'info')
            tracks_info = []
            musicas_indisponiveis_spotify = []
            vistas_no_spotify = set()
            total_itens_raw = 0
            offset = 0

            try:
                while True:
                    # Com token de usuário, o país da conta tem prioridade sobre 'market'.
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

                                if chave_unica in vistas_no_spotify:
                                    continue

                                vistas_no_spotify.add(chave_unica)
                                tracks_info.append({'name': name, 'artist': artists, 'full': chave_unica})
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
                return

            yt = YTMusic(YT_AUTH_PATH)
            estado = carregar_estado()
            yt_playlist_id = estado.get('playlist_id')

            if not yt_playlist_id:
                self.log(f"🚀 Criando a playlist '{nome_playlist_destino}' no YouTube Music...", 'info')
                yt_playlist_id = yt.create_playlist(title=nome_playlist_destino, description='Importada via Migrador de Playlists')
                if not isinstance(yt_playlist_id, str):
                    raise RuntimeError(f'O YouTube Music recusou criar a playlist: {yt_playlist_id}')
                estado['playlist_id'] = yt_playlist_id
                salvar_estado(estado)
            else:
                self.log('♻️ Playlist recuperada do histórico!', 'info')

            ja_adicionadas = set(estado.get('adicionadas', []))
            musicas_com_erro = []
            video_ids_sessao = set()
            erros_busca_seguidos = 0

            self.log('\n🔎 Sincronizando faixas com o YouTube Music...')
            for i, item in enumerate(tracks_info, 1):
                query = item['full']
                track_name = item['name']
                artist_name = item['artist']

                if query in ja_adicionadas:
                    self.log(f'[{i}/{len(tracks_info)}] ⏩ Já importada: {query}', 'aviso')
                    continue

                self.log(f'\n[{i}/{len(tracks_info)}] 🎵 Procurando: "{query}"')
                video_id = None
                search_results = []

                try:
                    buscas = [f'{track_name} {artist_name}'.strip(), query, track_name]
                    for termo in buscas:
                        if termo:
                            search_results.extend(yt.search(termo, limit=5))
                    erros_busca_seguidos = 0

                    for top_result in search_results:
                        yt_title = top_result.get('title', '')
                        yt_artists = top_result.get('artists', [])
                        yt_artist_name = yt_artists[0].get('name', '') if yt_artists else str(top_result.get('author', ''))
                        candidate_id = top_result.get('videoId')

                        if not candidate_id or candidate_id in video_ids_sessao:
                            continue

                        if self.validar_resultado(track_name, artist_name, yt_title, yt_artist_name):
                            video_id = candidate_id
                            self.log(f'   🎯 Match automático -> "{yt_title}" - "{yt_artist_name}"', 'sucesso')
                            break

                    if not video_id and search_results:
                        top_fail = search_results[0]
                        yt_title = top_fail.get('title', '')
                        yt_artists = top_fail.get('artists', [])
                        yt_artist_name = yt_artists[0].get('name', '') if yt_artists else str(top_fail.get('author', 'N/A'))
                        candidate_id = top_fail.get('videoId')

                        resposta_user = self.perguntar(
                            'Aprovação Manual',
                            f'Spotify: {query}\n\n'
                            f'Encontrado no YT: {yt_title} - {yt_artist_name}\n\n'
                            f'Deseja adicionar esta versão?')

                        if resposta_user:
                            video_id = candidate_id
                            self.log('   👉 Aprovado manualmente!', 'sucesso')
                        else:
                            self.log('   ❌ Rejeitado pelo usuário.', 'erro')
                            musicas_com_erro.append((query, 'Rejeitada pelo usuário'))

                except Exception as e:
                    erros_busca_seguidos += 1
                    registrar_erro_em_arquivo()
                    self.log(f'   ⚠️ Erro ao buscar no YouTube Music: {explicar_erro(e)}', 'aviso')
                    if erros_busca_seguidos >= 5:
                        raise RuntimeError(
                            'Muitas falhas seguidas ao buscar no YouTube Music. A sessão pode ter expirado: '
                            "clique em '2. Vincular YouTube Music' e cole os cabeçalhos de novo. "
                            'O progresso foi salvo e a migração continua de onde parou.')

                if video_id:
                    sucesso = False
                    ultimo_erro = None
                    for _ in range(3):
                        try:
                            yt.add_playlist_items(yt_playlist_id, [video_id])
                            sucesso = True
                            break
                        except Exception as e:
                            ultimo_erro = e
                            time.sleep(3)

                    if sucesso:
                        self.log('   ✅ Adicionada com sucesso!', 'sucesso')
                        video_ids_sessao.add(video_id)
                        estado['adicionadas'].append(query)
                        salvar_estado(estado)
                    else:
                        self.log('   ⚠️ Não consegui adicionar (limite do YouTube ou sessão inválida)', 'erro')
                        if ultimo_erro:
                            self.log(f'      {explicar_erro(ultimo_erro)}', 'cinza')
                        musicas_com_erro.append((query, 'Falha ao adicionar no YouTube Music'))
                        time.sleep(6)
                else:
                    if not any(query == q for q, motivo in musicas_com_erro):
                        self.log('   ⚠️ Não encontrada no YouTube.', 'aviso')
                        musicas_com_erro.append((query, 'Não encontrada no YouTube Music'))

                time.sleep(1.5)

            self.log('\n' + '=' * 60)
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

        except Exception as e:
            registrar_erro_em_arquivo()
            self.log(f'\n❌ Ocorreu um erro: {explicar_erro(e)}', 'erro')
        finally:
            self.ui(self.btn_iniciar.configure, state='normal', text='▶ Iniciar Migração')


if __name__ == '__main__':
    app = MigradorApp()
    app.mainloop()
