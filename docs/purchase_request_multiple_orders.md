# Múltiplos pedidos por solicitação de compra

## Uso

Comprador/ADMIN abre **Ações → Gerenciar pedidos**, preenche a quantidade de cada
O.C. e a justificativa. Pedidos existentes são preenchidos com a divisão atual;
zero não participa da nova distribuição. Salvar substitui a distribuição anterior,
sem apagar o antes/depois do histórico. Até 100 pedidos por operação.

- Soma menor que a quantidade solicitada: EM_COMPRAS, com quantidade restante.
- Soma igual: CONCLUIDA, vinculada a todos os pedidos. Revisar a distribuição de
  uma solicitação já concluída preserva a data/autor originais de conclusão se
  ela continuar integralmente atendida. Cobertura parcial a reabre em compras.
- Soma maior, número inválido, SKU incompatível, quantidade não positiva, mais de
  três decimais ou saldo insuficiente: operação rejeitada integralmente.
- Pedidos já recebidos/concluídos mantêm os vínculos existentes; não permitem
  alocação adicional sobre um saldo de recebimento inexistente.

O saldo é calculado por componente da B.O.M., como antes, inclusive para pedidos
de conjuntos. As alocações parciais também descontam a disponibilidade para outras
solicitações. Não há movimentos físicos de estoque nem alteração na quantidade
do pedido. Criar nova O.C. pelas solicitações selecionadas prepara somente a
quantidade ainda não atendida, após conversão de antecipação em nova compra quando
aplicável; a emissão não autoriza quantidade menor que o restante selecionado.

Cancelar uma O.C. cancela somente seu vínculo. Os demais vínculos ficam ativos e
a solicitação volta a ficar pendente apenas da quantidade faltante. Editar qualquer
O.C. vinculada verifica a sua parcela, inclusive quando ela não é o pedido principal.
Cancelar/excluir logicamente a solicitação libera todos seus vínculos. A exclusão
administrativa também remove as dependências novas, sujeita à proteção imutável
preexistente dos eventos PostgreSQL (problema legado independente deste ajuste).

## Contrato e concorrência

POST `purchase-requests/{uuid}/action`, action `ALOCAR_PEDIDO`, `version`, `reason`,
`allocations: [{purchase_order_id, quantity}, ...]`, `replace_allocations: true`.
O payload antigo com `purchase_order_id` único continua válido para concluir uma
solicitação pendente. Sem replace_allocations o novo payload adiciona parcelas.

Perfil/CSRF continuam validados nos servidores dos dois módulos. Bloqueio da
solicitação + versão evita reenvio duplicado. Pedidos são bloqueados em ordem com
NOWAIT para evitar inversão de locks contra edição/cancelamento canônico; conflito
exige atualizar/repetir e a transação falha sem alocação parcial.

Lista, busca por qualquer O.C., detalhes e histórico expõem `purchase_orders`,
`allocated_quantity`, `remaining_quantity`. O campo escalar `purchase_order_id`
permanece como âncora de compatibilidade apenas para conclusão integral. Excel
mostra todas as O.C.s e suas parcelas sem multiplicar a solicitação. Na exportação
por componentes, as parcelas são expressas na unidade da solicitação original.

## Publicação

1. Aplicar `supabase/migrations/20261022125157_purchase_request_multiple_orders.sql`.
   Gerada com CLI Supabase. Tabela aditiva com FKs, PK composta, quantidade positiva,
   índice por O.C./status, RLS sem acesso anon/authenticated. Somente serviços de
   backend têm acesso. Backfill copia o único vínculo das solicitações já concluídas,
   sem alterar as solicitações, O.C.s, eventos, recebimentos, O.S. ou saldos.
2. Publicar Estoque primeiro, depois Suprimentos. Não publicar a interface sozinha.
3. Conferir em leitura vínculos históricos e o contrato da API. Não criar pedidos
   ou movimentações reais como teste de publicação.

## Registro de publicação — 22/10/2026

Migração aplicada no projeto JI MONTADORA antes dos deploys, versão sincronizada
com o histórico remoto `20261022125157`. Os quatro vínculos concluídos foram
copiados sem divergências. As 39 solicitações conservaram o mesmo hash integral;
65 eventos e 220 pedidos preservados. RLS ativo, sem SELECT para anon/authenticated.
Validação: 123 testes Estoque, 26 de integração Suprimentos e 23 JavaScript aprovados.
Nenhum pedido, movimento físico, recebimento ou solicitação real criado como teste.
