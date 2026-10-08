# Changelog

## 1.6.0

Windows e Android agora oferecem **Retomar / atualizar** no histórico local. Ao adicionar músicas à playlist de origem, a ação relê seu conteúdo, confere as faixas presentes no destino e envia apenas as que faltam para a mesma playlist. Migrações concluídas continuam disponíveis para novas atualizações.

O progresso passa a identificar a playlist de origem e o sentido da migração, além de vincular a conta de destino quando a plataforma fornece um identificador estável. Checkpoints antigos são preservados; quando falta a identificação da origem, o app solicita o link uma vez para associar a retomada. Músicas removidas da origem permanecem no destino.

Veja as [notas completas da v1.6.0](releases/v1.6.0.md).

## 1.5.2

Ajuste visual do app Windows para dar muito mais espaço ao registro de migração, especialmente em telas 1366×768 e janelas maximizadas. O cabeçalho, cartões, formulário, progresso e rodapé ficaram mais compactos verticalmente, sem remover recursos.

O rodapé agora usa uma única linha e o painel de log ganha uma altura base maior, continuando expansível conforme a janela cresce.

Veja as [notas completas da v1.5.2](releases/v1.5.2.md).

## 1.5.1

Hotfix de estabilidade do primeiro acesso no Windows. O seletor de idioma agora roda antes da criação da janela principal e é completamente encerrado antes do RHYFT abrir, eliminando o caso em que a primeira execução fechava a interface e deixava o processo preso em segundo plano.

Também foi adicionado um teste de regressão que conclui o onboarding e confirma a abertura da janela principal no mesmo processo.

Veja as [notas completas da v1.5.1](releases/v1.5.1.md).

## 1.5.0

Consolida o rebranding RHYFT, as melhorias de Windows/Android/Web e da comunidade acumuladas desde a v1.4.0, além do onboarding em dez idiomas e da publicação conjunta protegida por testes e builds.

Veja as [notas completas da v1.5.0](releases/v1.5.0.md).

## 1.4.0

Release anterior congelada. Durante seu ciclo de desenvolvimento, alguns artefatos foram substituídos no GitHub; as mudanças posteriores à tag são consolidadas na v1.5.0. A tag e os arquivos existentes da v1.4.0 não são alterados por este lançamento.
