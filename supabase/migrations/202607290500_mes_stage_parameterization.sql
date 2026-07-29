-- Parametrização explícita das etapas antes da ativação da O.S. no MES.
-- Migração estritamente aditiva: não altera status, saldos ou apontamentos existentes.

begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

alter table if exists public.erp_work_orders
    add column if not exists stage_configuration_status text;

alter table if exists public.erp_work_orders
    add column if not exists stage_configured_at timestamptz;

alter table if exists public.erp_work_orders
    add column if not exists stage_configured_by text;

alter table if exists public.erp_work_order_stages
    add column if not exists parametrizado boolean;

-- Compatibilidade: toda etapa existente antes desta funcionalidade já era tratada
-- pelo MES como operacional. O backfill somente classifica metadados novos.
update public.erp_work_order_stages
set parametrizado = true
where parametrizado is null;

update public.erp_work_orders w
set stage_configuration_status = 'CONCLUIDA',
    stage_configured_at = coalesce(w.ativado_at, w.updated_at, w.created_at),
    stage_configured_by = coalesce(w.ativado_por, w.criado_por, 'MIGRACAO_COMPATIBILIDADE')
where w.stage_configuration_status is null
  and exists (
      select 1
      from public.erp_work_order_stages s
      where s.work_order_id = w.id
  );

update public.erp_work_orders
set stage_configuration_status = 'PENDENTE'
where stage_configuration_status is null;

alter table if exists public.erp_work_orders
    alter column stage_configuration_status set default 'PENDENTE';

alter table if exists public.erp_work_orders
    alter column stage_configuration_status set not null;

alter table if exists public.erp_work_order_stages
    alter column parametrizado set default false;

alter table if exists public.erp_work_order_stages
    alter column parametrizado set not null;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'ck_erp_work_orders_stage_configuration_status'
    ) then
        alter table public.erp_work_orders
            add constraint ck_erp_work_orders_stage_configuration_status
            check (stage_configuration_status in ('PENDENTE', 'CONCLUIDA'));
    end if;
end $$;

create index if not exists ix_erp_work_orders_stage_configuration
    on public.erp_work_orders(stage_configuration_status, status);

comment on column public.erp_work_orders.stage_configuration_status is
    'PENDENTE enquanto houver etapa ?; CONCLUIDA após parametrização explícita no MES.';

comment on column public.erp_work_order_stages.parametrizado is
    'Distingue ? (não parametrizada) de uma etapa canonicamente PENDENTE.';

commit;
