# Política de Segurança

## Versões suportadas

A versão mais recente publicada em [GitHub Releases](https://github.com/deadbynetsu/RHYFT/releases/latest) recebe prioridade para correções de segurança.

## Relatando uma vulnerabilidade

Se você encontrar uma vulnerabilidade, evite publicar detalhes que permitam exploração imediata em uma issue pública.

Entre em contato com o mantenedor pelo perfil oficial [@deadbynetsu](https://github.com/deadbynetsu) e informe, quando possível:

- componente afetado;
- versão e plataforma;
- passos mínimos para reproduzir;
- impacto esperado;
- evidências ou logs sem tokens, cookies, credenciais ou dados pessoais.

Relatos legítimos serão analisados e, quando necessário, a correção será preparada antes da divulgação pública dos detalhes.

## Credenciais e tokens

Nunca envie para o repositório:

- Client Secrets;
- access tokens ou refresh tokens;
- cookies de sessão;
- arquivos de autenticação pessoais;
- chaves privadas ou certificados privados.

O RHYFT utiliza autenticação das plataformas conectadas e o repositório público não deve conter credenciais de usuários.

## Builds oficiais

Os binários oficiais para Windows e Android são gerados pelos workflows públicos do GitHub Actions e distribuídos pela página de Releases. Sempre prefira os artefatos oficiais publicados neste repositório.
