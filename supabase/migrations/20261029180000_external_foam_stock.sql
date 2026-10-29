-- No internal stock is created. Activate before adding foam edges to bank BOMs.
begin;
set local lock_timeout = '5s';

create table if not exists public.erp_external_stock_items (
    sku text primary key,
    location text not null default 'SPANDY',
    activated_at timestamptz not null default now()
);
create table if not exists public.erp_external_stock_movements (
    id text primary key,
    sku text not null references public.erp_external_stock_items(sku),
    kind text not null check(kind in ('INITIAL','ENTRY','MANUAL','ADJUSTMENT','RECEIPT','REVERSAL')),
    delta numeric(20,6) not null,
    receipt_id text,
    receipt_line_id text,
    bank_sku text,
    bank_quantity numeric(20,6),
    bom_snapshot jsonb not null default '{}',
    reason text not null check(length(trim(reason)) > 0),
    actor text not null,
    user_id integer not null references public.users(id),
    request_key text not null unique,
    reverses_id text unique references public.erp_external_stock_movements(id),
    created_at timestamptz not null default now(),
    check ((kind = 'RECEIPT' and delta < 0 and receipt_id is not null and receipt_line_id is not null)
       or (kind <> 'RECEIPT')),
    check (kind <> 'REVERSAL' or reverses_id is not null)
);
create index if not exists external_movements_sku_date
    on public.erp_external_stock_movements(sku,created_at desc,id);
create index if not exists external_movements_receipt
    on public.erp_external_stock_movements(receipt_id) where receipt_id is not null;
create unique index if not exists external_movements_initial
    on public.erp_external_stock_movements(sku) where kind='INITIAL';
create unique index if not exists external_movements_receipt_line
    on public.erp_external_stock_movements(receipt_line_id,sku) where kind='RECEIPT';

insert into public.erp_external_stock_items(sku)
values ('10220272'),('10220273'),('10220274'),('10220275'),('10220276')
on conflict(sku) do nothing;

alter table public.erp_external_stock_items enable row level security;
alter table public.erp_external_stock_movements enable row level security;
revoke all on public.erp_external_stock_items,public.erp_external_stock_movements
    from public,anon,authenticated;
grant select,insert on public.erp_external_stock_items,public.erp_external_stock_movements to service_role;
-- Immutable ledger: corrections are new compensating movements, never edits.
comment on table public.erp_external_stock_movements is
  'Estoque externo independente; baixas de recebimentos são atômicas, idempotentes e estornáveis. Não equivale a movements/stock_balances.';
commit;
