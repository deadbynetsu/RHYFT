# Executar a versão Web fora do Netlify

O site e sua API podem ser hospedados fora do Netlify a partir do mesmo repositório principal: [deadbynetsu/RHYFT](https://github.com/deadbynetsu/RHYFT), branch `main`. O servidor usa Node.js 22 ou superior, sem dependências npm adicionais. Os aplicativos Android e Windows continuam no mesmo projeto, com distribuição separada.

Arquivos HTML sozinhos permitem abrir a interface, mas a migração exige o backend para OAuth e operações nas playlists. Este guia e o `render.yaml` são configuração de exemplo: não representam um novo site já publicado nem garantem um plano gratuito.

## Hospedagem pública no Render

O `render.yaml` descreve um serviço Docker usando `Dockerfile.web`, ligado ao repositório principal e à branch `main`. Para publicar a versão Web:

1. Abra [Deploy no Render](https://render.com/deploy?repo=https://github.com/deadbynetsu/RHYFT) ou crie um Blueprint no Render e conecte [deadbynetsu/RHYFT](https://github.com/deadbynetsu/RHYFT).
2. Selecione a branch `main` e o arquivo `render.yaml` da raiz. Como alternativa, crie um Web Service Docker para esse mesmo repositório e selecione `Dockerfile.web`.
3. Configure os valores abaixo e os callbacks da URL final. Não é necessário criar outro repositório ou enviar um ZIP ao Render.

O Blueprint solicita o plano `free`. Confirme a disponibilidade e os limites exibidos pelo Render antes de criar o serviço; o arquivo não indica que uma conta Render já foi vinculada ou que o site foi publicado.

No serviço, configure:

- `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`: os valores do cliente Google Web existente.
- `SESSION_SECRET`: o Blueprint gera um valor aleatório; conserve-o entre reinícios e atualizações para manter as sessões.
- `SPOTIFY_CLIENT_ID`: opcional para oferecer a conexão pelo app compartilhado.
- `SITE_URL`: opcional se você usar um domínio próprio; nesse caso, informe sua URL HTTPS sem barra ao final.

O endereço HTTPS `onrender.com` do serviço é detectado automaticamente pela variável `RENDER_EXTERNAL_URL` do Render. O Blueprint define `HOST=0.0.0.0`, `TRUST_PROXY=1` e `NETLIFY_DEV=false`. O servidor respeita o `PORT` fornecido pelo host; a verificação de funcionamento é `/health`. Quando o Render informar a URL final, cadastre seus callbacks antes de testar o login. O arquivo de configuração não cria ou publica um serviço por si só.

Depois de conectado ao Render, o serviço recebe deploys automáticos de alterações Web em `main`. O filtro `buildFilter.paths` inclui `site/**`, `netlify/functions/**`, `web-server.cjs`, `Dockerfile.web`, `Dockerfile.web.dockerignore` e `render.yaml`. Alterações apenas nos aplicativos nativos não acionam esse serviço Web.

### Publicação pontual no Netlify

A configuração permite um build Netlify somente para o commit com a mensagem exata `Deploy web fixes to Netlify`. Os outros commits continuam ignorados, inclusive atualizações futuras destinadas ao Render. Isso permite a tentativa pontual autorizada sem reativar todos os builds automáticos. Uma repetição manual desse deploy ainda pode gerar outra cobrança. Acessos ao site e chamadas às funções publicadas também podem consumir créditos da plataforma.

## Endereços OAuth por instalação

Uma origem nova precisa ser cadastrada nos clientes OAuth uma vez. Use os mesmos clientes existentes quando permitido; os endereços do Netlify podem continuar cadastrados enquanto aquele site estiver em uso.

| Configuração | Execução local | Hospedagem pública |
|---|---|---|
| `SITE_URL` | `http://127.0.0.1:8787` | `https://rhyft.seudominio.com` |
| Origem autorizada no cliente Google Web | `http://127.0.0.1:8787` | `https://rhyft.seudominio.com` |
| Redirect URI do Google | `http://127.0.0.1:8787/api/google/callback` | `https://rhyft.seudominio.com/api/google/callback` |
| Redirect URI do Spotify | `http://127.0.0.1:8787/api/spotify/callback` | `https://rhyft.seudominio.com/api/spotify/callback` |

Configure o Google em **Google Auth Platform → Clientes → seu cliente Web** e o Spotify nas configurações do aplicativo escolhido. Uma pessoa usando Client ID próprio deve cadastrar o callback do Spotify nesse próprio aplicativo. Restrições de contas autorizadas pelas plataformas continuam valendo.

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

## Abrir no seu PC Windows

1. Instale o [Node.js 22 ou superior](https://nodejs.org/en/download).
2. Extraia o pacote Web em uma pasta e copie `.env.web.example` para `.env.web`.
3. Preencha `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` com o cliente Google Web que você já usa. Quem administra a instalação pode copiar esses valores das configurações de ambiente atuais, como as do Netlify, diretamente para o arquivo local. `SPOTIFY_CLIENT_ID` é opcional; a conexão por Client ID pessoal continua disponível.
4. Cadastre os endereços locais de OAuth da tabela acima e abra `iniciar-web.cmd`.

O launcher mantém a janela do servidor aberta e abre `http://127.0.0.1:8787/migrar.html` no navegador **do PC onde foi executado**. Esse endereço não é uma URL pública nem uma prévia acessível de outro computador. Fechar a janela encerra o servidor.

O launcher cria um segredo aleatório em `.rhyft-web-session-secret` e reutiliza esse arquivo nas próximas execuções. Não é preciso preencher `SESSION_SECRET` para essa execução local. As variáveis definidas pelo launcher têm prioridade sobre `.env.web`, incluindo os endereços locais e o segredo. Os cookies OAuth ficam no navegador. O histórico pertence ao navegador e ao endereço do site: mudar do Netlify para localhost ou outro domínio não transfere automaticamente as migrações anteriores.

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
