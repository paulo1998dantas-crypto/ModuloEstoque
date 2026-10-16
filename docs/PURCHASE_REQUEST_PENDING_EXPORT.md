# Pendências e histórico de solicitações de compra

- A aba **Solicitações** do Excel contém somente `SOLICITADA` e `EM_COMPRAS`, respeitando busca, origem e período escolhidos. Concluídas e canceladas/excluídas não entram nessa aba, mesmo se forem selecionadas pelo filtro da tela.
- A aba **Histórico** é completa e independente dos filtros da fila. Inclui eventos antes/depois, autores, motivos e horários de todas as solicitações, pendentes, concluídas e canceladas/excluídas. Registros legados sem eventos recebem somente um retrato identificado como `ESTADO_ATUAL_SEM_EVENTOS`, sem inventar autoria/data histórica.
- Só usuários ativos com o perfil **PCP explicitamente atribuído** podem criar solicitações, tanto no Estoque quanto no Suprimentos. ADMIN e COMPRADOR sozinhos não são exceções. Contas com PCP mais outros perfis podem criar normalmente.
- A validação é aplicada no serviço canônico do Estoque e no proxy do Suprimentos, não apenas na interface. Consulta, exportação, permissões atuais de edição/exclusão e tratamento de compras continuam preservados. Nenhum perfil é atribuído automaticamente.
- Sem migração de banco. Publicar Estoque e Suprimentos.

Testes em 16/10/2026: 14 de exportação, 75 de workflow/rotas e 24 de integração do Suprimentos aprovados. Permanece a única falha preexistente dependente da data no teste de contagem de atrasos, já documentada em `PURCHASE_ALLOCATION_HISTORY.md`.
