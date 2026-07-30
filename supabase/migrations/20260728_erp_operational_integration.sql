-- ERP operational integration. Additive only; do not run against production
-- before backup, staging validation and explicit authorization.
begin;
set local lock_timeout = 5000;
set local statement_timeout = 60000;

create extension if not exists pgcrypto;

create table if not exists public.erp_purchase_orders (
    id uuid primary key default gen_random_uuid(),
    numero_oc text not null,
    categoria text not null default 'GERAL',
    fornecedor_id text null,
    fornecedor_nome text not null default '',
    data_criacao timestamptz not null default now(),
    data_emissao timestamptz null,
    criado_por text not null default '',
    status text not null default 'RASCUNHO' check (status in ('RASCUNHO','EMITIDA','PARCIALMENTE_RECEBIDA','RECEBIDA','CANCELADA','ENCERRADA_COM_SALDO')),
    destino text not null default '', frete numeric(14,2) not null default 0,
    data_necessidade date null, observacoes text not null default '',
    valor_total_pedido numeric(14,2) not null default 0, version integer not null default 1,
    idempotency_key text null unique, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
    unique (numero_oc, categoria, id)
);

create table if not exists public.erp_purchase_order_lines (
    id uuid primary key default gen_random_uuid(), purchase_order_id uuid not null references public.erp_purchase_orders(id),
    numero_linha integer not null, sku_id integer null, sku_codigo text null, descricao_original text not null,
    unidade text not null default 'UN', quantidade_pedida numeric(14,3) not null check (quantidade_pedida > 0),
    quantidade_recebida numeric(14,3) not null default 0, valor_unitario_pedido numeric(14,4) not null default 0,
    destino text not null default '', cliente_id text null, work_order_id uuid null,
    data_necessidade date null, status text not null default 'PENDENTE' check (status in ('PENDENTE','PARCIALMENTE_RECEBIDA','RECEBIDA','CANCELADA')),
    unique(purchase_order_id, numero_linha)
);

create table if not exists public.erp_goods_receipts (
    id uuid primary key default gen_random_uuid(), purchase_order_id uuid null references public.erp_purchase_orders(id),
    origem text not null check (origem in ('PURCHASE_ORDER','MANUAL')), data_recebimento timestamptz not null,
    fornecedor_nome text not null default '', numero_nf text not null default '', operador text not null,
    status text not null default 'CONFIRMADO' check (status in ('RASCUNHO','CONFIRMADO','ESTORNADO','CANCELADO')),
    observacoes text not null default '', motivo_excecao text not null default '', idempotency_key text not null unique,
    confirmed_at timestamptz not null default now(), reversed_at timestamptz null, created_at timestamptz not null default now()
);

create table if not exists public.erp_goods_receipt_lines (
    id uuid primary key default gen_random_uuid(), goods_receipt_id uuid not null references public.erp_goods_receipts(id),
    purchase_order_line_id uuid null references public.erp_purchase_order_lines(id), sku_id integer null, sku_codigo text null,
    quantidade_esperada numeric(14,3) not null default 0, quantidade_recebida_anterior numeric(14,3) not null default 0,
    saldo_pendente numeric(14,3) not null default 0, quantidade_fisica numeric(14,3) not null default 0,
    quantidade_aprovada numeric(14,3) not null default 0, quantidade_condicional numeric(14,3) not null default 0,
    quantidade_rejeitada numeric(14,3) not null default 0, valor_unitario_pedido numeric(14,4) not null default 0,
    valor_unitario_real numeric(14,4) not null default 0, lote text null, serie text null, validade date null,
    localizacao text null, certificado_exigido boolean not null default false, certificado_apresentado boolean not null default false,
    validade_certificado date null, resultado_inspecao text not null check (resultado_inspecao in ('A','AC','D')),
    justificativa_divergencia text not null default '', unique(goods_receipt_id, purchase_order_line_id, sku_codigo)
);

create table if not exists public.erp_stock_receipt_links (
    id uuid primary key default gen_random_uuid(), goods_receipt_line_id uuid not null references public.erp_goods_receipt_lines(id),
    movement_id integer null, quantidade_disponivel numeric(14,3) not null default 0,
    quantidade_quarentena numeric(14,3) not null default 0, idempotency_key text not null unique, created_at timestamptz not null default now()
);

create table if not exists public.erp_audit_events (
    id uuid primary key default gen_random_uuid(), entity_type text not null, entity_id uuid null, action text not null,
    actor text not null default '', origin text not null default 'ERP', before_data jsonb not null default '{}'::jsonb,
    after_data jsonb not null default '{}'::jsonb, reason text not null default '', created_at timestamptz not null default now()
);

create table if not exists public.erp_vehicles (
    id uuid primary key default gen_random_uuid(), chassi text not null unique, marca text not null default '', modelo text not null default '', versao text not null default '', mmv text not null default '', created_at timestamptz not null default now()
);
create table if not exists public.erp_vehicle_entries (
    id uuid primary key default gen_random_uuid(), item_number bigint generated always as identity unique, vehicle_id uuid not null references public.erp_vehicles(id),
    data_chegada timestamptz not null, cliente_id text null, cliente_nome text not null default '', origem text not null default 'MANUAL',
    observacoes text not null default '', avarias text not null default '', criado_por text not null, status text not null default 'AGUARDANDO_O_S', created_at timestamptz not null default now()
);
create table if not exists public.erp_work_orders (
    id uuid primary key default gen_random_uuid(), vehicle_entry_id uuid not null unique references public.erp_vehicle_entries(id), numero_os text not null unique,
    tipo_servico text not null default 'TRANSFORMAÇÃO' check (tipo_servico in ('TRANSFORMAÇÃO','PÓS-VENDA','INSTALAÇÃO_DE_ACESSÓRIO','RETORNO','OUTRO')),
    proposta_numero text not null default '', data_aprovacao date null, vendedor text not null default '', mercado text not null default '', cliente_nome text not null default '', municipio text not null default '', uf text not null default '', tipo_veiculo text not null default '', linha text not null default '', transformacao text not null default '', codigo_banco text not null default '', conjunto_bancos text not null default '', acessibilidade text not null default '', lotacao text not null default '', ar_condicionado text not null default '', tipo_sistema_ar text not null default '', ar_quente text not null default '', acessorio text not null default '', plotagem text not null default '', data_comercial_prevista date null, termino_producao timestamptz null, data_entrega timestamptz null, status text not null default 'RASCUNHO' check (status in ('RASCUNHO','AGUARDANDO_O_S','ATIVA','EM_PRODUÇÃO','FINALIZADA','ENTREGUE','RETIRADA','CANCELADA','ARQUIVADA')), criado_por text not null, ativado_por text null, ativado_at timestamptz null, finalizado_por text null, finalizado_at timestamptz null, version integer not null default 1, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table if not exists public.erp_work_order_status_history (
    id uuid primary key default gen_random_uuid(), work_order_id uuid not null references public.erp_work_orders(id), status_anterior text null, novo_status text not null, usuario text not null, motivo text not null default '', observacao text not null default '', created_at timestamptz not null default now()
);
create table if not exists public.erp_work_order_stages (
    id uuid primary key default gen_random_uuid(), work_order_id uuid not null references public.erp_work_orders(id), stage_code text not null,
    aplicavel boolean not null default true, status text not null default 'PENDENTE' check (status in ('NÃO_APLICÁVEL','PENDENTE','LIBERADA','EM_ANDAMENTO','CONCLUÍDA')),
    ordem integer not null, semana_planejada text null, data_planejada date null, responsavel text null, localizacao text null, inicio timestamptz null, termino timestamptz null, observacoes text not null default '', bloqueio_motivo text not null default '', unique(work_order_id, stage_code)
);
create table if not exists public.erp_work_order_stage_events (
    id uuid primary key default gen_random_uuid(), work_order_stage_id uuid not null references public.erp_work_order_stages(id), action text not null, status_anterior text null, novo_status text not null, operador text not null, inicio timestamptz null, termino timestamptz null, localizacao text null, observacao text not null default '', idempotency_key text null unique, created_at timestamptz not null default now()
);
create table if not exists public.erp_work_order_schedules (
    id uuid primary key default gen_random_uuid(), work_order_id uuid not null references public.erp_work_orders(id), data_anterior date null, nova_data date not null, motivo text not null default '', usuario text not null, vigente boolean not null default true, created_at timestamptz not null default now()
);
create table if not exists public.erp_legacy_import_records (
    id uuid primary key default gen_random_uuid(), source_key text not null unique, source_file text not null, source_sheet text not null,
    source_item text not null default '', entity_type text not null, entity_id uuid null, payload jsonb not null default '{}'::jsonb,
    imported_at timestamptz not null default now()
);

alter table if exists public.movements add column if not exists source_type text null;
alter table if exists public.movements add column if not exists source_id uuid null;
alter table if exists public.movements add column if not exists source_line_id uuid null;
alter table if exists public.movements add column if not exists idempotency_key text null;
do $$ begin
    if to_regclass('public.movements') is not null then
        execute 'create unique index if not exists movements_idempotency_key_unique on public.movements(idempotency_key) where idempotency_key is not null';
    end if;
end $$;

create index if not exists erp_po_pending_idx on public.erp_purchase_orders(status, data_necessidade);
create index if not exists erp_po_lines_order_idx on public.erp_purchase_order_lines(purchase_order_id, status);
create index if not exists erp_stages_active_idx on public.erp_work_order_stages(status, stage_code);

commit;
