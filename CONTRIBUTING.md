# Contribuindo com o RHYFT

Obrigado por querer contribuir com o RHYFT.

O projeto é open source e aceita correções, melhorias de interface, documentação, testes e sugestões relacionadas à migração de playlists entre Spotify e YouTube Music.

## Antes de começar

1. Procure uma issue existente para evitar trabalho duplicado.
2. Para mudanças maiores, abra uma issue descrevendo a ideia antes de implementar.
3. Nunca envie tokens, Client Secrets, cookies, credenciais ou dados pessoais em commits, issues ou logs.

## Desenvolvimento local

### Windows / desktop

```bash
pip install -r requirements.txt
python app.py
```

### Estrutura principal

- `app.py`: interface desktop
- `nucleo.py`: lógica compartilhada
- `celular/`: aplicativo Android
- `site/`: site e interface Web
- `netlify/functions/`: backend da versão Web
- `.github/workflows/`: builds e validações automáticas

## Pull requests

- Mantenha o PR focado em uma mudança principal.
- Explique o problema e a solução.
- Inclua passos de teste quando aplicável.
- Não altere identificadores técnicos do Android ou caminhos de dados persistentes sem justificar compatibilidade.
- Preserve o fluxo de OAuth e não adicione credenciais ao código.

## Bugs

Ao relatar um bug, inclua:

- plataforma: Windows, Android ou Web;
- versão do RHYFT;
- passos para reproduzir;
- comportamento esperado e comportamento observado;
- logs relevantes sem tokens ou informações privadas.

## Segurança

Falhas de segurança não devem ser publicadas com detalhes exploráveis em uma issue comum. Consulte [SECURITY.md](./SECURITY.md).

## Licença

Ao contribuir, você concorda que sua contribuição seja distribuída sob a licença MIT do projeto.
