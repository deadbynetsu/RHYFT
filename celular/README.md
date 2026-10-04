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

1. **Spotify:** use o mesmo Client ID da versão para PC e autorize pelo navegador.
2. **YouTube Music:** no PC, abra **Vincular o YouTube Music → Copiar para o celular** e importe o texto no app mobile. Esse texto pode conter cookies de login e deve ser tratado como senha.
3. Escolha o sentido da migração, cole o link da playlist e toque em **Iniciar migração**.

Deixe o app aberto enquanto a migração acontece; o Android pode pausar aplicativos em segundo plano.