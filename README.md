<div align="center">

# RHYFT

**Your music. No borders.**

### Spotify ⇄ YouTube Music

Migre suas playlists entre **Spotify** e **YouTube Music** de forma simples, rápida e organizada.

[![Site](https://img.shields.io/badge/Abrir%20site-migrador--playlists.netlify.app-00d084?style=for-the-badge&logo=netlify&logoColor=white)](https://migrador-playlists.netlify.app/)
[![Migrar online](https://img.shields.io/badge/Migrar%20online-Abrir%20no%20navegador-7c5cff?style=for-the-badge)](https://migrador-playlists.netlify.app/migrar.html)
[![Releases](https://img.shields.io/badge/Downloads-GitHub%20Releases-24292f?style=for-the-badge&logo=github&logoColor=white)](https://github.com/deadbynetsu/Spotify-Youtube-Music-Playlists-Migrator/releases/latest)

**Windows · Android · Web · Gratuito · Open Source**

> Projeto independente, sem ligação oficial com Spotify, Google, YouTube ou YouTube Music.

</div>

---

## ✨ O projeto

O **RHYFT** permite transferir playlists nos dois sentidos:

- **Spotify → YouTube Music**
- **YouTube Music → Spotify**

Você pode usar pelo navegador ou baixar a versão nativa para Windows e Android.

### 🌐 Site oficial

**https://migrador-playlists.netlify.app/**

No site você encontra a versão Web, downloads atualizados, Política de Privacidade e Termos de Serviço.

---

## 🚀 Recursos

- Migração bidirecional entre Spotify e YouTube Music
- Correspondência automática de músicas
- Revisão manual quando uma correspondência fica incerta
- Progresso da migração em tempo real
- Login via OAuth
- Versões para **Windows**, **Android** e **Web**
- Downloads atualizados automaticamente pela Release mais recente
- Projeto gratuito e open source

---

## 💻 Formas de usar

| Plataforma | Como usar |
|---|---|
| 🌐 **Web** | [Abrir o RHYFT Online](https://migrador-playlists.netlify.app/migrar.html) |
| 🪟 **Windows** | [Baixar a versão mais recente](https://github.com/deadbynetsu/Spotify-Youtube-Music-Playlists-Migrator/releases/latest) |
| 🤖 **Android** | [Baixar o APK mais recente](https://github.com/deadbynetsu/Spotify-Youtube-Music-Playlists-Migrator/releases/latest) |

Os builds de Windows e Android são gerados automaticamente pelo **GitHub Actions** e publicados nas Releases.

---

## 🔄 Como funciona

1. Conecte suas contas do Spotify e Google/YouTube.
2. Escolha o sentido da migração.
3. Cole o link ou ID da playlist.
4. Escolha o nome da playlist de destino.
5. Inicie a migração.
6. Revise apenas as músicas que precisarem de confirmação.

---

## 🔐 Privacidade

O projeto usa OAuth para autenticação — sua senha do Spotify ou Google não é entregue ao RHYFT.

Nos aplicativos nativos, tokens, configurações e progresso ficam no dispositivo. Na versão Web, as sessões são protegidas em cookies **HttpOnly** criptografados e os tokens não ficam expostos ao JavaScript da página.

- [Política de Privacidade](https://migrador-playlists.netlify.app/privacidade.html)
- [Termos de Serviço](https://migrador-playlists.netlify.app/termos.html)

---

## 🛠️ Tecnologias

- **Python**
- **Flet**
- **Spotify Web API**
- **YouTube Data API**
- **Netlify Functions**
- **GitHub Actions**

---

## 📁 Estrutura do projeto

```text
app.py                     # Interface desktop
nucleo.py                  # Lógica compartilhada dos apps nativos
celular/                    # Aplicativo Android em Flet
site/                       # Site oficial e RHYFT Web
netlify/functions/api.js    # OAuth e integrações da versão Web
netlify.toml                # Configuração do deploy no Netlify
.github/workflows/          # Builds automáticos
```

---

## 👨‍💻 Rodar pelo código-fonte

```bash
pip install -r requirements.txt
python app.py
```

---

## 📄 Licença

Distribuído sob a licença **MIT**.

<div align="center">

Feito por **[deadbynetsu](https://github.com/deadbynetsu)**

[🌐 Site](https://migrador-playlists.netlify.app/) · [🎵 Migrar online](https://migrador-playlists.netlify.app/migrar.html) · [📦 Releases](https://github.com/deadbynetsu/Spotify-Youtube-Music-Playlists-Migrator/releases/latest)

</div>
