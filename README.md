# Migrador de Playlists: Spotify ➔ YouTube Music

Programa gratuito para Windows que copia uma playlist do **Spotify** para o **YouTube Music**.
Roda no **seu computador**, com as **suas contas**. Nada passa por servidor do autor: seus
dados só são enviados ao Spotify e ao YouTube.

> Projeto independente, sem ligação com Spotify, Google ou YouTube.

## Antes de começar

- **Spotify Premium é necessário** para criar o app de acesso (exigência atual do Spotify para
  desenvolvedores). Só quem cria o app precisa; é uma criação única, de 2 minutos.
- O Spotify só libera o conteúdo de playlists **suas** ou **colaborativas**. Para migrar a playlist
  de outra pessoa, crie uma playlist sua e copie as músicas para ela.
- Você precisa estar logado no YouTube Music (music.youtube.com) em um navegador.

## Baixar e abrir

1. Vá em **Releases** (barra lateral direita desta página) e baixe `MigradorPlaylists-windows.zip`.
2. Extraia o zip e abra `MigradorPlaylists.exe`.
3. O Windows pode mostrar "O Windows protegeu seu computador", porque o programa não tem
   assinatura digital paga. Clique em **Mais informações → Executar assim mesmo**.
   Se preferir, confira o arquivo: o `.sha256` da mesma página deve bater com o resultado de
   `Get-FileHash MigradorPlaylists-windows.zip` no PowerShell. O código-fonte está todo aqui.

## Usar (a vinculação é feita uma vez só)

1. **1. Vincular Spotify:** siga o passo a passo da janela (criar um app no painel do Spotify,
   copiar o *Client ID* e autorizar no navegador).
2. **2. Vincular YouTube Music:** siga o passo a passo da janela. Em resumo: no music.youtube.com,
   aperte F12 → aba *Rede*, filtre por `browse`, clique com o botão direito em uma requisição POST
   → *Copiar* → **Copiar como cURL (bash)** → cole na janela. O programa testa a conexão antes de salvar.
3. Cole o link da playlist do Spotify, escolha o nome da nova playlist e clique em **Iniciar Migração**.

Se a migração for interrompida, é só rodar de novo com o mesmo nome de playlist: ela continua de onde parou.

## Privacidade e segurança

- Tudo fica em `%APPDATA%\MigradorPlaylists` (tokens, sessão do YouTube Music, progresso).
- **O texto copiado do YouTube Music contém cookies de login da sua conta Google. Trate como uma
  senha: não cole em chats, fóruns ou prints.** Para revogar, saia de todas as sessões em
  *Conta Google → Segurança* ou use **Desvincular** no programa.
- O programa não tem Client Secret nem credenciais embutidas.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| "O YouTube não aceitou a sessão copiada" | Refaça o passo 2 logado no music.youtube.com, copiando de uma requisição POST `browse`. |
| "O Spotify não deixou ler esta playlist" | A playlist não é sua. Copie as músicas para uma playlist sua e use o link dela. |
| "Não consegui abrir a porta 8080" | Feche outro programa que use essa porta e tente vincular de novo. |
| Qualquer outro erro | Abra um *Issue* aqui e anexe o `erros.log` (em `%APPDATA%\MigradorPlaylists`). **Antes de anexar, confira que não há cookies nem tokens no arquivo.** |

## Rodar pelo código-fonte (Windows, macOS ou Linux)

```bash
pip install -r requirements.txt
python app.py
```

Para gerar o `.exe` no Windows: `build.bat`.

## Avisos

O acesso ao YouTube Music usa a biblioteca não oficial [ytmusicapi](https://github.com/sigma67/ytmusicapi),
que pode deixar de funcionar se o Google mudar algo, e seu uso pode contrariar os termos do YouTube.
Use por sua conta e risco. Licença: MIT.
