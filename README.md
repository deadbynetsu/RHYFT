# Migrador de Playlists: Spotify ⇄ YouTube Music

Aplicativo gratuito para migrar playlists entre **Spotify** e **YouTube Music** nos dois sentidos. O projeto tem versão para **Windows** e **Android** usando o mesmo núcleo de migração.

> Projeto independente, sem ligação com Spotify, Google ou YouTube.

## Downloads pelo GitHub Actions

Cada atualização enviada para a branch `main` dispara dois workflows:

- **Build Windows** → gera `MigradorPlaylists-windows.zip`, contendo o executável para Windows.
- **Build Android (APK)** → gera `flet-apk.zip`, contendo o APK do Android.

Abra a aba **Actions**, entre no workflow concluído e baixe o arquivo em **Artifacts**. Quando uma tag `v*` é criada, os arquivos também são publicados em **Releases**.

## Windows

1. Baixe `MigradorPlaylists-windows.zip`.
2. Extraia o ZIP.
3. Abra `MigradorPlaylists.exe`.
4. Se o Windows SmartScreen aparecer, use **Mais informações → Executar assim mesmo**.

## Android

1. Baixe o artifact **MigradorPlaylists-android** no workflow **Build Android (APK)**.
2. Extraia `flet-apk.zip` e pegue o `.apk`.
3. Permita a instalação de apps desconhecidos no Android e instale o APK.

Para manter a mesma assinatura em atualizações futuras, siga `celular/README.md` e configure os segredos de assinatura.

## Como usar

1. Vincule o Spotify usando seu próprio Client ID.
2. Vincule o YouTube Music seguindo as instruções do app.
3. Escolha **Spotify ➔ YouTube Music** ou **YouTube Music ➔ Spotify**.
4. Cole o link da playlist, escolha o nome da playlist de destino e inicie a migração.

## Estrutura

- `app.py` — interface desktop.
- `nucleo.py` — lógica compartilhada entre desktop e mobile.
- `celular/` — projeto Flet do Android.
- `.github/workflows/build.yml` — build automático do Windows.
- `.github/workflows/build-android.yml` — build automático do APK.
- `ferramentas/gerar_chave_android.py` — gera uma chave de assinatura persistente.

## Rodar pelo código-fonte no PC

```bash
pip install -r requirements.txt
python app.py
```

## Privacidade

Tokens, sessões e progresso ficam no dispositivo do usuário. O projeto não usa servidor próprio para receber credenciais. O acesso ao YouTube Music usa a biblioteca não oficial `ytmusicapi`.

Licença: MIT.
