-- Ordem de Produção: conversão controlada de SKU de composição para SKU final.
-- Aditiva. Não recalcula saldo, não altera movimentos existentes e não remove dados.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 60000;

select pg_advisory_xact_lock(982734622);

do $$
begin
    if to_regclass('public.skus') is null
       or to_regclass('public.users') is null
       or to_regclass('public.movements') is null then
        raise exception 'Tabelas de Estoque obrigatórias não encontradas para O.P.';
    end if;
end
$$;

create table if not exists public.erp_production_orders (
    id uuid primary key default gen_random_uuid(),
    numero_op text not null unique,
    status text not null default 'RASCUNHO',
    setor text not null default 'SERRALHERIA',
    target_sku_id integer not null references public.skus(id) on update cascade on delete restrict,
    quantidade_planejada numeric(14,3) not null,
    quantidade_produzida numeric(14,3) not null default 0,
    unidade text null,
    producao_tipo text not null default 'ESTOQUE',
    destino_descricao text null,
    chassi_lote text null,
    cliente_nome text null,
    municipio text null,
    mmv text null,
    observacoes text null,
    target_snapshot jsonb not null default '{}'::jsonb,
    selected_parameters jsonb not null default '[]'::jsonb,
    process_snapshot jsonb not null default '[]'::jsonb,
    idempotency_key text not null unique,
    created_by integer not null references public.users(id) on delete restrict,
    released_by integer null references public.users(id) on delete set null,
    completed_by integer null references public.users(id) on delete set null,
    completed_operation_id uuid null unique,
    completed_at timestamptz null,
    cancelled_at timestamptz null,
    cancelled_by integer null references public.users(id) on delete set null,
    cancel_reason text null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint erp_production_orders_status_ck check (status in ('RASCUNHO','LIBERADA','EM_SERRALHERIA','EMPENHADA','PARCIAL','CONCLUIDA','CANCELADA')),
    constraint erp_production_orders_quantity_ck check (quantidade_planejada > 0 and quantidade_produzida >= 0),
    constraint erp_production_orders_type_ck check (producao_tipo in ('ESTOQUE','DESTINADA'))
);

create table if not exists public.erp_production_order_inputs (
    id uuid primary key default gen_random_uuid(),
    production_order_id uuid not null references public.erp_production_orders(id) on delete restrict,
    numero_linha integer not null,
    source_sku_id integer not null references public.skus(id) on update cascade on delete restrict,
    quantidade_planejada numeric(14,3) not null,
    quantidade_empenhada numeric(14,3) not null default 0,
    quantidade_baixada numeric(14,3) not null default 0,
    source_snapshot jsonb not null default '{}'::jsonb,
    commitment_movement_id integer null unique references public.movements(id) on delete restrict,
    consumption_movement_id integer null unique references public.movements(id) on delete restrict,
    created_at timestamptz not null default now(),
    constraint erp_production_order_inputs_line_uq unique (production_order_id, numero_linha),
    constraint erp_production_order_inputs_quantity_ck check (quantidade_planejada > 0 and quantidade_empenhada >= 0 and quantidade_baixada >= 0)
);

create table if not exists public.erp_production_order_events (
    id uuid primary key default gen_random_uuid(),
    production_order_id uuid not null references public.erp_production_orders(id) on delete restrict,
    action text not null,
    actor_user_id integer null references public.users(id) on delete set null,
    details jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists erp_production_orders_status_created_idx
    on public.erp_production_orders(status, created_at desc);
create index if not exists erp_production_orders_target_sku_idx
    on public.erp_production_orders(target_sku_id);
create index if not exists erp_production_order_inputs_source_sku_idx
    on public.erp_production_order_inputs(source_sku_id);
create index if not exists erp_production_order_events_order_created_idx
    on public.erp_production_order_events(production_order_id, created_at);

-- Não expor operações de estoque via Data API: os backends fazem a operação
-- atômica no Estoque e usam a credencial de serviço apenas no servidor.
alter table public.erp_production_orders enable row level security;
alter table public.erp_production_order_inputs enable row level security;
alter table public.erp_production_order_events enable row level security;
revoke all on public.erp_production_orders, public.erp_production_order_inputs, public.erp_production_order_events from public;
revoke all on public.erp_production_orders, public.erp_production_order_inputs, public.erp_production_order_events from anon, authenticated;
grant select, insert, update, delete on public.erp_production_orders, public.erp_production_order_inputs, public.erp_production_order_events to service_role;

insert into public.erp_permissions (code, module, description)
values
    ('suprimentos.production_order.view', 'SUPRIMENTOS', 'Consultar Ordens de Produção.'),
    ('suprimentos.production_order.manage', 'SUPRIMENTOS', 'Criar e editar Ordens de Produção.'),
    ('suprimentos.production_order.execute', 'SUPRIMENTOS', 'Empenhar, concluir e cancelar Ordens de Produção.')
on conflict (code) do update set module=excluded.module, description=excluded.description;

insert into public.erp_role_permissions (role_code, permission_code)
select role_code, permission_code
from (values
    ('OPERADOR','suprimentos.production_order.view'),
    ('OPERADOR','suprimentos.production_order.execute'),
    ('COMPRADOR','suprimentos.production_order.view'),
    ('FINANCEIRO','suprimentos.production_order.view'),
    ('PCP','suprimentos.production_order.view'),
    ('PCP','suprimentos.production_order.manage'),
    ('PCP','suprimentos.production_order.execute'),
    ('ENGENHARIA','suprimentos.production_order.view'),
    ('ENGENHARIA','suprimentos.production_order.manage'),
    ('ENGENHARIA','suprimentos.production_order.execute'),
    ('ADMIN','suprimentos.production_order.view'),
    ('ADMIN','suprimentos.production_order.manage'),
    ('ADMIN','suprimentos.production_order.execute')
) as grants(role_code, permission_code)
where exists (select 1 from public.erp_roles r where r.code=grants.role_code)
on conflict do nothing;

commit;
