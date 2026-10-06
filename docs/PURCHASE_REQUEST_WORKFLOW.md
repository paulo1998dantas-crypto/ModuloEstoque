# Solicitações de compra — 06/10/2026

## Fluxo operacional

- Estoque: menu **Solicitações de compra**; selecionar SKU ativo, quantidade e data de necessidade. Referência/O.S./setor e observações opcionais.
- Suprimentos: **Solicitações**; perfis PCP/ADMIN possuem o formulário de Planejamento/PCP.
- A tabela consolidada de Compras contém as duas origens, filtros e paginação (100 linhas por página). Os demais usuários autenticados podem consultar e abrir o histórico.
- COMPRADOR/ADMIN pode assumir, registrar observações, cancelar/reabrir e selecionar solicitações para preparar o formulário atual de O.C.
- Estados: SOLICITADA → EM_COMPRAS → CONCLUIDA; CANCELADA pode ser reaberta. Emissão direta também pode concluir SOLICITADA.
- Salvar novo rascunho não conclui. Emitir confirma o pedido integrado; somente então conclui as solicitações.
- “Compra concluída” não significa material recebido. Entrada, inspeção, estoque, empenhos e recebimentos permanecem nos fluxos atuais.
- Uma solicitação é um SKU/quantidade. Um pedido pode atender várias solicitações. A conclusão exige a quantidade integral, somada por SKU; não há atendimento parcial neste incremento.
- Editar uma O.C. vinculada não permite remover o SKU/quantidade das solicitações, mesmo que o cliente omita os vínculos.
- Cancelamento de O.C. reabre as solicitações, sem apagar a conclusão anterior da linha do tempo.

## Rastreabilidade e segurança

UUID interno; solicitante, comprador e horários automáticos em UTC, apresentados em Brasília.
Snapshot do SKU e eventos com antes/depois; confirmação e edição do pedido incluem número, fornecedor e quantidade comprada.
Histórico append-only, protegido por trigger PostgreSQL; não há exclusão de eventos.
Criação idempotente por usuário/token; bloqueio transacional no PostgreSQL e versão otimista nas ações.
Permissão de comprador validada tanto em Suprimentos quanto no banco/backend do Estoque, inclusive quando o RBAC legado é permissivo.
Novas ações via navegador possuem CSRF. APIs internas usam token de backend e identidade autenticada.
Notificações no menu mostram solicitações novas/em tratamento, com atualização a cada minuto enquanto a página está visível.

## Publicação (ainda não executada)

1. Aplicar em transação a migração **ModuloEstoque/supabase/migrations/20261006120000_purchase_requests.sql**. É aditiva, não altera cadastros ou saldos existentes.
2. Publicar Estoque: tabelas/serviço, endpoints internos e integração de conclusão/cancelamento de O.C.
3. Publicar Suprimentos: PCP, tabela consolidada, notificações e preenchimento do pedido.
4. Manter ERP_FEATURE_FLAG habilitada, ERP_STOCK_API_URL/ERP_BACKEND_TOKEN configurados e os usuários com os perfis atuais.
5. Validar login como OPERADOR, PCP e COMPRADOR; consultar telas, sem lançar registros fictícios em produção.
6. Primeiro pedido real: confirmar data/solicitante; preparar O.C.; salvar rascunho (continua pendente); emitir (conclui); conferir UUID/quantidade/histórico.

Se a migração estiver ausente, o workflow retorna mensagem explícita de indisponibilidade. Compras sem solicitações mantêm seu caminho anterior.
Reversão: desativar os novos pontos de entrada/republicar as versões anteriores; preservar as tabelas e a auditoria. Não excluir dados.

## Verificações locais

- 29 testes do Estoque: domínio, transações, idempotência, soma por SKU, edição/cancelamento de pedidos, permissões e endpoints.
- 15 testes de Suprimentos: permissões, CSRF, proxy, preenchimento, rascunho/emissão e preservação dos vínculos.
- 48 testes existentes de documentos em Suprimentos e 7 de consultas/relatórios de compras em Estoque aprovados.
- Navegador: desktop/mobile, tabela, histórico, horário Brasília, seleção para O.C. e formulário PCP.
- PostgreSQL/WASM isolado: migração aplicada/reaplicada, UUID/JSON, constraints, RLS, histórico imutável e lock idempotente. Não equivale a teste de concorrência multi-conexão ou deploy real.
- Três falhas antigas nos testes de recebimento por B.O.M. reproduzidas também em origin/main; não são introduzidas pelo workflow.

