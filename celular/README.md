# Migrador de Playlists: versão de celular (Android e iPhone)

Mesma ideia do app do computador, em Flet. **Toda a lógica está em `nucleo.py`**, o mesmo arquivo do PC
(o workflow copia o `nucleo.py` da raiz para cá antes de compilar, então não há duas versões do código).

## Gerar o APK (Android), sem instalar nada no seu computador
1. Coloque na raiz do repositório: `app.py`, `nucleo.py`, a pasta `celular/` e `.github/workflows/build-android.yml`.
2. No GitHub: **Actions > Build Android (APK) > Run workflow** (ou publique uma tag `v1.4.0`).
3. Quando terminar, baixe o arquivo `flet-apk.zip` no fim da execução e extraia o `.apk`.
4. No celular, permita "instalar apps desconhecidos" para o navegador/gerenciador de arquivos e instale o `.apk`.

## Primeiro uso
1. **Spotify:** use o **mesmo Client ID** do PC (o Redirect URI `http://127.0.0.1:8080` já serve). Toque em
   *Vincular / gerenciar*, cole o Client ID e *Vincular e autorizar*. Se o navegador mostrar erro de conexão
   depois de aceitar, copie o endereço da barra e cole no **plano B**.
2. **YouTube Music:** no app do PC (v1.4+), *Vincular o YouTube Music > Copiar para o celular*. Envie o texto para
   você mesmo e cole em *Importar do computador*. Ele contém seu login do Google: trate como senha e apague depois.
3. Escolha o sentido, cole o link da playlist e toque em *Iniciar migração*.

## Dicas e limites
- **Deixe o app aberto** enquanto migra: o Android pode pausar apps em segundo plano. O progresso é salvo a cada
  faixa confirmada, então dá para retomar com o mesmo link e o mesmo nome de playlist.
- Atualização: o app avisa de versões novas (botão *Verificar agora*); instale o novo `.apk` por cima.
- **iPhone:** o iOS só instala apps assinados. Precisa de macOS (o GitHub tem) e de uma conta **Apple Developer
  (paga)**; depois, descomente o job `ipa` do workflow e siga https://flet.dev/docs/publish/ (TestFlight/App Store).
