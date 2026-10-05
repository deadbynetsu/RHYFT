# Site oficial + Migração Web (Netlify)

O repositório agora contém um site completo em `site/` e uma API serverless em `netlify/functions/api.js`.

## O que já está pronto

- Landing page profissional com downloads automáticos da Release mais recente.
- Política de Privacidade pública em `/privacidade.html`.
- Termos de Serviço públicos em `/termos.html`.
- Migração online em `/migrar.html`.
- Login OAuth de Spotify e Google/YouTube.
- Tokens Web protegidos em cookies `HttpOnly` criptografados (não ficam expostos ao JavaScript).
- Spotify → YouTube: lê a playlist, pesquisa correspondências, cria playlist privada e adiciona vídeos.
- YouTube → Spotify: lê a playlist, pesquisa faixas, cria playlist privada e adiciona músicas.
- Resultados de baixa confiança ficam para escolha manual.
- Sem banco de dados obrigatório.

## 1. Publicar no Netlify

Importe este repositório no Netlify. O `netlify.toml` já configura:

- pasta publicada: `site`
- funções: `netlify/functions`
- rota da API: `/api/*`
- headers de segurança

## 2. Variáveis de ambiente no Netlify

Em **Site configuration → Environment variables**, crie:

- `SITE_URL` — URL final, sem barra no fim. Ex.: `https://migrador.seudominio.com`
- `SPOTIFY_CLIENT_ID` — Client ID do seu app no Spotify Developer Dashboard
- `GOOGLE_CLIENT_ID` — Client ID de um cliente OAuth do tipo **Aplicativo da Web**
- `GOOGLE_CLIENT_SECRET` — Secret desse mesmo cliente Web
- `SESSION_SECRET` — texto aleatório forte com pelo menos 32 caracteres (ideal: 64+)

Nunca coloque `GOOGLE_CLIENT_SECRET` ou `SESSION_SECRET` no JavaScript do site ou em arquivos públicos do GitHub.

## 3. Spotify OAuth

No Spotify Developer Dashboard, adicione esta Redirect URI ao app:

`https://SEU-DOMINIO/api/spotify/callback`

Exemplo:

`https://migrador.seudominio.com/api/spotify/callback`

O site usa Authorization Code + PKCE.

## 4. Google OAuth

O cliente usado pelo Android/TV **não é o ideal para o site**. Crie um cliente OAuth separado:

**Google Auth Platform → Clientes → Criar cliente → Aplicativo da Web**

Adicione:

### Origens JavaScript autorizadas

`https://SEU-DOMINIO`

### URIs de redirecionamento autorizados

`https://SEU-DOMINIO/api/google/callback`

Depois coloque o Client ID e Client Secret desse cliente nas variáveis do Netlify.

A YouTube Data API v3 precisa continuar ativada no projeto.

## 5. Branding / verificação do Google

Use no Google Auth Platform:

- Página inicial: `https://SEU-DOMINIO/`
- Política de Privacidade: `https://SEU-DOMINIO/privacidade.html`
- Termos: `https://SEU-DOMINIO/termos.html`

Depois publique o app em **Público-alvo → Publicar app**. Para remover avisos e limites destinados a apps não verificados, conclua a verificação exigida pelo Google para o escopo do YouTube.

## 6. Cota do YouTube

A versão Web usa a YouTube Data API oficial. Operações de busca e escrita consomem a cota do projeto Google. Com uso público, será necessário acompanhar a cota e, quando necessário, solicitar aumento no Google Cloud.

A versão Web foi desenhada para mostrar uma mensagem clara caso a cota seja atingida, sem expor tokens do usuário.

## 7. Atualização automática dos downloads

A Home consulta a API pública do GitHub para a Release mais recente. Quando uma nova Release é publicada, os botões passam a apontar automaticamente para os novos assets de Windows e Android. Não é necessário editar o HTML a cada versão.

## Segurança

- OAuth `state` é validado.
- Spotify usa PKCE.
- Google Client Secret fica apenas na função serverless.
- Tokens de usuário ficam criptografados em cookies `HttpOnly`, `Secure` e `SameSite=Lax`.
- Endpoints de escrita verificam a origem da requisição.
- CSP e outros headers de segurança são definidos no `netlify.toml`.
- Playlists criadas pela versão Web são privadas por padrão.

## Observação sobre o Spotify em 2026

A API atual do Spotify restringe a leitura de itens de playlist a playlists que a conta atual possui ou em que é colaboradora. Se o Spotify devolver 403 para uma playlist de terceiros, crie uma cópia na sua própria conta e migre essa cópia.
