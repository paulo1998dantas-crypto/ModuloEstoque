# Estorno integral de recebimento

Em Movimentações, uma entrada vinculada à inspeção oferece **Estornar recebimento completo**. A prévia lista todos os movimentos daquele UUID, fornecedor, NF, O.C., usuário original e possíveis bloqueios. Confirmar exige motivo e permissão `estoque.movement.cancel_any`.

A confirmação é feita na própria página, marcando a conferência da nota/itens e clicando no botão. O servidor valida essa confirmação; o formulário não depende de pop-up JavaScript, evitando travamentos do navegador e preservando a etapa de aprovação.

- O serviço existente `reverse_receipt` é a única fonte da operação. Todos os componentes, ajustes, vínculo de reversão, estado do recebimento, quantidades da O.C. e auditoria são atualizados numa transação. Falhas revertem tudo. Repetições não geram ajustes adicionais.
- Nenhuma nota ou movimentação é apagada. As entradas compensadas ficam canceladas e os ajustes apontam aos movimentos originais. Cancelamentos individuais antigos não são descontados duas vezes.
- Por padrão a tela marca **Reabrir O.C. para recebimento**: encerra a conclusão técnica com evento auditável e recompõe `EMITIDA` ou `PARCIALMENTE_RECEBIDA`, conforme os outros recebimentos confirmados. Desmarcar preserva uma conclusão técnica já existente.
- A API mantém compatibilidade: `POST /api/erp/receipts/<uuid>/reverse`, JSON com `motivo` obrigatório; `reabrir_pedido: true` solicita reabertura técnica. Retorna `replayed`, status do recebimento e da O.C.
- Bloqueia se existir baixa financeira no pedido ou se o estoque físico ficar negativo. Não força saldo, não estorna pagamentos e não altera empenhos/apontamentos de produção.
- A inspeção, o dashboard de compras, as pendências e o trânsito usam as tabelas canônicas recalculadas. Recarregar consultas já abertas. Outros recebimentos, documentos emitidos e solicitações de compra já tratadas conservam seu histórico.
- Não exige migração. Publicar somente o Estoque. Validar saúde, abrir uma prévia real, conferir abrangência e confirmar apenas a nota solicitada. A aplicação da mudança não executa estornos automaticamente.

## Testes

`python -m unittest discover -s tests -p test_receipt_reversal.py`: 102 movimentos/26 SKUs, recebimento por componentes de CJ com multiplicador, parcial com outra NF válida, reabertura opt-in, rollback completo, baixa financeira, motivo obrigatório, idempotência e cancelamento individual prévio.

`python -m unittest discover -s tests -p test_receipt_reversal_routes.py`: permissões, prévia, confirmação, repetição e bloqueios pelas rotas de tela/API em base temporária isolada.

Validação de 16/10/2026: 12 testes novos aprovados, 14 testes de recebimento e 19 de contexto/cancelamento aprovados. A suíte ampla tem uma falha preexistente por data no teste `test_listing_filters_and_notifications` (contagem de atrasos esperada 1, obtida 2); não foram alterados o teste nem o serviço de solicitações de compra. As fixtures de conjuntos dos testes de recebimento/backflush passaram a informar seus grupos 20/30, conforme o contrato produtivo já vigente.
