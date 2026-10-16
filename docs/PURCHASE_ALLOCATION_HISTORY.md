# Edição de compra com O.S. histórica

Uma O.C. já vinculada a uma O.S. pode ter suas linhas comerciais corrigidas após a entrega/conclusão dessa O.S. Ao manter exatamente o mesmo vínculo persistido, a validação conserva os UUIDs da O.S. e da entrada do veículo, sem reabrir produção nem alterar apontamentos.

Pedidos novos e trocas reais de destino continuam exigindo O.S. vigente, tecnicamente aberta e ativa/em produção. O bloqueio de alteração das linhas de pedidos com recebimentos confirmados permanece em vigor. Não exige migração; publicar somente o Estoque, que valida a integração do Suprimentos.

Teste: `python -m unittest discover -s tests -p test_purchase_allocation_history.py -v`.

Validado em 16/10/2026: sete testes da correção e 14 testes de resolução de SKU/recebimento aprovados. Da suíte de solicitações, 70/71 aprovados; permanece a falha preexistente dependente da data em `test_listing_filters_and_notifications` (atrasos esperados 1, obtidos 2), já registrada antes desta alteração.
