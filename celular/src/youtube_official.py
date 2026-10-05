# -*- coding: utf-8 -*-
"""Camada Android para YouTube/YouTube Music.

O OAuth do ytmusicapi continua gerando tokens válidos do Google, mas desde uma
mudança do servidor do YouTube em 2025 as chamadas autenticadas ao endpoint
privado /youtubei podem responder HTTP 400 (issue #813 do ytmusicapi).

No Android usamos:
- ytmusicapi SEM autenticação apenas para busca pública de músicas;
- YouTube Data API v3 oficial para ler/criar playlists e adicionar vídeos.

Assim o usuário continua vendo uma playlist compatível com YouTube Music, mas
as operações autenticadas deixam de depender do endpoint privado quebrado.
"""
import json
import os
import re
import time
from datetime import timedelta

import requests

API_BASE = "https://www.googleapis.com/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class HybridYTMusic:
    """Subset compatível com o YTMusic usado pelo núcleo do Migrador."""

    PUBLIC_CLASS = None  # preenchido pelo entrypoint antes do app iniciar

    def __init__(self, auth=None, *args, oauth_credentials=None, **kwargs):
        self.auth = auth if isinstance(auth, str) else None
        self.oauth_credentials = oauth_credentials
        public_cls = self.PUBLIC_CLASS
        if public_cls is None:
            raise RuntimeError("Cliente público do YouTube Music não foi inicializado.")
        # Sem login: ótimo para search(), sem o bug atual do OAuth do ytmusicapi.
        self.public = public_cls()

    # ------------------------------ token ---------------------------------
    def _ler_token(self):
        if not self.auth or not os.path.exists(self.auth):
            raise RuntimeError("YouTube Music não vinculado. Entre com sua conta Google.")
        try:
            with open(self.auth, "r", encoding="utf-8") as f:
                token = json.load(f)
        except (OSError, ValueError) as ex:
            raise RuntimeError("A sessão do YouTube está corrompida. Vincule a conta novamente.") from ex
        if not isinstance(token, dict) or not token.get("access_token"):
            raise RuntimeError("A sessão do YouTube está incompleta. Vincule a conta novamente.")
        return token

    def _salvar_token(self, token):
        if not self.auth:
            return
        tmp = self.auth + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(token, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.auth)
        try:
            os.chmod(self.auth, 0o600)
        except OSError:
            pass

    def _cred(self, nome):
        c = self.oauth_credentials
        if c is None:
            return ""
        return str(getattr(c, nome, "") or "").strip()

    def _access_token(self):
        token = self._ler_token()
        expira = float(token.get("expires_at") or 0)
        if expira and expira > time.time() + 90:
            return token["access_token"]
        refresh = token.get("refresh_token")
        if not refresh:
            return token["access_token"]

        client_id = self._cred("client_id")
        client_secret = self._cred("client_secret")
        if not client_id:
            raise RuntimeError("O APK não contém o Client ID do Google.")
        dados = {
            "client_id": client_id,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        }
        if client_secret:
            dados["client_secret"] = client_secret
        r = requests.post(TOKEN_URL, data=dados, timeout=30)
        if r.status_code != 200:
            try:
                d = r.json()
                detalhe = d.get("error_description") or d.get("error")
            except ValueError:
                detalhe = r.text[:200]
            raise RuntimeError(
                "A sessão do Google expirou ou foi revogada. Vincule o YouTube Music novamente."
                + (f" ({detalhe})" if detalhe else "")
            )
        novo = r.json()
        token.update(novo)
        token["refresh_token"] = refresh
        token["expires_at"] = int(time.time()) + int(novo.get("expires_in") or 3600)
        self._salvar_token(token)
        return token["access_token"]

    # --------------------------- REST oficial -----------------------------
    @staticmethod
    def _erro_google(resp):
        try:
            d = resp.json()
            erro = d.get("error") if isinstance(d, dict) else None
            if isinstance(erro, dict):
                return erro.get("message") or str(erro)
            return str(erro or d)
        except ValueError:
            return resp.text[:300] or f"HTTP {resp.status_code}"

    def _api(self, metodo, recurso, params=None, payload=None):
        token = self._access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url = API_BASE + "/" + recurso.lstrip("/")
        resp = requests.request(
            metodo, url, params=params or {}, json=payload,
            headers=headers, timeout=30,
        )
        if resp.status_code == 401:
            # Pode ter vencido entre a leitura e a chamada; força uma renovação uma vez.
            t = self._ler_token()
            t["expires_at"] = 0
            self._salvar_token(t)
            token = self._access_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = requests.request(
                metodo, url, params=params or {}, json=payload,
                headers=headers, timeout=30,
            )
        if not (200 <= resp.status_code < 300):
            detalhe = self._erro_google(resp)
            if resp.status_code == 403 and "quota" in detalhe.lower():
                raise RuntimeError("A cota diária da YouTube Data API acabou. Tente novamente amanhã.")
            raise RuntimeError(f"YouTube respondeu HTTP {resp.status_code}: {detalhe}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ------------------------- interface YTMusic ---------------------------
    def get_account_info(self):
        d = self._api("GET", "channels", {
            "part": "snippet", "mine": "true", "maxResults": 1,
        })
        itens = d.get("items") or []
        nome = ((itens[0].get("snippet") or {}).get("title") if itens else None)
        return {"accountName": nome or "conta conectada"}

    def get_library_playlists(self, limit=25):
        maximo = 50 if limit is None else max(1, min(50, int(limit)))
        d = self._api("GET", "playlists", {
            "part": "snippet,contentDetails", "mine": "true", "maxResults": maximo,
        })
        saida = []
        for p in d.get("items") or []:
            saida.append({
                "playlistId": p.get("id"),
                "title": (p.get("snippet") or {}).get("title", ""),
                "count": (p.get("contentDetails") or {}).get("itemCount", 0),
            })
        return saida

    def create_playlist(self, title, description="", privacy_status="PRIVATE", **kwargs):
        priv = str(privacy_status or "PRIVATE").lower()
        if priv not in ("private", "public", "unlisted"):
            priv = "private"
        d = self._api("POST", "playlists", {"part": "snippet,status"}, {
            "snippet": {"title": str(title), "description": str(description or "")},
            "status": {"privacyStatus": priv},
        })
        pid = d.get("id")
        if not pid:
            raise RuntimeError("O YouTube não devolveu o ID da playlist criada.")
        return pid

    def add_playlist_items(self, playlistId, videoIds, **kwargs):
        if isinstance(videoIds, str):
            videoIds = [videoIds]
        respostas = []
        for video_id in videoIds or []:
            respostas.append(self._api("POST", "playlistItems", {"part": "snippet"}, {
                "snippet": {
                    "playlistId": playlistId,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            }))
        return {"status": "STATUS_SUCCEEDED", "results": respostas}

    @staticmethod
    def _duracao_segundos(valor):
        # ISO 8601 simples: PT1H2M3S / PT4M5S / PT37S
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(valor or ""))
        if not m:
            return None
        h, mi, s = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + s

    @staticmethod
    def _artista_canal(nome):
        n = str(nome or "").strip()
        n = re.sub(r"(?i)\s*-\s*Topic$", "", n).strip()
        n = re.sub(r"(?i)VEVO$", "", n).strip()
        return n

    def _detalhes_videos(self, ids):
        detalhes = {}
        lista = [i for i in ids if i]
        for pos in range(0, len(lista), 50):
            lote = lista[pos:pos + 50]
            d = self._api("GET", "videos", {
                "part": "snippet,contentDetails,status", "id": ",".join(lote), "maxResults": 50,
            })
            for v in d.get("items") or []:
                detalhes[v.get("id")] = v
        return detalhes

    def get_playlist(self, playlistId, limit=100, **kwargs):
        meta = self._api("GET", "playlists", {
            "part": "snippet,contentDetails", "id": playlistId, "maxResults": 1,
        })
        itens_meta = meta.get("items") or []
        if not itens_meta:
            raise RuntimeError("Playlist do YouTube não encontrada ou sem acesso.")
        p = itens_meta[0]
        snippet_p = p.get("snippet") or {}
        total = (p.get("contentDetails") or {}).get("itemCount", 0)

        alvo = None if limit is None else max(0, int(limit))
        tracks_brutos = []
        token_pag = None
        while alvo is None or len(tracks_brutos) < alvo:
            maximo = 50 if alvo is None else max(1, min(50, alvo - len(tracks_brutos)))
            params = {
                "part": "snippet,contentDetails,status", "playlistId": playlistId,
                "maxResults": maximo,
            }
            if token_pag:
                params["pageToken"] = token_pag
            d = self._api("GET", "playlistItems", params)
            lote = d.get("items") or []
            tracks_brutos.extend(lote)
            token_pag = d.get("nextPageToken")
            if not token_pag or not lote:
                break

        ids = []
        for it in tracks_brutos:
            sn = it.get("snippet") or {}
            cd = it.get("contentDetails") or {}
            vid = cd.get("videoId") or ((sn.get("resourceId") or {}).get("videoId"))
            if vid:
                ids.append(vid)
        detalhes = self._detalhes_videos(ids) if ids else {}

        tracks = []
        for it in tracks_brutos:
            sn = it.get("snippet") or {}
            cd = it.get("contentDetails") or {}
            vid = cd.get("videoId") or ((sn.get("resourceId") or {}).get("videoId"))
            dv = detalhes.get(vid) or {}
            dvs = dv.get("snippet") or {}
            titulo = dvs.get("title") or sn.get("title") or ""
            canal = dvs.get("channelTitle") or sn.get("videoOwnerChannelTitle") or ""
            artista = self._artista_canal(canal)
            disponivel = bool(dv) and titulo not in ("Deleted video", "Private video")
            tipo = "MUSIC_VIDEO_TYPE_ATV" if re.search(r"(?i)-\s*Topic$", canal) else "MUSIC_VIDEO_TYPE_OMV"
            tracks.append({
                "videoId": vid,
                "title": titulo,
                "artists": ([{"name": artista}] if artista else []),
                "author": artista,
                "isAvailable": disponivel,
                "videoType": tipo,
                "duration_seconds": self._duracao_segundos((dv.get("contentDetails") or {}).get("duration")),
            })

        return {
            "id": playlistId,
            "title": snippet_p.get("title") or "Playlist do YouTube",
            "trackCount": total,
            "tracks": tracks,
        }

    def get_liked_songs(self, limit=100, **kwargs):
        d = self._api("GET", "channels", {
            "part": "contentDetails", "mine": "true", "maxResults": 1,
        })
        itens = d.get("items") or []
        likes = None
        if itens:
            likes = (((itens[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("likes"))
        if not likes:
            raise RuntimeError("Não consegui localizar a playlist de músicas/vídeos curtidos dessa conta.")
        return self.get_playlist(likes, limit=limit)

    def search(self, query, filter=None, scope=None, limit=20, ignore_spelling=False, **kwargs):
        # Mantemos a busca musical do ytmusicapi, mas SEM OAuth. O bug #813 só
        # afeta as chamadas autenticadas; a busca pública continua sendo a melhor
        # fonte de metadados de música para o algoritmo de matching do app.
        return self.public.search(
            query, filter=filter, scope=scope, limit=limit,
            ignore_spelling=ignore_spelling,
        )

    def __getattr__(self, nome):
        # Métodos públicos que o núcleo não sobrescreve continuam disponíveis.
        return getattr(self.public, nome)
