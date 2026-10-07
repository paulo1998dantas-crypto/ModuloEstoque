# Solicitações de compra — 06/10/2026

## Fluxo operacional

- Estoque: menu **Solicitações de compra**; selecionar SKU ativo, quantidade, data de necessidade e setor (PRODUÇÃO, ADMINISTRATIVO ou GERAL). Vincular uma ou mais O.S. abertas pela lista pesquisável; observações opcionais.
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
O mesmo SKU não pode ter outra solicitação SOLICITADA/EM_COMPRAS para nenhuma das O.S. selecionadas, independentemente da origem ou usuário. Sem O.S., a referência exclusiva é o setor; O.S. diferentes não conflitam apenas por pertencerem ao mesmo setor. CONCLUIDA/CANCELADA não bloqueiam novas necessidades.
Criação, edição e reabertura validam a duplicidade antes da confirmação e da auditoria. Locks transacionais por SKU serializam gravações concorrentes. Reenvio do mesmo token retorna a solicitação existente. Se cancelar uma O.C. reabriria uma solicitação duplicada, toda a operação é rejeitada, preservando o pedido e seus vínculos.
Referências históricas são vinculadas apenas quando O.S./ITEM correspondem de forma exata e única. Texto anterior é preservado, ambiguidades são sinalizadas para revisão; não há fusão/exclusão automática de solicitações antigas. Reaplicar a migração não sobrescreve vínculos/setores revisados.
Permissão de comprador validada tanto em Suprimentos quanto no banco/backend do Estoque, inclusive quando o RBAC legado é permissivo.
Novas ações via navegador possuem CSRF. APIs internas usam token de backend e identidade autenticada.
Notificações no menu mostram solicitações novas/em tratamento, com atualização a cada minuto enquanto a página está visível.

## Publicação (ordem de execução)

1. Aplicar em transação a migração **ModuloEstoque/supabase/migrations/20261006120000_purchase_requests.sql**. É aditiva, não altera cadastros ou saldos existentes.
   Para o incremento de referências, aplicar também **20261007120000_purchase_request_work_order_references.sql** antes de publicar os novos commits. A migração cria vínculos UUID e normaliza referências históricas, sem movimentar estoque.
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
- Referências: regressão de duplicidade na criação/edição/reabertura, várias O.S., origens compartilhadas, cancelamento atômico de O.C. e idempotência; teste isolado `tests/postgres/purchase_request_references.cjs` no Estoque cobre a normalização histórica e reaplicação da migração. Requer `@electric-sql/pglite` apenas no ambiente de testes.
- Três falhas antigas nos testes de recebimento por B.O.M. reproduzidas também em origin/main; não são introduzidas pelo workflow.

## Incremento de referências — 07/10/2026

- Migração de referências aplicada ao Supabase operacional: 59 solicitações preservadas, 31 solicitações com 107 vínculos de O.S.; 28 sem referência de O.S. foram mantidas como solicitações por setor. Todas as referências originais foram preservadas e o RLS permanece habilitado.
- Para a O.S. 2922, as referências pendentes foram vinculadas à única ordem aberta, sem reaproveitar a ordem cancelada de mesmo número. Quando houver mais de uma candidata aberta, o sistema mantém a revisão pendente.
- Validação direcionada: 67 testes do Estoque e 18 de Suprimentos, além de 21 casos de referências em PostgreSQL/WASM. Nenhum registro de teste foi criado em produção.
- Clientes antigos sem os campos estruturados recebem orientação para recarregar a tela; texto livre não pode contornar a validação de novas referências. O texto histórico continua preservado. As 10 falhas fora deste escopo na suíte geral foram reproduzidas em checkouts isolados dos commits anteriores.
- O bloqueio de duplicidade entra em vigor com a publicação do backend do Estoque; Suprimentos usa a mesma validação canônica, sem fila independente.

