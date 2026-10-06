# Idiomas

`rhyft_i18n.py` e `locales/*.json` são a fonte compartilhada. O build Android copia esses arquivos para `celular/src`; o PyInstaller inclui os catálogos no EXE.

O onboarding e os controles principais usam dez idiomas. Instruções avançadas, logs técnicos e diagnósticos dos provedores ainda podem usar o idioma original. As chaves desconhecidas preservam seu texto; uma chave ausente em um catálogo usa o inglês como fallback.

Para adicionar um idioma, registre seu nome nativo em `LANGUAGES`, inclua um JSON com todas as chaves do catálogo inglês e amplie os testes de normalização. Use `tr()` nos pontos que apresentam texto, sem traduzir identificadores, URLs ou valores internos. Mantenha placeholders iguais em todos os idiomas.

A preferência fica em `language.json` no diretório privado de dados do aplicativo, separada das credenciais e de `settings.json`. O arquivo é gravado atomicamente. Uma preferência inválida ou corrompida reabre o seletor.

No Windows, a detecção consulta a configuração de idioma do usuário. No Android, consulta `Page.get_device_info().locales` do Flet, respeitando a ordem do sistema. Variantes regionais são normalizadas; chinês tradicional e simplificado permanecem separados. Idiomas não suportados usam a próxima preferência reconhecida ou inglês.

O título inicial permanece por 4,2 segundos, seguido de transições de 250 ms e pausas de 1,8 segundos. Selecionar manualmente interrompe a animação. A troca posterior vale na próxima abertura, sem recriar controles durante uma migração.

Validação: `python -m unittest discover -s tests -v`, com as dependências de `requirements.txt`, `flet==1.0.3` e `PyYAML`. Os testes de janela desktop exigem Windows.
