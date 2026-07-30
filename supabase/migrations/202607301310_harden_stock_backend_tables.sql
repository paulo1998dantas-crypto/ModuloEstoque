-- Fecha o acesso direto pela Data API às tabelas operacionais do Estoque.
--
-- Contrato verificado antes desta migration:
--   * ModuloEstoque e MES usam conexão PostgreSQL de backend;
--   * ModuloCadastro e Suprimentos usam SUPABASE_SERVICE_ROLE_KEY no backend;
--   * nenhum frontend autorizado depende dos papeis anon/authenticated.
--
-- A migration não altera, recalcula nem remove qualquer registro.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 60000;

do $$
declare
    table_name text;
    sequence_record record;
    table_names constant text[] := array[
        'users',
        'skus',
        'stock_balances',
        'movements',
        'inventory_counts',
        'inventory_sessions',
        'label_print_jobs',
        'bom_components',
        'dashboard_movement_cache',
        'app_settings'
    ];
begin
    foreach table_name in array table_names loop
        if to_regclass(format('public.%I', table_name)) is null then
            raise exception 'Tabela obrigatoria public.% nao existe.', table_name;
        end if;

        execute format(
            'alter table public.%I enable row level security',
            table_name
        );

        execute format(
            'revoke all privileges on table public.%I from public',
            table_name
        );

        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format(
                'revoke all privileges on table public.%I from anon',
                table_name
            );
        end if;

        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format(
                'revoke all privileges on table public.%I from authenticated',
                table_name
            );
        end if;

        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format(
                'grant select, insert, update, delete '
                'on table public.%I to service_role',
                table_name
            );
        end if;
    end loop;

    -- Descobre as sequencias realmente associadas as tabelas. Isso cobre
    -- SERIAL, IDENTITY e nomes nao convencionais, sem depender de uma lista
    -- manual que poderia deixar uma sequencia exposta.
    for sequence_record in
        with target_tables as (
            select
                requested_name,
                format('public.%I', requested_name)::regclass::oid as table_oid
            from unnest(table_names) as requested_name
        ),
        serial_sequences as (
            select distinct serial_ref.sequence_oid
            from target_tables target
            join pg_class table_class on table_class.oid = target.table_oid
            join pg_namespace table_namespace
              on table_namespace.oid = table_class.relnamespace
            join pg_attribute attribute
              on attribute.attrelid = table_class.oid
             and attribute.attnum > 0
             and not attribute.attisdropped
            cross join lateral (
                select to_regclass(
                    pg_catalog.pg_get_serial_sequence(
                        format(
                            '%I.%I',
                            table_namespace.nspname,
                            table_class.relname
                        ),
                        attribute.attname
                    )
                )::oid as sequence_oid
            ) serial_ref
            where serial_ref.sequence_oid is not null
        ),
        dependency_sequences as (
            select distinct sequence_class.oid as sequence_oid
            from target_tables target
            join pg_depend dependency
              on dependency.refclassid = 'pg_class'::regclass
             and dependency.refobjid = target.table_oid
             and dependency.refobjsubid > 0
             and dependency.classid = 'pg_class'::regclass
             and dependency.deptype in ('a', 'i')
            join pg_class sequence_class
              on sequence_class.oid = dependency.objid
             and sequence_class.relkind = 'S'
        ),
        discovered_sequences as (
            select sequence_oid from serial_sequences
            union
            select sequence_oid from dependency_sequences
        )
        select
            sequence_namespace.nspname as schema_name,
            sequence_class.relname as sequence_name
        from discovered_sequences discovered
        join pg_class sequence_class
          on sequence_class.oid = discovered.sequence_oid
         and sequence_class.relkind = 'S'
        join pg_namespace sequence_namespace
          on sequence_namespace.oid = sequence_class.relnamespace
        order by sequence_namespace.nspname, sequence_class.relname
    loop
        execute format(
            'revoke all privileges on sequence %I.%I from public',
            sequence_record.schema_name,
            sequence_record.sequence_name
        );
        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format(
                'revoke all privileges on sequence %I.%I from anon',
                sequence_record.schema_name,
                sequence_record.sequence_name
            );
        end if;
        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format(
                'revoke all privileges on sequence %I.%I from authenticated',
                sequence_record.schema_name,
                sequence_record.sequence_name
            );
        end if;
        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format(
                'grant usage, select on sequence %I.%I to service_role',
                sequence_record.schema_name,
                sequence_record.sequence_name
            );
        end if;
    end loop;
end
$$;

comment on table public.users is
    'Tabela de identidade compartilhada, acessível somente pelos backends autorizados.';
comment on table public.movements is
    'Livro operacional de estoque; escrita somente pelos serviços de domínio autorizados.';

commit;
