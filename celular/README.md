# Migrador de Playlists: versão de celular (Android)

Mesma ideia do app do computador, em Flet. A lógica fica em `nucleo.py`, o mesmo arquivo usado pela versão de PC. O workflow copia o `nucleo.py` da raiz para `celular/src/` antes de compilar.

## Gerar o APK pelo GitHub Actions

O workflow **Build Android (APK)** roda automaticamente quando há push na `main`, quando uma tag `v*` é publicada ou manualmente pela aba **Actions**.

No fim da execução, baixe o artifact **MigradorPlaylists-android** e extraia `flet-apk.zip` para encontrar o `.apk`.

## Assinatura do APK

Sem uma chave fixa, o APK ainda pode ser gerado para testes, mas futuras versões podem não instalar por cima da anterior. Para distribuição contínua, gere uma chave uma única vez:

```bash
pip install cryptography
python ferramentas/gerar_chave_android.py
```

O script cria `chave-android/migrador-chave.jks` e `segredos-para-o-github.txt`. Faça backup do `.jks`, nunca envie a chave ao GitHub e cadastre os quatro valores em **Settings → Secrets and variables → Actions**:

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_PASSWORD`
- `ANDROID_KEY_ALIAS`

Depois apague `segredos-para-o-github.txt` do computador.

## Primeiro uso

1. **Spotify:** use seu Client ID e autorize pelo navegador do próprio celular.
2. **YouTube Music:** toque em **Vincular / gerenciar**. Na primeira vez, informe um OAuth Client ID e Client Secret do Google do tipo **TVs and Limited Input devices**, com a **YouTube Data API v3** ativada. O app gera um código, abre o Google no próprio celular e detecta automaticamente quando a autorização termina.
3. Depois do primeiro login, as credenciais do cliente OAuth ficam guardadas na pasta privada do app, então as próximas vinculações exigem apenas tocar em **Entrar com Google**.
4. O método antigo de importar a sessão do computador continua disponível como **Plano B**, mas não é mais o fluxo principal.
5. Escolha o sentido da migração, cole o link da playlist e toque em **Iniciar migração**.

> Desde novembro de 2024, o YouTube Music/ytmusicapi exige um Client ID e Client Secret próprios para o fluxo OAuth. Por isso o app não consegue oferecer um login direto sem essas credenciais do Google.

Deixe o app aberto enquanto a migração acontece; o Android pode pausar aplicativos em segundo plano.