# Situação e marcos da solicitação de compra

- Nova compra: destaque azul; antecipação: destaque âmbar. O status operacional permanece separado.
- Concluído permanece em verde, com a classificação da tratativa abaixo.
- Solicitada em: `created_at` da solicitação.
- Assumida em / por: primeiro evento `ASSUMIR` ou `SOLICITAR_ANTECIPACAO` do ciclo atual, obtido da auditoria existente. Observações, edições e conclusão não substituem essa data.
- Concluída em / por: `completed_at` e `completed_by` canônicos. Não significa recebimento do material.
- Reabertura inicia um novo ciclo; os marcos antigos continuam no Histórico.
- Uma conclusão direta, sem ação Assumir, não recebe uma data fictícia de assunção.
- Tabela, detalhes, API e Excel usam os mesmos marcos. Horários exibidos em Brasília; datas do Excel continuam valores datados, não texto.
- Não requer migração nem atualização de solicitações reais. A recuperação histórica consulta eventos em lotes, sem uma consulta por linha.

Publicação: backend do Estoque e interface do Suprimentos (inclui interface do Estoque). O Suprimentos mantém o proxy da API canônica; não grava uma segunda cópia dos marcos.
