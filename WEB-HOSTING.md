# Executar a versão Web fora do Netlify

O pacote Web executa o site e sua API em um servidor Node.js 22 ou superior, sem instalar dependências npm. Ele pode rodar no seu PC, em Docker ou em um host público. Os aplicativos Android e Windows têm distribuição separada.

Arquivos HTML sozinhos permitem abrir a interface, mas a migração exige o backend para OAuth e operações nas playlists. Este guia e o `render.yaml` são configuração de exemplo: não representam um novo site já publicado nem garantem um plano gratuito.

## Abrir no seu PC Windows

1. Instale o [Node.js 22 ou superior](https://nodejs.org/en/download).
2. Extraia o pacote Web em uma pasta e copie `.env.web.example` para `.env.web`.
3. Preencha `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` com o cliente Google Web que você já usa. Quem administra a instalação pode copiar esses valores das configurações de ambiente atuais, como as do Netlify, diretamente para o arquivo local. `SPOTIFY_CLIENT_ID` é opcional; a conexão por Client ID pessoal continua disponível.
4. Cadastre os endereços locais de OAuth descritos abaixo e abra `iniciar-web.cmd`.

O launcher mantém a janela do servidor aberta e abre `http://127.0.0.1:8787/migrar.html` no navegador **do PC onde foi executado**. Esse endereço não é uma URL pública nem uma prévia acessível de outro computador. Fechar a janela encerra o servidor.

O launcher cria um segredo aleatório em `.rhyft-web-session-secret` e reutiliza esse arquivo nas próximas execuções. Não é preciso preencher `SESSION_SECRET` para essa execução local. As variáveis definidas pelo launcher têm prioridade sobre `.env.web`, incluindo os endereços locais e o segredo. Os cookies OAuth ficam no navegador. O histórico pertence ao navegador e ao endereço do site: mudar do Netlify para localhost ou outro domínio não transfere automaticamente as migrações anteriores.

## Endereços OAuth por instalação

Uma origem nova precisa ser cadastrada nos clientes OAuth uma vez. Use os mesmos clientes existentes quando permitido; os endereços do Netlify podem continuar cadastrados enquanto aquele site estiver em uso.

| Configuração | Execução local | Hospedagem pública |
|---|---|---|
| `SITE_URL` | `http://127.0.0.1:8787` | `https://rhyft.seudominio.com` |
| Origem autorizada no cliente Google Web | `http://127.0.0.1:8787` | `https://rhyft.seudominio.com` |
| Redirect URI do Google | `http://127.0.0.1:8787/api/google/callback` | `https://rhyft.seudominio.com/api/google/callback` |
| Redirect URI do Spotify | `http://127.0.0.1:8787/api/spotify/callback` | `https://rhyft.seudominio.com/api/spotify/callback` |

Configure o Google em **Google Auth Platform → Clientes → seu cliente Web** e o Spotify nas configurações do aplicativo escolhido. Uma pessoa usando Client ID próprio deve cadastrar o callback do Spotify nesse próprio aplicativo. Restrições de contas autorizadas pelas plataformas continuam valendo.

## Hospedagem pública no Render

O `render.yaml` descreve um serviço Docker usando `Dockerfile.web`. Para publicar este pacote:

1. Crie um repositório GitHub separado para a versão Web, por exemplo `RHYFT-WEB`.
2. Extraia o ZIP e envie somente os arquivos e diretórios originais do pacote para a raiz desse novo repositório. Mantenha `.env.web` e `.rhyft-web-session-secret`, gerados ao configurar a instalação, apenas na sua máquina. O upload manual no GitHub não aplica as regras do `.gitignore`. O Render precisa ler os arquivos extraídos; enviar apenas o ZIP não prepara o serviço.
3. No Render, importe esse novo repositório como Blueprint ou crie um Web Service Docker e selecione `Dockerfile.web`.

Use o conteúdo deste pacote, pois essas alterações não são publicadas automaticamente no repositório original nem no Netlify. Escolha o plano conforme a disponibilidade e os preços exibidos pelo provedor.

No serviço, configure:

- `SITE_URL`: a URL HTTPS final do serviço ou domínio, sem barra ao final.
- `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`: os valores do cliente Google Web existente.
- `SESSION_SECRET`: o Blueprint gera um valor aleatório; conserve-o entre reinícios e atualizações para manter as sessões.
- `SPOTIFY_CLIENT_ID`: opcional para oferecer a conexão pelo app compartilhado.

O Blueprint define `HOST=0.0.0.0`, `TRUST_PROXY=1` e `NETLIFY_DEV=false`. O servidor respeita o `PORT` fornecido pelo host; a verificação de funcionamento é `/health`. Cadastre os callbacks da URL final antes de testar o login. O arquivo de configuração não cria ou publica um serviço por si só.

## Docker e VPS com HTTPS

Para um VPS, envie o ZIP diretamente ao servidor e extraia os arquivos em uma pasta própria. Não é necessário um repositório GitHub para esse caminho. Crie a imagem na pasta do pacote extraído:

```bash
docker build -f Dockerfile.web -t rhyft-web .
```

Copie `.env.web.example` para `.env.web` no host e preencha as credenciais. Para gerar um `SESSION_SECRET` no arquivo, sem exibi-lo, use este comando com Node.js instalado no host:

```bash
node -e "const fs=require('node:fs'),crypto=require('node:crypto'),p='.env.web';let text=fs.readFileSync(p,'utf8');if(/^SESSION_SECRET=.{32,}$/m.test(text)===false){const line='SESSION_SECRET='+crypto.randomBytes(48).toString('hex');text=/^SESSION_SECRET=.*$/m.test(text)?text.replace(/^SESSION_SECRET=.*$/m,line):text+'\n'+line+'\n';fs.writeFileSync(p,text,{mode:0o600})}"
```

Inicie o container atrás de um proxy HTTPS, usando sua URL pública:

```bash
docker run -d --name rhyft-web --restart unless-stopped \
  -p 127.0.0.1:8787:8787 --env-file .env.web \
  -e HOST=0.0.0.0 -e PORT=8787 \
  -e SITE_URL=https://rhyft.seudominio.com \
  -e TRUST_PROXY=1 -e NETLIFY_DEV=false \
  rhyft-web
```

No VPS, encaminhe seu domínio HTTPS para `http://127.0.0.1:8787` com um proxy como Caddy ou Nginx. Os parâmetros `-e` acima substituem os valores locais do arquivo de exemplo. A imagem roda com o usuário `node` e copia somente o servidor, as funções e o site; as credenciais são fornecidas na execução.

Para testar Docker apenas no próprio PC, use a URL local e cookies locais:

```bash
docker run --rm -p 127.0.0.1:8787:8787 --env-file .env.web \
  -e HOST=0.0.0.0 -e PORT=8787 \
  -e SITE_URL=http://127.0.0.1:8787 \
  -e TRUST_PROXY=0 -e NETLIFY_DEV=true \
  rhyft-web
```

Sem Docker, o mesmo servidor pode ser executado diretamente em um VPS com Node.js, mantendo o processo sob um gerenciador de serviços e um proxy HTTPS:

```bash
HOST=127.0.0.1 PORT=8787 SITE_URL=https://rhyft.seudominio.com \
  TRUST_PROXY=1 NETLIFY_DEV=false \
  node --env-file=.env.web web-server.cjs
```

## Conteúdo do pacote ZIP

Inclua estes arquivos e diretórios, preservando os caminhos:

```text
web-server.cjs
iniciar-web.cmd
.env.web.example
.gitignore
Dockerfile.web
Dockerfile.web.dockerignore
render.yaml
WEB-HOSTING.md
site/
netlify/functions/
```

`netlify/functions/` precisa incluir `api.js` e `lib/`; a pasta `site/` precisa conter todos os recursos da interface. O pacote dispensa `app.py`, `celular/` e executáveis nativos. Distribua somente o exemplo de ambiente: `.env.web` e `.rhyft-web-session-secret` permanecem na instalação de cada usuário.

## Busca e limites

A busca usa o catálogo público anônimo do YouTube Music pelo backend, seguindo o fluxo já usado pelo Android. A busca Web não chama `search.list` nem `videos.list` da YouTube Data API. Google OAuth e a API oficial continuam necessários para ler, criar e atualizar playlists; essas operações ainda têm cotas. O catálogo público e o Spotify também têm limites próprios. Trocar a hospedagem não remove restrições impostas por essas plataformas.
