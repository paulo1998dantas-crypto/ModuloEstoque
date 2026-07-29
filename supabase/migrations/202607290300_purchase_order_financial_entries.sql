-- Baixas financeiras parciais da O.C.
-- Não cria, altera ou estorna movimentos de estoque.
-- Migration aditiva e idempotente.

begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

create table if not exists public.erp_purchase_order_financial_entries (
    id uuid primary key default gen_random_uuid(),
    purchase_order_id uuid not null references public.erp_purchase_orders(id),
    entry_number integer not null,
    entry_type text not null check (entry_type in ('PARCIAL', 'COMPLETA')),
    entry_date timestamptz not null,
    numero_nf text not null default '',
    quantidade_lancada numeric(14,3) not null check (quantidade_lancada > 0),
    valor_lancado numeric(14,2) not null check (valor_lancado >= 0),
    closes_balance boolean not null default false,
    actor text not null,
    reason text not null default '',
    idempotency_key text not null unique,
    created_at timestamptz not null default now(),
    unique (purchase_order_id, entry_number)
);

create table if not exists public.erp_purchase_order_financial_entry_lines (
    id uuid primary key default gen_random_uuid(),
    financial_entry_id uuid not null
        references public.erp_purchase_order_financial_entries(id),
    purchase_order_line_id uuid not null
        references public.erp_purchase_order_lines(id),
    quantidade_baixada numeric(14,3) not null
        check (quantidade_baixada > 0),
    valor_baixado numeric(14,2) not null default 0
        check (valor_baixado >= 0),
    created_at timestamptz not null default now(),
    unique (financial_entry_id, purchase_order_line_id)
);

-- A migration de estados anterior já cria esta constraint com todos os estados.
-- Não substitua silenciosamente uma constraint existente em produção: um banco
-- que tenha recebido uma versão antiga deve parar aqui, passar por backup, staging
-- e uma migration de compatibilidade aprovada especificamente para ele.
do $$
declare
    constraint_definition text;
begin
    select pg_get_constraintdef(c.oid, true)
      into constraint_definition
      from pg_constraint c
     where c.conname = 'erp_purchase_orders_financial_status_check'
       and c.conrelid = 'public.erp_purchase_orders'::regclass;

    if constraint_definition is null then
        alter table public.erp_purchase_orders
            add constraint erp_purchase_orders_financial_status_check
            check (financial_status in ('PENDENTE', 'PARCIALMENTE_CONCLUIDA', 'CONCLUIDA'));
    elsif constraint_definition not like '%PARCIALMENTE_CONCLUIDA%' then
        raise exception
            'Constraint financeira legada incompatível. Não foi alterada automaticamente; execute a compatibilização somente após backup e validação em staging.';
    end if;
end
$$;

create index if not exists erp_financial_entries_order_idx
    on public.erp_purchase_order_financial_entries (purchase_order_id, entry_date);

create index if not exists erp_financial_entry_lines_order_line_idx
    on public.erp_purchase_order_financial_entry_lines (purchase_order_line_id);

comment on table public.erp_purchase_order_financial_entries is
    'Histórico de baixas financeiras parciais/completas. Nunca movimenta saldo físico.';
comment on column public.erp_purchase_order_financial_entries.closes_balance is
    'Indica que a baixa encerra o saldo financeiro, inclusive quando o valor real diverge do pedido.';

commit;
