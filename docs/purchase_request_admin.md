# Administração do histórico de solicitações de compra

## Acesso

Na tabela de solicitações (inclusive usando filtros Concluído ou Cancelada), o
menu Ações de cada linha oferece exclusivamente ao ADMIN ativo:

- **Alterar quantidade (ADMIN)**: corrige somente a quantidade solicitada, em
  qualquer status. Exige quantidade positiva, até três casas decimais, motivo e
  versão atual. Grava antes/depois, autor e horário na rastreabilidade.
- **Excluir definitivamente**: abre confirmação explícita. Apaga a solicitação,
  todos os seus eventos, seus vínculos de referência e relatório de normalização.
  A linha deixa de existir no sistema e em futuras exportações de ambas as abas.
  Não é cancelamento e não pode ser reaberta.

As ações administrativas não alteram O.C., O.S., recebimentos, movimentos ou
saldos de estoque. Corrigir a quantidade solicitada não corrige a quantidade de
um pedido emitido; esse documento continua preservado. Exportações antigas já
baixadas e backups não são alterados.

O comportamento anterior de Editar/Excluir permanece para os demais usuários:
somente nas situações já permitidas e com exclusão lógica auditada.

## Integração

Publicar Estoque (serviço canônico) e Suprimentos (proxy/interface). Não há
migração de estrutura. Ambos oferecem interface administrativa, mas o Estoque
sempre verifica o perfil ativo obtido no servidor, independentemente dos campos
do JSON. Suprimentos valida adicionalmente perfil e CSRF antes de encaminhar.

As ações são POST em `purchase-requests/{uuid}/action`, com `version`:

- `EDITAR_QUANTIDADE_ADMIN`: `quantity` e `reason`.
- `EXCLUIR_DEFINITIVO`: `confirmation` contendo exatamente o UUID da linha.

O bloqueio da linha, controle de versão e transação única evitam exclusões ou
correções sobre dados desatualizados. Dependências são removidas antes do pai,
respeitando as FKs existentes; qualquer falha provoca rollback de tudo.

## Verificação local em 21/10/2026

- 8 testes Python novos de domínio/rotas: todos aprovados.
- 14 testes de Excel: aprovados, incluindo ausência de registros apagados.
- Workflow Estoque: execução combinada de 98 testes, 97 aprovados; a única falha
  é preexistente e foi reproduzida com o código HEAD sem alterações:
  `test_listing_filters_and_notifications` usa vencimento fixo em 15/10/2026,
  esperando apenas uma pendência vencida mesmo após essa data.
- 26 testes Python de integração Suprimentos: aprovados.
- 4 testes JavaScript administrativos em cada módulo e os 7 de conclusão:
  aprovados. Nenhum dado de produção foi alterado para testar.
