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

## Retomar uma migração Web

O destino e o progresso são salvos no histórico deste navegador assim que a playlist é criada, e atualizados a cada envio confirmado. Use **Retomar / atualizar** no histórico ou inicie novamente com a mesma playlist de origem e o mesmo sentido. Links do Spotify, URIs e IDs equivalentes identificam a mesma origem; links do YouTube e YouTube Music também.

Antes de continuar, o site consulta as faixas presentes no destino para evitar duplicatas. Migrações do histórico antigo também reutilizam o link de destino salvo. Faixas com erro podem ser tentadas novamente e escolhas manuais pendentes são preservadas ao recarregar a página. Uma playlist removida ou sem acesso interrompe a retomada; o site não cria outra automaticamente.

A busca separa créditos de artistas do título, reconhece canais Topic/VEVO e ignora rótulos como “Official Audio” e “lyrics”. Título, artista, versão e duração determinam a correspondência; covers, remixes, gravações ao vivo e durações incompatíveis ficam para revisão. A busca no YouTube considera até dez resultados por consulta; uma segunda consulta mais específica ocorre somente quando a primeira não encontra uma correspondência clara. Pendências salvas por versões anteriores são reavaliadas ao retomar, aproveitando os candidatos já disponíveis. Cada faixa ainda incerta mostra o motivo e um link para ouvir; opções resolvidas saem da lista.

Leituras que falham temporariamente têm tentativas limitadas. Envios não são repetidos automaticamente: uma falha pode ocorrer depois de a plataforma aceitar a faixa. Nesse caso, o site confere o destino e interrompe se não conseguir confirmar o envio. Cotas e autorizações continuam dependendo das plataformas. Limpar o histórico ou trocar de navegador remove a informação local necessária para localizar o destino.

Buscas têm até **três tentativas**. As chamadas à API Web do YouTube começam com pelo menos 2s de intervalo, e o servidor espaça em 1s as chamadas de busca e de detalhes que compõem cada pesquisa. Um limite temporário suspende todas as chamadas daquela plataforma neste navegador: no YouTube, a espera cresce de 60s para 120s e depois 240s; no Spotify, de 30s para 60s e 120s. Prazos maiores de `Retry-After` e do `RetryInfo` enviado pelo Google são respeitados. Depois do bloqueio, o ritmo do YouTube fica mais lento, entre 4s e 10s por chamada à API Web. Essa pausa e o ritmo são compartilhados entre abas e persistem ao recarregar; o intervalo normal volta após 30 minutos sem chamadas. A espera mostra uma contagem regressiva e permite pausar ou cancelar, sem acionar o timeout de rede.

Se a busca ainda falhar, a faixa fica com erro para tentar novamente na retomada, e a migração segue com as próximas **respeitando a pausa restante da plataforma**, sem reiniciar a espera curta a cada faixa. O servidor deixa a espera por limite temporário para o navegador, sem multiplicar essas três tentativas. `quotaExceeded`, `dailyLimitExceeded` e descrições explícitas de limite diário indicam cota esgotada e interrompem a migração; limites identificados por minuto/segundo permitem nova tentativa. Falhas de sessão, criação ou envio também não são tratadas como uma busca que pode ser pulada.

A pausa controla as chamadas deste navegador. O projeto Google é compartilhado pelo site, e o uso de outros navegadores também consome seus limites. Se o bloqueio persistir, o responsável deve verificar **YouTube Data API v3 → Quotas e métricas** no Google Cloud para identificar o limite efetivamente atingido; uma cota externa não pode ser ampliada pelo JavaScript.

O log identifica a etapa e a plataforma que falharam, com HTTP, código do erro, motivo enviado pela API e número de tentativas. Um timeout local informa seu prazo; uma resposta `ABORTED` indica interrupção sem presumir timeout ou cota. Falhas durante a consulta dos detalhes de vídeos e a renovação de sessão também recebem contexto. As tentativas de leitura e a conferência de um envio sem resposta aparecem no log. URLs de requisição, tokens e cookies não fazem parte desses detalhes.

## Testes da versão Web

Os testes da API usam apenas Node.js 22 ou superior:

```bash
node --test tests/web_api.test.cjs tests/web_matching.test.cjs tests/web_pacing.test.cjs
```

Os testes da interface executam Chromium com Playwright e respostas simuladas das APIs, sem credenciais ou alterações em playlists reais. Instale as ferramentas fora do checkout:

```bash
npm install --prefix /tmp/rhyft-web-tests playwright@1.62.1
/tmp/rhyft-web-tests/node_modules/.bin/playwright install chromium
NODE_PATH=/tmp/rhyft-web-tests/node_modules node --test tests/web_migration.test.cjs
```

Se Chromium já estiver instalado, defina `CHROMIUM_PATH` para seu executável e omita o download do navegador. Os testes cobrem os dois sentidos, retomada pelo histórico antigo e por links equivalentes, recarregamento, falhas temporárias, respostas de escrita perdidas, revisão manual e cancelamento.

## 1. Publicar no Netlify

Importe este repositório no Netlify. O `netlify.toml` já configura:

- pasta publicada: `site`
- funções: `netlify/functions`
- rota da API: `/api/*`
- headers de segurança

## 2. Variáveis de ambiente no Netlify

Em **Site configuration → Environment variables**, crie:

- `SITE_URL` — URL final, sem barra no fim. Ex.: `https://migrador.seudominio.com`
- `SPOTIFY_CLIENT_ID` — opcional: Client ID do app compartilhado do site no Spotify Developer Dashboard. Cada pessoa também pode usar um Client ID próprio pelo botão Conectar.
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

Ao clicar em **Conectar**, a Web mostra uma janela compacta com as opções **Configurar meu acesso**, **Já tenho um Client ID** e, quando disponível, **Conectar pelo app do site**. O tutorial apresenta uma etapa por vez: criar o app, salvar a Redirect URI e colar o Client ID. Quem já salvou um ID vai diretamente para a conexão. A Redirect URI correta vem da configuração do servidor e pode ser copiada no tutorial. O navegador lembra apenas esse identificador público; não solicita nem armazena Client Secret, tokens ou senhas no histórico/localStorage. A página de consentimento do Spotify mostra o nome cadastrado no aplicativo selecionado.

O Client ID escolhido fica vinculado à tentativa de login e à sessão criptografada. A troca de código e as renovações posteriores usam esse mesmo aplicativo, mesmo quando o site possui outro `SPOTIFY_CLIENT_ID` configurado. Sessões antigas continuam usando o app compartilhado.

### Limitação importante para uso público

Em **Development Mode**, o Spotify permite somente um pequeno grupo de usuários autorizados no app. Portanto, mesmo com o site público, contas fora da allowlist podem conseguir fazer login e depois receber `403` ao chamar a Web API.

Para remover essa allowlist, o aplicativo do Spotify precisa estar em **Extended Quota Mode**. As regras atuais do Spotify para solicitar esse modo são bastante restritivas e voltadas a organizações/serviços já estabelecidos. Isso é uma limitação da plataforma Spotify, não do código do RHYFT.

Enquanto o app Spotify estiver em Development Mode, use a versão Web como beta/teste e mantenha as versões nativas como alternativa para o público.

Se aparecer **“The user is not registered for this application”**, o dono do aplicativo deve cadastrar o e-mail da conta Spotify em **User Management**, e a pessoa deve conectar novamente com essa conta. A Web agora explica isso e oferece o tutorial de Client ID pessoal. Criar um aplicativo próprio continua sujeito às exigências de conta/criação do Spotify; não remove cotas, limitações de playlists ou regras da plataforma. Uma conta recusada pela API não é mais apresentada como conectada após o callback.

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

## Observação sobre playlists do Spotify em 2026

Além do limite de usuários do Development Mode, a API atual do Spotify restringe a leitura de itens de playlist a playlists que a conta atual possui ou em que é colaboradora. Se o Spotify devolver `403` para uma playlist de terceiros, crie uma cópia na sua própria conta e migre essa cópia.
