-- Proteção das tabelas ERP compartilhadas.
-- Os frontends não acessam estas tabelas diretamente; usam backends autenticados.
-- Aditiva e idempotente. Não cria políticas permissivas para anon/authenticated.
begin;

do $$
declare
    table_name text;
    table_names constant text[] := array[
        'erp_purchase_orders',
        'erp_purchase_order_lines',
        'erp_goods_receipts',
        'erp_goods_receipt_lines',
        'erp_stock_receipt_links',
        'erp_purchase_order_financial_entries',
        'erp_purchase_order_financial_entry_lines',
        'erp_audit_events',
        'erp_vehicles',
        'erp_vehicle_entries',
        'erp_work_orders',
        'erp_work_order_status_history',
        'erp_work_order_stages',
        'erp_work_order_stage_events',
        'erp_work_order_schedules',
        'erp_legacy_import_records'
    ];
begin
    foreach table_name in array table_names loop
        if to_regclass(format('public.%I', table_name)) is null then
            raise exception 'Tabela obrigatória public.% ainda não existe.', table_name;
        end if;

        execute format('alter table public.%I enable row level security', table_name);

        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format('revoke all on table public.%I from anon', table_name);
        end if;
        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format('revoke all on table public.%I from authenticated', table_name);
        end if;
        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format(
                'grant select,insert,update,delete on table public.%I to service_role',
                table_name
            );
        end if;
    end loop;

    if exists (select 1 from pg_roles where rolname = 'service_role')
       and to_regclass('public.erp_vehicle_entries_item_number_seq') is not null then
        grant usage, select on sequence public.erp_vehicle_entries_item_number_seq
            to service_role;
    end if;
end
$$;

commit;

-- Verificação somente leitura:
-- select schemaname,tablename,rowsecurity
-- from pg_tables
-- where schemaname='public' and tablename like 'erp_%'
-- order by tablename;
