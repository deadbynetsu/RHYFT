# Migrador de Playlists: Spotify ⇄ YouTube Music

Aplicativo gratuito e open source para migrar playlists entre **Spotify** e **YouTube Music** nos dois sentidos. O projeto possui versões para **Windows**, **Android** e uma **versão Web** preparada para Netlify.

> Projeto independente, sem ligação oficial com Spotify, Google, YouTube ou YouTube Music.

## Formas de usar

- **Windows** — aplicativo desktop.
- **Android** — APK assinado e compilado pelo GitHub Actions.
- **Web** — site em `site/` com migração diretamente pelo navegador e backend serverless em `netlify/functions/`.

## Downloads

Os builds nativos são gerados automaticamente pelos workflows do GitHub Actions e publicados nas Releases da versão correspondente.

- **Build Windows** → `MigradorPlaylists-windows.zip`
- **Build Android (APK)** → `MigradorPlaylists-android.apk`

A página Web consulta a Release mais recente no GitHub e atualiza automaticamente os botões de download.

## Windows

1. Baixe `MigradorPlaylists-windows.zip` na Release mais recente.
2. Extraia o ZIP.
3. Abra `MigradorPlaylists.exe`.

## Android

1. Baixe `MigradorPlaylists-android.apk` na Release mais recente.
2. Permita a instalação do APK no Android.
3. Abra o aplicativo e conecte suas contas.

Para manter a mesma assinatura nas atualizações, veja `celular/README.md`.

## Web

O site contém:

- landing page profissional;
- downloads automáticos da Release mais recente;
- Política de Privacidade e Termos de Serviço;
- OAuth de Spotify e Google/YouTube;
- migração Spotify → YouTube e YouTube → Spotify;
- correspondência automática com revisão manual para resultados incertos;
- sessões protegidas em cookies HttpOnly criptografados.

A versão Web usa as APIs oficiais para operações autenticadas. Veja **[WEB_DEPLOY.md](WEB_DEPLOY.md)** para publicar no Netlify e configurar OAuth com segurança.

## Como usar

1. Conecte Spotify e Google/YouTube.
2. Escolha **Spotify ➔ YouTube Music** ou **YouTube Music ➔ Spotify**.
3. Cole o link/ID da playlist.
4. Escolha o nome da playlist de destino.
5. Inicie a migração e revise eventuais correspondências incertas.

## Estrutura

- `app.py` — interface desktop.
- `nucleo.py` — lógica compartilhada dos apps nativos.
- `celular/` — versão Android em Flet.
- `site/` — site público e interface da migração Web.
- `netlify/functions/api.js` — OAuth e integração serverless da versão Web.
- `netlify.toml` — configuração de deploy, rotas e headers de segurança.
- `WEB_DEPLOY.md` — guia de configuração do site e OAuth.
- `.github/workflows/build.yml` — build automático do Windows.
- `.github/workflows/build-android.yml` — build automático do APK.

## Rodar pelo código-fonte no PC

```bash
pip install -r requirements.txt
python app.py
```

## Privacidade

Nos aplicativos nativos, tokens, configurações e progresso ficam no dispositivo do usuário. Na versão Web, tokens OAuth são armazenados em cookies HttpOnly criptografados e não são expostos ao JavaScript da página. A versão Web não exige banco de dados para armazenar contas ou histórico de usuários.

Leia também `site/privacidade.html`.

## Licença

MIT.
