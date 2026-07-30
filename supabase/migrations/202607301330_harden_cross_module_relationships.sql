-- Endurece os vinculos que cruzam Estoque, Suprimentos e MES.
--
-- Pre-condicao de corte: as quatro consultas de orfaos devem retornar zero.
-- A migration e aditiva: nao altera nem recalcula saldos, movimentos ou
-- documentos existentes.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 60000;

select pg_advisory_xact_lock(982734621);

do $$
begin
    if to_regclass('public.erp_purchase_order_lines') is null
       or to_regclass('public.erp_goods_receipt_lines') is null
       or to_regclass('public.erp_stock_receipt_links') is null
       or to_regclass('public.erp_work_orders') is null
       or to_regclass('public.skus') is null
       or to_regclass('public.movements') is null then
        raise exception
            'Schema ERP incompleto para endurecer os relacionamentos.';
    end if;

    if exists (
        select 1
        from public.erp_purchase_order_lines line
        where line.work_order_id is not null
          and not exists (
              select 1
              from public.erp_work_orders work_order
              where work_order.id = line.work_order_id
          )
    ) then
        raise exception
            'Existem linhas de O.C. com work_order_id orfao.';
    end if;

    if exists (
        select 1
        from public.erp_purchase_order_lines line
        where line.sku_id is not null
          and not exists (
              select 1 from public.skus sku where sku.id = line.sku_id
          )
    ) then
        raise exception
            'Existem linhas de O.C. com sku_id orfao.';
    end if;

    if exists (
        select 1
        from public.erp_goods_receipt_lines line
        where line.sku_id is not null
          and not exists (
              select 1 from public.skus sku where sku.id = line.sku_id
          )
    ) then
        raise exception
            'Existem linhas de recebimento com sku_id orfao.';
    end if;

    if exists (
        select 1
        from public.erp_stock_receipt_links link
        where link.movement_id is not null
          and not exists (
              select 1
              from public.movements movement
              where movement.id = link.movement_id
          )
    ) then
        raise exception
            'Existem links de recebimento com movement_id orfao.';
    end if;
end
$$;

create index if not exists erp_po_lines_work_order_id_idx
    on public.erp_purchase_order_lines(work_order_id)
    where work_order_id is not null;
create index if not exists erp_po_lines_sku_id_idx
    on public.erp_purchase_order_lines(sku_id)
    where sku_id is not null;
create index if not exists erp_goods_receipt_lines_sku_id_idx
    on public.erp_goods_receipt_lines(sku_id)
    where sku_id is not null;
create index if not exists erp_stock_receipt_links_movement_id_idx
    on public.erp_stock_receipt_links(movement_id)
    where movement_id is not null;

-- Indices para FKs introduzidas pela migration 1300.
create index if not exists erp_movement_reference_history_previous_work_order_idx
    on public.erp_movement_reference_history(previous_work_order_id)
    where previous_work_order_id is not null;
create index if not exists erp_movement_reference_history_new_work_order_idx
    on public.erp_movement_reference_history(new_work_order_id)
    where new_work_order_id is not null;
create index if not exists erp_user_permission_overrides_permission_idx
    on public.erp_user_permission_overrides(permission_code);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.erp_purchase_order_lines'::regclass
          and conname = 'erp_purchase_order_lines_work_order_id_fk'
    ) then
        alter table public.erp_purchase_order_lines
            add constraint erp_purchase_order_lines_work_order_id_fk
            foreign key (work_order_id)
            references public.erp_work_orders(id)
            on update cascade
            on delete restrict
            not valid;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.erp_purchase_order_lines'::regclass
          and conname = 'erp_purchase_order_lines_sku_id_fk'
    ) then
        alter table public.erp_purchase_order_lines
            add constraint erp_purchase_order_lines_sku_id_fk
            foreign key (sku_id)
            references public.skus(id)
            on update cascade
            on delete restrict
            not valid;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.erp_goods_receipt_lines'::regclass
          and conname = 'erp_goods_receipt_lines_sku_id_fk'
    ) then
        alter table public.erp_goods_receipt_lines
            add constraint erp_goods_receipt_lines_sku_id_fk
            foreign key (sku_id)
            references public.skus(id)
            on update cascade
            on delete restrict
            not valid;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.erp_stock_receipt_links'::regclass
          and conname = 'erp_stock_receipt_links_movement_id_fk'
    ) then
        alter table public.erp_stock_receipt_links
            add constraint erp_stock_receipt_links_movement_id_fk
            foreign key (movement_id)
            references public.movements(id)
            on update cascade
            on delete restrict
            not valid;
    end if;
end
$$;

alter table public.erp_purchase_order_lines
    validate constraint erp_purchase_order_lines_work_order_id_fk;
alter table public.erp_purchase_order_lines
    validate constraint erp_purchase_order_lines_sku_id_fk;
alter table public.erp_goods_receipt_lines
    validate constraint erp_goods_receipt_lines_sku_id_fk;
alter table public.erp_stock_receipt_links
    validate constraint erp_stock_receipt_links_movement_id_fk;

-- O Supabase pode conceder EXECUTE explicitamente a anon/authenticated.
-- Funcoes de trigger nao sao APIs; apenas os backends devem enxerga-las.
do $$
declare
    function_name text;
    function_names constant text[] := array[
        'erp_users_auth_version_bump',
        'erp_membership_auth_version_bump',
        'erp_guard_last_admin_membership',
        'erp_guard_last_admin_user',
        'erp_guard_admin_role_active'
    ];
begin
    foreach function_name in array function_names loop
        execute format(
            'revoke all on function public.%I() from public',
            function_name
        );
        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format(
                'revoke all on function public.%I() from anon',
                function_name
            );
        end if;
        if exists (
            select 1 from pg_roles where rolname = 'authenticated'
        ) then
            execute format(
                'revoke all on function public.%I() from authenticated',
                function_name
            );
        end if;
        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format(
                'grant execute on function public.%I() to service_role',
                function_name
            );
        end if;
    end loop;
end
$$;

commit;
