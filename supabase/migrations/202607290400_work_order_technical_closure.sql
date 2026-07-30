-- Conclusão técnica da O.S. registrada por Suprimentos.
-- Não altera o status produtivo, não remove cards e não movimenta estoque.
-- Migration aditiva e idempotente.

begin;
set local lock_timeout = 5000;
set local statement_timeout = 60000;

alter table if exists public.erp_work_orders
    add column if not exists technical_status text not null default 'ABERTA',
    add column if not exists technical_closed_at timestamptz null,
    add column if not exists technical_closed_by text null,
    add column if not exists technical_close_reason text not null default '';

do $$
begin
    if not exists (
        select 1
          from pg_constraint
         where conname = 'erp_work_orders_technical_status_check'
           and conrelid = 'public.erp_work_orders'::regclass
    ) then
        alter table public.erp_work_orders
            add constraint erp_work_orders_technical_status_check
            check (technical_status in ('ABERTA', 'CONCLUIDA'));
    end if;
end
$$;

create index if not exists erp_work_orders_technical_status_idx
    on public.erp_work_orders (technical_status, status);

comment on column public.erp_work_orders.technical_status is
    'Conclusão técnica registrada em Suprimentos. Não altera o fluxo produtivo do MES.';
comment on column public.erp_work_orders.technical_closed_at is
    'Data/hora da conclusão técnica usada para auditoria e coluna ARQUIVADO do relatório.';

commit;
