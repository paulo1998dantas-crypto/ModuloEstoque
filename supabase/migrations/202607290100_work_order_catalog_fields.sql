-- Aditiva. Não aplicar em produção antes de backup, staging e autorização.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

alter table if exists public.erp_work_orders
    add column if not exists transformacao_codigo text not null default '';

create index if not exists erp_work_orders_transformacao_codigo_idx
    on public.erp_work_orders(transformacao_codigo)
    where transformacao_codigo <> '';

commit;
