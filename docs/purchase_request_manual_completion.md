# Conclusão manual auditada

Autorizada por PAULO em 28/10/2026 para encerrar solicitações cuja necessidade
foi atendida sem uma O.C. vinculada. Não representa recebimento físico e não cria
pedido, alocação, movimentação de estoque ou alteração em O.S.

Comprador/ADMIN pode usar `CONCLUIR_MANUAL` em SOLICITADA ou EM_COMPRAS. O backend
exige versão atual, confirmação com o ID exato, motivo e evidência de atendimento
(10 a 4.000 caracteres). Data, responsável, antes/depois e evidência ficam no
histórico. A conclusão é identificada como MANUAL, separada de PEDIDO.

Vínculos anteriores são preservados. A quantidade não alocada fica identificada
como atendida sem pedido e não como compra efetuada. Não é permitido redistribuir
pedidos de uma conclusão manual sem primeiro reabri-la. Reabrir exige comprador,
motivo, versão e ausência de duplicidade pendente; restaura o saldo pendente e
preserva a evidência original no evento anterior.

A migração `20261028201154_purchase_request_manual_completion.sql` é aditiva e
retrocompatível: registros anteriores ficam PEDIDO, com seus status e vínculos
intactos. Nenhuma permissão, política RLS ou função privilegiada é criada.
Publicar Estoque e depois a interface do Suprimentos. O arquivo de exportação
preserva as colunas anteriores e acrescenta modo, motivo e evidência de conclusão.
