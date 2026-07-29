-- Estados complementares da O.C. sem alterar saldo, movimentos ou o status
-- físico de recebimento. Migration aditiva e idempotente.

begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

alter table if exists public.erp_purchase_orders
    add column if not exists technical_status text not null default 'ABERTA',
    add column if not exists technical_closed_at timestamptz null,
    add column if not exists technical_closed_by text null,
    add column if not exists technical_close_reason text not null default '',
    add column if not exists financial_status text not null default 'PENDENTE',
    add column if not exists financial_closed_at timestamptz null,
    add column if not exists financial_closed_by text null,
    add column if not exists financial_close_reason text not null default '';

do $$
begin
    if not exists (
        select 1
          from pg_constraint
         where conname = 'erp_purchase_orders_technical_status_check'
           and conrelid = 'public.erp_purchase_orders'::regclass
    ) then
        alter table public.erp_purchase_orders
            add constraint erp_purchase_orders_technical_status_check
            check (technical_status in ('ABERTA', 'CONCLUIDA'));
    end if;

    if not exists (
        select 1
          from pg_constraint
         where conname = 'erp_purchase_orders_financial_status_check'
           and conrelid = 'public.erp_purchase_orders'::regclass
    ) then
        alter table public.erp_purchase_orders
            add constraint erp_purchase_orders_financial_status_check
            check (financial_status in ('PENDENTE', 'PARCIALMENTE_CONCLUIDA', 'CONCLUIDA'));
    end if;
end
$$;

create index if not exists erp_purchase_orders_closure_idx
    on public.erp_purchase_orders (technical_status, financial_status, status);

comment on column public.erp_purchase_orders.status is
    'Estado físico da O.C.: emissão, recebimento parcial/total ou cancelamento.';
comment on column public.erp_purchase_orders.technical_status is
    'Conclusão técnica pelo PCP/Compras. Não gera movimento e não altera o estado físico.';
comment on column public.erp_purchase_orders.financial_status is
    'Conclusão financeira, permitida somente após recebimento físico total. Não gera movimento.';

commit;
