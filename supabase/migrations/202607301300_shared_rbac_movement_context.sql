-- RBAC compartilhado e contexto operacional de movimentacoes.
-- Aditiva, idempotente e sem backfill inferido de movimentacoes legadas.
-- As feature flags das aplicacoes devem permanecer desligadas ate a validacao.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 60000;

create extension if not exists pgcrypto;

do $$
begin
    if to_regclass('public.users') is null then
        raise exception 'Tabela obrigatoria public.users nao existe.';
    end if;
    if to_regclass('public.movements') is null then
        raise exception 'Tabela obrigatoria public.movements nao existe.';
    end if;
    if to_regclass('public.erp_work_orders') is null then
        raise exception 'Tabela obrigatoria public.erp_work_orders nao existe.';
    end if;
end
$$;

alter table public.users
    add column if not exists auth_version integer not null default 1;

create table if not exists public.erp_roles (
    code text primary key,
    name text not null,
    description text not null default '',
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint erp_roles_code_format_ck
        check (code = upper(code) and code ~ '^[A-Z][A-Z0-9_]*$')
);

create table if not exists public.erp_permissions (
    code text primary key,
    module text not null,
    description text not null default '',
    created_at timestamptz not null default now(),
    constraint erp_permissions_code_format_ck
        check (code ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')
);

create table if not exists public.erp_role_permissions (
    role_code text not null
        references public.erp_roles(code) on update cascade on delete cascade,
    permission_code text not null
        references public.erp_permissions(code) on update cascade on delete cascade,
    created_at timestamptz not null default now(),
    primary key (role_code, permission_code)
);

create table if not exists public.erp_user_roles (
    user_id integer not null
        references public.users(id) on update cascade on delete cascade,
    role_code text not null
        references public.erp_roles(code) on update cascade on delete restrict,
    assigned_by integer null references public.users(id) on delete set null,
    assigned_at timestamptz not null default now(),
    primary key (user_id, role_code)
);

create table if not exists public.erp_user_permission_overrides (
    user_id integer not null
        references public.users(id) on update cascade on delete cascade,
    permission_code text not null
        references public.erp_permissions(code) on update cascade on delete cascade,
    allowed boolean not null,
    reason text not null default '',
    assigned_by integer null references public.users(id) on delete set null,
    assigned_at timestamptz not null default now(),
    primary key (user_id, permission_code)
);

create table if not exists public.erp_app_sessions (
    id uuid primary key default gen_random_uuid(),
    token_hash text not null unique,
    user_id integer not null references public.users(id) on delete cascade,
    app_code text not null,
    auth_version integer not null,
    expires_at timestamptz not null,
    revoked_at timestamptz null,
    created_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    constraint erp_app_sessions_app_code_ck
        check (app_code in ('ESTOQUE','SUPRIMENTOS','MES','CADASTRO'))
);

create table if not exists public.erp_auth_audit_events (
    id uuid primary key default gen_random_uuid(),
    user_id integer null references public.users(id) on delete set null,
    actor_user_id integer null references public.users(id) on delete set null,
    action text not null,
    before_data jsonb not null default '{}'::jsonb,
    after_data jsonb not null default '{}'::jsonb,
    reason text not null default '',
    origin_app text not null default 'ESTOQUE',
    created_at timestamptz not null default now()
);

-- SQLAlchemy Base.metadata.create_all() pode ter criado parte destas tabelas
-- antes da migration. CREATE TABLE IF NOT EXISTS nao reconcilia colunas,
-- defaults ou constraints de uma tabela preexistente, por isso o contrato
-- abaixo e aplicado explicitamente e sem sobrescrever valores validos.
alter table public.users
    alter column auth_version set default 1;
update public.users
set auth_version = 1
where auth_version is null;
alter table public.users
    alter column auth_version set not null;

alter table public.erp_roles
    add column if not exists description text,
    add column if not exists active boolean,
    add column if not exists created_at timestamptz,
    add column if not exists updated_at timestamptz;
alter table public.erp_roles
    alter column description set default '',
    alter column active set default true,
    alter column created_at set default now(),
    alter column updated_at set default now();
update public.erp_roles
set description = coalesce(description, ''),
    active = coalesce(active, true),
    created_at = coalesce(created_at, now()),
    updated_at = coalesce(updated_at, now())
where description is null
   or active is null
   or created_at is null
   or updated_at is null;
alter table public.erp_roles
    alter column description set not null,
    alter column active set not null,
    alter column created_at set not null,
    alter column updated_at set not null;

alter table public.erp_permissions
    add column if not exists description text,
    add column if not exists created_at timestamptz;
alter table public.erp_permissions
    alter column description set default '',
    alter column created_at set default now();
update public.erp_permissions
set description = coalesce(description, ''),
    created_at = coalesce(created_at, now())
where description is null
   or created_at is null;
alter table public.erp_permissions
    alter column description set not null,
    alter column created_at set not null;

alter table public.erp_role_permissions
    add column if not exists created_at timestamptz;
alter table public.erp_role_permissions
    alter column created_at set default now();
update public.erp_role_permissions
set created_at = now()
where created_at is null;
alter table public.erp_role_permissions
    alter column created_at set not null;

alter table public.erp_user_roles
    add column if not exists assigned_by integer,
    add column if not exists assigned_at timestamptz;
alter table public.erp_user_roles
    alter column assigned_at set default now();
update public.erp_user_roles
set assigned_at = now()
where assigned_at is null;
alter table public.erp_user_roles
    alter column assigned_at set not null;

alter table public.erp_user_permission_overrides
    add column if not exists reason text,
    add column if not exists assigned_by integer,
    add column if not exists assigned_at timestamptz;
alter table public.erp_user_permission_overrides
    alter column reason set default '',
    alter column assigned_at set default now();
update public.erp_user_permission_overrides
set reason = coalesce(reason, ''),
    assigned_at = coalesce(assigned_at, now())
where reason is null
   or assigned_at is null;
alter table public.erp_user_permission_overrides
    alter column reason set not null,
    alter column assigned_at set not null;

alter table public.erp_app_sessions
    alter column id set default gen_random_uuid(),
    alter column created_at set default now(),
    alter column last_seen_at set default now();

alter table public.erp_auth_audit_events
    add column if not exists before_data jsonb,
    add column if not exists after_data jsonb,
    add column if not exists reason text,
    add column if not exists origin_app text,
    add column if not exists created_at timestamptz;
alter table public.erp_auth_audit_events
    alter column id set default gen_random_uuid(),
    alter column before_data set default '{}',
    alter column after_data set default '{}',
    alter column reason set default '',
    alter column origin_app set default 'ESTOQUE',
    alter column created_at set default now();
update public.erp_auth_audit_events
set before_data = coalesce(before_data, '{}'),
    after_data = coalesce(after_data, '{}'),
    reason = coalesce(reason, ''),
    origin_app = coalesce(origin_app, 'ESTOQUE'),
    created_at = coalesce(created_at, now())
where before_data is null
   or after_data is null
   or reason is null
   or origin_app is null
   or created_at is null;
alter table public.erp_auth_audit_events
    alter column before_data set not null,
    alter column after_data set not null,
    alter column reason set not null,
    alter column origin_app set not null,
    alter column created_at set not null;

do $$
declare
    role_fk record;
    desired_fk_exists boolean := false;
begin
    -- O ORM antigo declarava ON DELETE CASCADE para role_code. Isso permitiria
    -- apagar memberships ao excluir um perfil. O contrato canonico e RESTRICT.
    for role_fk in
        select c.conname, c.confrelid, c.confdeltype, c.confupdtype
        from pg_constraint c
        where c.conrelid = 'public.erp_user_roles'::regclass
          and c.contype = 'f'
          and c.conkey = array[
              (
                  select a.attnum
                  from pg_attribute a
                  where a.attrelid = 'public.erp_user_roles'::regclass
                    and a.attname = 'role_code'
                    and not a.attisdropped
              )
          ]::smallint[]
    loop
        if role_fk.conname = 'erp_user_roles_role_code_fk'
           and role_fk.confrelid = 'public.erp_roles'::regclass
           and role_fk.confdeltype = 'r'
           and role_fk.confupdtype = 'c' then
            desired_fk_exists := true;
        else
            execute format(
                'alter table public.erp_user_roles drop constraint %I',
                role_fk.conname
            );
        end if;
    end loop;

    if not desired_fk_exists then
        alter table public.erp_user_roles
            add constraint erp_user_roles_role_code_fk
            foreign key (role_code)
            references public.erp_roles(code)
            on update cascade
            on delete restrict
            not valid;
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'erp_roles_code_format_ck'
          and conrelid = 'public.erp_roles'::regclass
    ) then
        alter table public.erp_roles
            add constraint erp_roles_code_format_ck
            check (code = upper(code) and code ~ '^[A-Z][A-Z0-9_]*$')
            not valid;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'erp_permissions_code_format_ck'
          and conrelid = 'public.erp_permissions'::regclass
    ) then
        alter table public.erp_permissions
            add constraint erp_permissions_code_format_ck
            check (code ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')
            not valid;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'erp_app_sessions_app_code_ck'
          and conrelid = 'public.erp_app_sessions'::regclass
    ) then
        alter table public.erp_app_sessions
            add constraint erp_app_sessions_app_code_ck
            check (app_code in ('ESTOQUE','SUPRIMENTOS','MES','CADASTRO'))
            not valid;
    end if;
end
$$;

alter table public.movements
    add column if not exists work_order_id uuid null;
alter table public.movements
    add column if not exists context_kind text null;
alter table public.movements
    add column if not exists setor text null;
alter table public.movements
    add column if not exists reference_text text null;
alter table public.movements
    add column if not exists link_updated_at timestamptz null;
alter table public.movements
    add column if not exists link_updated_by integer null;
alter table public.movements
    add column if not exists movement_status text not null default 'ATIVA';
alter table public.movements
    add column if not exists canceled_at timestamptz null;
alter table public.movements
    add column if not exists canceled_by integer null;
alter table public.movements
    add column if not exists cancel_reason text null;
alter table public.movements
    add column if not exists reversal_movement_id integer null;
alter table public.movements
    add column if not exists source_id uuid null;
alter table public.movements
    add column if not exists source_line_id uuid null;
alter table public.movements
    add column if not exists operation_id uuid null;
alter table public.movements
    add column if not exists parent_movement_id integer null;

alter table public.movements
    alter column movement_status set default 'ATIVA';
update public.movements
set movement_status = 'ATIVA'
where movement_status is null;
alter table public.movements
    alter column movement_status set not null;

do $$
declare
    source_id_type text;
    source_line_id_type text;
    operation_id_type text;
begin
    select pg_catalog.format_type(a.atttypid, a.atttypmod)
    into source_id_type
    from pg_attribute a
    where a.attrelid = 'public.movements'::regclass
      and a.attname = 'source_id'
      and not a.attisdropped;

    select pg_catalog.format_type(a.atttypid, a.atttypmod)
    into source_line_id_type
    from pg_attribute a
    where a.attrelid = 'public.movements'::regclass
      and a.attname = 'source_line_id'
      and not a.attisdropped;

    select pg_catalog.format_type(a.atttypid, a.atttypmod)
    into operation_id_type
    from pg_attribute a
    where a.attrelid = 'public.movements'::regclass
      and a.attname = 'operation_id'
      and not a.attisdropped;

    if source_id_type is distinct from 'uuid'
       or source_line_id_type is distinct from 'uuid'
       or operation_id_type is distinct from 'uuid' then
        raise exception
            'movements.source_id/source_line_id/operation_id devem ser UUID (atual: %/%/%). Corrija por migration dedicada antes do RBAC.',
            coalesce(source_id_type, '(ausente)'),
            coalesce(source_line_id_type, '(ausente)'),
            coalesce(operation_id_type, '(ausente)');
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'movements_parent_not_self_ck'
          and conrelid = 'public.movements'::regclass
    ) then
        alter table public.movements
            add constraint movements_parent_not_self_ck
            check (
                parent_movement_id is null
                or parent_movement_id <> id
            ) not valid;
    end if;
end
$$;

do $$
declare
    fk_spec record;
    existing_fk record;
    desired_fk_exists boolean;
begin
    -- Normaliza FKs que o ORM poderia ter criado com NO ACTION. Sem esta
    -- reconciliacao, uma segunda FK SET NULL/RESTRICT nao corrigiria o efeito
    -- da primeira e exclusoes administrativas continuariam bloqueadas.
    for fk_spec in
        select *
        from (
            values
                (
                    'movements',
                    'work_order_id',
                    'erp_work_orders',
                    'movements_work_order_id_fk',
                    'r',
                    'a',
                    'restrict',
                    'no action'
                ),
                (
                    'movements',
                    'link_updated_by',
                    'users',
                    'movements_link_updated_by_fk',
                    'n',
                    'a',
                    'set null',
                    'no action'
                ),
                (
                    'movements',
                    'canceled_by',
                    'users',
                    'movements_canceled_by_fk',
                    'n',
                    'a',
                    'set null',
                    'no action'
                ),
                (
                    'movements',
                    'reversal_movement_id',
                    'movements',
                    'movements_reversal_movement_id_fk',
                    'n',
                    'a',
                    'set null',
                    'no action'
                ),
                (
                    'movements',
                    'parent_movement_id',
                    'movements',
                    'movements_parent_movement_id_fk',
                    'r',
                    'a',
                    'restrict',
                    'no action'
                )
        ) as f(
            table_name,
            column_name,
            referenced_table,
            constraint_name,
            delete_action_code,
            update_action_code,
            delete_action_sql,
            update_action_sql
        )
    loop
        desired_fk_exists := false;

        for existing_fk in
            select c.conname, c.confrelid, c.confdeltype, c.confupdtype
            from pg_constraint c
            where c.conrelid = format('public.%I', fk_spec.table_name)::regclass
              and c.contype = 'f'
              and c.conkey = array[
                  (
                      select a.attnum
                      from pg_attribute a
                      where a.attrelid =
                          format('public.%I', fk_spec.table_name)::regclass
                        and a.attname = fk_spec.column_name
                        and not a.attisdropped
                  )
              ]::smallint[]
        loop
            if existing_fk.conname = fk_spec.constraint_name
               and existing_fk.confrelid =
                   format('public.%I', fk_spec.referenced_table)::regclass
               and existing_fk.confdeltype = fk_spec.delete_action_code
               and existing_fk.confupdtype = fk_spec.update_action_code then
                desired_fk_exists := true;
            else
                execute format(
                    'alter table public.%I drop constraint %I',
                    fk_spec.table_name,
                    existing_fk.conname
                );
            end if;
        end loop;

        if not desired_fk_exists then
            execute format(
                'alter table public.%I add constraint %I '
                'foreign key (%I) references public.%I(id) '
                'on update %s on delete %s not valid',
                fk_spec.table_name,
                fk_spec.constraint_name,
                fk_spec.column_name,
                fk_spec.referenced_table,
                fk_spec.update_action_sql,
                fk_spec.delete_action_sql
            );
        end if;
    end loop;
end
$$;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'movements_context_kind_ck'
          and conrelid = 'public.movements'::regclass
    ) then
        alter table public.movements
            add constraint movements_context_kind_ck
            check (
                context_kind is null
                or context_kind in ('WORK_ORDER','SETOR','REFERENCIA','LEGACY')
            ) not valid;
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'movements_status_ck'
          and conrelid = 'public.movements'::regclass
    ) then
        alter table public.movements
            add constraint movements_status_ck
            check (movement_status in ('ATIVA','CANCELADA')) not valid;
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'movements_work_order_id_fk'
          and conrelid = 'public.movements'::regclass
    ) then
        alter table public.movements
            add constraint movements_work_order_id_fk
            foreign key (work_order_id)
            references public.erp_work_orders(id)
            on delete restrict
            not valid;
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'movements_link_updated_by_fk'
          and conrelid = 'public.movements'::regclass
    ) then
        alter table public.movements
            add constraint movements_link_updated_by_fk
            foreign key (link_updated_by)
            references public.users(id)
            on delete set null
            not valid;
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'movements_canceled_by_fk'
          and conrelid = 'public.movements'::regclass
    ) then
        alter table public.movements
            add constraint movements_canceled_by_fk
            foreign key (canceled_by)
            references public.users(id)
            on delete set null
            not valid;
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'movements_reversal_movement_id_fk'
          and conrelid = 'public.movements'::regclass
    ) then
        alter table public.movements
            add constraint movements_reversal_movement_id_fk
            foreign key (reversal_movement_id)
            references public.movements(id)
            on delete set null
            not valid;
    end if;
end
$$;

create table if not exists public.erp_movement_reference_history (
    id uuid primary key default gen_random_uuid(),
    movement_id integer not null references public.movements(id) on delete restrict,
    previous_work_order_id uuid null references public.erp_work_orders(id) on delete restrict,
    new_work_order_id uuid null references public.erp_work_orders(id) on delete restrict,
    previous_context_kind text null,
    new_context_kind text null,
    previous_setor text null,
    new_setor text null,
    previous_reference_text text null,
    new_reference_text text null,
    changed_by integer null references public.users(id) on delete set null,
    reason text not null,
    created_at timestamptz not null default now()
);

alter table public.erp_movement_reference_history
    alter column id set default gen_random_uuid(),
    alter column reason set default '',
    alter column created_at set default now();
update public.erp_movement_reference_history
set reason = coalesce(reason, ''),
    created_at = coalesce(created_at, now())
where reason is null
   or created_at is null;
alter table public.erp_movement_reference_history
    alter column reason set not null,
    alter column created_at set not null;

do $$
declare
    fk_spec record;
    existing_fk record;
    desired_fk_exists boolean;
begin
    for fk_spec in
        select *
        from (
            values
                (
                    'erp_user_roles',
                    'assigned_by',
                    'users',
                    'erp_user_roles_assigned_by_fk',
                    'n',
                    'a',
                    'set null',
                    'no action'
                ),
                (
                    'erp_user_permission_overrides',
                    'assigned_by',
                    'users',
                    'erp_user_permission_overrides_assigned_by_fk',
                    'n',
                    'a',
                    'set null',
                    'no action'
                ),
                (
                    'erp_movement_reference_history',
                    'movement_id',
                    'movements',
                    'erp_movement_reference_history_movement_id_fk',
                    'r',
                    'a',
                    'restrict',
                    'no action'
                ),
                (
                    'erp_movement_reference_history',
                    'previous_work_order_id',
                    'erp_work_orders',
                    'erp_movement_reference_history_previous_work_order_id_fk',
                    'r',
                    'a',
                    'restrict',
                    'no action'
                ),
                (
                    'erp_movement_reference_history',
                    'new_work_order_id',
                    'erp_work_orders',
                    'erp_movement_reference_history_new_work_order_id_fk',
                    'r',
                    'a',
                    'restrict',
                    'no action'
                ),
                (
                    'erp_movement_reference_history',
                    'changed_by',
                    'users',
                    'erp_movement_reference_history_changed_by_fk',
                    'n',
                    'a',
                    'set null',
                    'no action'
                )
        ) as f(
            table_name,
            column_name,
            referenced_table,
            constraint_name,
            delete_action_code,
            update_action_code,
            delete_action_sql,
            update_action_sql
        )
    loop
        desired_fk_exists := false;

        for existing_fk in
            select c.conname, c.confrelid, c.confdeltype, c.confupdtype
            from pg_constraint c
            where c.conrelid = format('public.%I', fk_spec.table_name)::regclass
              and c.contype = 'f'
              and c.conkey = array[
                  (
                      select a.attnum
                      from pg_attribute a
                      where a.attrelid =
                          format('public.%I', fk_spec.table_name)::regclass
                        and a.attname = fk_spec.column_name
                        and not a.attisdropped
                  )
              ]::smallint[]
        loop
            if existing_fk.conname = fk_spec.constraint_name
               and existing_fk.confrelid =
                   format('public.%I', fk_spec.referenced_table)::regclass
               and existing_fk.confdeltype = fk_spec.delete_action_code
               and existing_fk.confupdtype = fk_spec.update_action_code then
                desired_fk_exists := true;
            else
                execute format(
                    'alter table public.%I drop constraint %I',
                    fk_spec.table_name,
                    existing_fk.conname
                );
            end if;
        end loop;

        if not desired_fk_exists then
            execute format(
                'alter table public.%I add constraint %I '
                'foreign key (%I) references public.%I(id) '
                'on update %s on delete %s not valid',
                fk_spec.table_name,
                fk_spec.constraint_name,
                fk_spec.column_name,
                fk_spec.referenced_table,
                fk_spec.update_action_sql,
                fk_spec.delete_action_sql
            );
        end if;
    end loop;
end
$$;

insert into public.erp_roles (code, name, description)
values
    ('ADMIN', 'Administrador', 'Acesso integral e administracao de usuarios.'),
    ('OPERADOR', 'Operador', 'Execucao operacional de estoque e MES.'),
    ('COMPRADOR', 'Comprador', 'Criacao e acompanhamento de ordens de compra.'),
    ('FINANCEIRO', 'Financeiro', 'Consulta e conclusao financeira de compras.'),
    ('PCP', 'PCP', 'Planejamento e controle operacional.'),
    ('ENGENHARIA', 'Engenharia', 'Operacao completa e acesso a dados mestres.')
on conflict (code) do update
set name = excluded.name,
    description = excluded.description,
    updated_at = now();

-- ADMIN e um perfil de recuperacao obrigatorio. Outros perfis preservam o
-- estado ativo/inativo que ja possuam.
update public.erp_roles
set active = true,
    updated_at = now()
where code = 'ADMIN'
  and active is distinct from true;

insert into public.erp_permissions (code, module, description)
values
    ('estoque.dashboard.view', 'ESTOQUE', 'Consultar dashboard.'),
    ('estoque.entry.create', 'ESTOQUE', 'Registrar entradas.'),
    ('estoque.commitment.create', 'ESTOQUE', 'Registrar empenhos.'),
    ('estoque.consumption.create', 'ESTOQUE', 'Registrar baixas.'),
    ('estoque.movement.view', 'ESTOQUE', 'Consultar movimentacoes.'),
    ('estoque.movement.cancel_own', 'ESTOQUE', 'Cancelar movimentacao propria.'),
    ('estoque.movement.cancel_any', 'ESTOQUE', 'Cancelar qualquer movimentacao permitida.'),
    ('estoque.inspection.receive', 'ESTOQUE', 'Confirmar inspecao de recebimento.'),
    ('estoque.labels.use', 'ESTOQUE', 'Gerar etiquetas.'),
    ('estoque.stock.view', 'ESTOQUE', 'Consultar estoque.'),
    ('estoque.reports.view', 'ESTOQUE', 'Consultar e exportar relatorios.'),
    ('estoque.skus.view', 'ESTOQUE', 'Consultar CODs/SKUs.'),
    ('estoque.skus.manage', 'ESTOQUE', 'Gerenciar CODs/SKUs.'),
    ('estoque.inventory.manage', 'ESTOQUE', 'Executar inventario.'),
    ('estoque.import', 'ESTOQUE', 'Executar importacoes operacionais.'),
    ('estoque.settings.manage', 'ESTOQUE', 'Alterar configuracoes do modulo.'),
    ('estoque.users.manage', 'ESTOQUE', 'Gerenciar usuarios e acessos.'),
    ('suprimentos.dashboard.view', 'SUPRIMENTOS', 'Consultar dashboard.'),
    ('suprimentos.purchase.view', 'SUPRIMENTOS', 'Consultar compras.'),
    ('suprimentos.purchase.create', 'SUPRIMENTOS', 'Criar ordens de compra.'),
    ('suprimentos.purchase.edit', 'SUPRIMENTOS', 'Editar ordens de compra.'),
    ('suprimentos.purchase.cancel', 'SUPRIMENTOS', 'Cancelar ordens de compra.'),
    ('suprimentos.purchase.technical_close', 'SUPRIMENTOS', 'Realizar conclusao tecnica.'),
    ('suprimentos.purchase.financial_close', 'SUPRIMENTOS', 'Realizar conclusao financeira.'),
    ('suprimentos.purchase.export', 'SUPRIMENTOS', 'Exportar relatorios de compras.'),
    ('suprimentos.purchase.bulk_manage', 'SUPRIMENTOS', 'Executar operacoes em lote.'),
    ('suprimentos.work_order.view', 'SUPRIMENTOS', 'Consultar gestao de O.S.'),
    ('suprimentos.work_order.manage', 'SUPRIMENTOS', 'Criar, editar e ativar O.S.'),
    ('suprimentos.work_order.schedule', 'SUPRIMENTOS', 'Programar e reprogramar O.S.'),
    ('suprimentos.work_order.technical_close', 'SUPRIMENTOS', 'Concluir ou reabrir O.S.'),
    ('suprimentos.work_order.import', 'SUPRIMENTOS', 'Executar importacoes/reconciliacoes de O.S.'),
    ('suprimentos.master_data.manage', 'SUPRIMENTOS', 'Gerenciar dados mestres auxiliares.'),
    ('suprimentos.system.admin', 'SUPRIMENTOS', 'Administrar o modulo.'),
    ('mes.dashboard.read', 'MES', 'Consultar as quatro visoes e cards.'),
    ('mes.stage.write', 'MES', 'Apontar etapas e localizacao.'),
    ('mes.exports.read', 'MES', 'Exportar controle, logs e tempos.'),
    ('mes.work_orders.manage', 'MES', 'Gerenciar O.S. e historico.'),
    ('mes.vehicle_entries.create', 'MES', 'Registrar entrada de veiculo.'),
    ('mes.schedule.manage', 'MES', 'Programar e reprogramar producao.'),
    ('mes.finalize', 'MES', 'Finalizar e entregar O.S.'),
    ('mes.legacy.import', 'MES', 'Importar e limpar dados legados.'),
    ('mes.users.manage', 'MES', 'Acessar administracao de usuarios.'),
    ('cadastro.access', 'CADASTRO', 'Acessar o Modulo Cadastro.')
on conflict (code) do update
set module = excluded.module,
    description = excluded.description;

-- Os seis perfis internos possuem matriz fechada. Excecoes por usuario ficam
-- exclusivamente em erp_user_permission_overrides.
delete from public.erp_role_permissions
where role_code in (
    'ADMIN', 'OPERADOR', 'COMPRADOR', 'FINANCEIRO', 'PCP', 'ENGENHARIA'
);

-- ADMIN recebe todas as permissoes atuais; o backend tambem trata ADMIN como superperfil.
insert into public.erp_role_permissions (role_code, permission_code)
select 'ADMIN', p.code
from public.erp_permissions p
on conflict do nothing;

insert into public.erp_role_permissions (role_code, permission_code)
values
    ('OPERADOR','estoque.dashboard.view'),
    ('OPERADOR','estoque.entry.create'),
    ('OPERADOR','estoque.commitment.create'),
    ('OPERADOR','estoque.consumption.create'),
    ('OPERADOR','estoque.movement.view'),
    ('OPERADOR','estoque.movement.cancel_own'),
    ('OPERADOR','estoque.inspection.receive'),
    ('OPERADOR','estoque.labels.use'),
    ('OPERADOR','estoque.stock.view'),
    ('OPERADOR','estoque.reports.view'),
    ('OPERADOR','estoque.skus.view'),
    ('OPERADOR','suprimentos.dashboard.view'),
    ('OPERADOR','suprimentos.purchase.view'),
    ('OPERADOR','suprimentos.work_order.view'),
    ('OPERADOR','mes.dashboard.read'),
    ('OPERADOR','mes.stage.write'),
    ('OPERADOR','mes.exports.read'),

    ('COMPRADOR','estoque.dashboard.view'),
    ('COMPRADOR','estoque.stock.view'),
    ('COMPRADOR','estoque.movement.view'),
    ('COMPRADOR','estoque.skus.view'),
    ('COMPRADOR','estoque.reports.view'),
    ('COMPRADOR','suprimentos.dashboard.view'),
    ('COMPRADOR','suprimentos.purchase.view'),
    ('COMPRADOR','suprimentos.purchase.create'),
    ('COMPRADOR','suprimentos.purchase.edit'),
    ('COMPRADOR','suprimentos.purchase.cancel'),
    ('COMPRADOR','suprimentos.purchase.export'),
    ('COMPRADOR','suprimentos.work_order.view'),
    ('COMPRADOR','mes.dashboard.read'),
    ('COMPRADOR','mes.stage.write'),
    ('COMPRADOR','mes.exports.read'),

    ('FINANCEIRO','estoque.dashboard.view'),
    ('FINANCEIRO','estoque.stock.view'),
    ('FINANCEIRO','estoque.movement.view'),
    ('FINANCEIRO','estoque.skus.view'),
    ('FINANCEIRO','estoque.reports.view'),
    ('FINANCEIRO','suprimentos.dashboard.view'),
    ('FINANCEIRO','suprimentos.purchase.view'),
    ('FINANCEIRO','suprimentos.purchase.financial_close'),
    ('FINANCEIRO','suprimentos.purchase.export'),
    ('FINANCEIRO','suprimentos.work_order.view'),

    ('PCP','estoque.dashboard.view'),
    ('PCP','estoque.entry.create'),
    ('PCP','estoque.commitment.create'),
    ('PCP','estoque.consumption.create'),
    ('PCP','estoque.movement.view'),
    ('PCP','estoque.movement.cancel_own'),
    ('PCP','estoque.movement.cancel_any'),
    ('PCP','estoque.inspection.receive'),
    ('PCP','estoque.labels.use'),
    ('PCP','estoque.stock.view'),
    ('PCP','estoque.reports.view'),
    ('PCP','estoque.skus.view'),
    ('PCP','estoque.skus.manage'),
    ('PCP','estoque.inventory.manage'),
    ('PCP','estoque.import'),
    ('PCP','suprimentos.dashboard.view'),
    ('PCP','suprimentos.purchase.view'),
    ('PCP','suprimentos.purchase.create'),
    ('PCP','suprimentos.purchase.edit'),
    ('PCP','suprimentos.purchase.cancel'),
    ('PCP','suprimentos.purchase.technical_close'),
    ('PCP','suprimentos.purchase.financial_close'),
    ('PCP','suprimentos.purchase.export'),
    ('PCP','suprimentos.purchase.bulk_manage'),
    ('PCP','suprimentos.work_order.view'),
    ('PCP','suprimentos.work_order.manage'),
    ('PCP','suprimentos.work_order.schedule'),
    ('PCP','suprimentos.work_order.technical_close'),
    ('PCP','suprimentos.work_order.import'),
    ('PCP','mes.dashboard.read'),
    ('PCP','mes.stage.write'),
    ('PCP','mes.exports.read'),
    ('PCP','mes.work_orders.manage'),
    ('PCP','mes.vehicle_entries.create'),
    ('PCP','mes.schedule.manage'),
    ('PCP','mes.finalize'),

    ('ENGENHARIA','estoque.dashboard.view'),
    ('ENGENHARIA','estoque.entry.create'),
    ('ENGENHARIA','estoque.commitment.create'),
    ('ENGENHARIA','estoque.consumption.create'),
    ('ENGENHARIA','estoque.movement.view'),
    ('ENGENHARIA','estoque.movement.cancel_own'),
    ('ENGENHARIA','estoque.movement.cancel_any'),
    ('ENGENHARIA','estoque.inspection.receive'),
    ('ENGENHARIA','estoque.labels.use'),
    ('ENGENHARIA','estoque.stock.view'),
    ('ENGENHARIA','estoque.reports.view'),
    ('ENGENHARIA','estoque.skus.view'),
    ('ENGENHARIA','estoque.skus.manage'),
    ('ENGENHARIA','estoque.inventory.manage'),
    ('ENGENHARIA','estoque.import'),
    ('ENGENHARIA','suprimentos.dashboard.view'),
    ('ENGENHARIA','suprimentos.purchase.view'),
    ('ENGENHARIA','suprimentos.purchase.create'),
    ('ENGENHARIA','suprimentos.purchase.edit'),
    ('ENGENHARIA','suprimentos.purchase.cancel'),
    ('ENGENHARIA','suprimentos.purchase.technical_close'),
    ('ENGENHARIA','suprimentos.purchase.financial_close'),
    ('ENGENHARIA','suprimentos.purchase.export'),
    ('ENGENHARIA','suprimentos.purchase.bulk_manage'),
    ('ENGENHARIA','suprimentos.work_order.view'),
    ('ENGENHARIA','suprimentos.work_order.manage'),
    ('ENGENHARIA','suprimentos.work_order.schedule'),
    ('ENGENHARIA','suprimentos.work_order.technical_close'),
    ('ENGENHARIA','suprimentos.work_order.import'),
    ('ENGENHARIA','mes.dashboard.read'),
    ('ENGENHARIA','mes.stage.write'),
    ('ENGENHARIA','mes.exports.read'),
    ('ENGENHARIA','mes.work_orders.manage'),
    ('ENGENHARIA','mes.vehicle_entries.create'),
    ('ENGENHARIA','mes.schedule.manage'),
    ('ENGENHARIA','mes.finalize'),
    ('ENGENHARIA','cadastro.access')
on conflict do nothing;

-- Compatibilidade: nao altera users.role; apenas cria o primeiro vinculo de perfil.
insert into public.erp_user_roles (user_id, role_code)
select
    u.id,
    case
        when upper(coalesce(u.role, '')) in ('ADM','ADMIN') then 'ADMIN'
        else upper(u.role)
    end
from public.users u
where not exists (
    select 1
    from public.erp_user_roles ur
    where ur.user_id = u.id
)
and upper(coalesce(u.role, '')) in (
    'ADM', 'ADMIN', 'OPERADOR', 'COMPRADOR', 'FINANCEIRO', 'PCP', 'ENGENHARIA'
)
on conflict do nothing;

alter table public.erp_roles
    validate constraint erp_roles_code_format_ck;
alter table public.erp_permissions
    validate constraint erp_permissions_code_format_ck;
alter table public.erp_app_sessions
    validate constraint erp_app_sessions_app_code_ck;
alter table public.erp_user_roles
    validate constraint erp_user_roles_role_code_fk;
alter table public.erp_user_roles
    validate constraint erp_user_roles_assigned_by_fk;
alter table public.erp_user_permission_overrides
    validate constraint erp_user_permission_overrides_assigned_by_fk;
alter table public.erp_movement_reference_history
    validate constraint erp_movement_reference_history_movement_id_fk;
alter table public.erp_movement_reference_history
    validate constraint erp_movement_reference_history_previous_work_order_id_fk;
alter table public.erp_movement_reference_history
    validate constraint erp_movement_reference_history_new_work_order_id_fk;
alter table public.erp_movement_reference_history
    validate constraint erp_movement_reference_history_changed_by_fk;

do $$
declare
    users_without_role bigint;
    users_without_role_sample text;
begin
    select
        count(*),
        string_agg(format('%s:%s', missing.id, missing.username), ', ')
            filter (where missing.sample_order <= 10)
    into users_without_role, users_without_role_sample
    from (
        select
            u.id,
            u.username,
            row_number() over (order by u.id) as sample_order
        from public.users u
        where u.active = true
          and not exists (
              select 1
              from public.erp_user_roles ur
              join public.erp_roles r on r.code = ur.role_code
              where ur.user_id = u.id
                and r.active = true
          )
    ) missing;

    if users_without_role > 0 then
        raise exception
            'Existem % usuario(s) ativo(s) sem perfil RBAC ativo. Corrija explicitamente antes do corte. Amostra: %',
            users_without_role,
            coalesce(users_without_role_sample, '(sem amostra)');
    end if;

    if not exists (
        select 1
        from public.erp_user_roles ur
        join public.users u on u.id = ur.user_id
        join public.erp_roles r on r.code = ur.role_code
        where ur.role_code = 'ADMIN'
          and u.active = true
          and r.active = true
    ) then
        raise exception
            'Nenhum ADMIN ativo foi reconciliado. A migration foi abortada para evitar bloqueio administrativo.';
    end if;
end
$$;

create index if not exists erp_user_roles_role_code_idx
    on public.erp_user_roles(role_code, user_id);
create index if not exists erp_user_roles_assigned_by_idx
    on public.erp_user_roles(assigned_by)
    where assigned_by is not null;
create index if not exists erp_role_permissions_permission_idx
    on public.erp_role_permissions(permission_code, role_code);
create index if not exists erp_user_permission_overrides_assigned_by_idx
    on public.erp_user_permission_overrides(assigned_by)
    where assigned_by is not null;
create index if not exists erp_app_sessions_active_idx
    on public.erp_app_sessions(user_id, app_code, expires_at)
    where revoked_at is null;
create index if not exists erp_auth_audit_events_user_idx
    on public.erp_auth_audit_events(user_id, created_at);
create index if not exists erp_auth_audit_events_actor_idx
    on public.erp_auth_audit_events(actor_user_id, created_at)
    where actor_user_id is not null;
create index if not exists movements_work_order_id_idx
    on public.movements(work_order_id, tipo, created_at)
    where work_order_id is not null;
create index if not exists movements_sku_active_tipo_idx
    on public.movements(sku_id, tipo, created_at)
    where movement_status = 'ATIVA';
create index if not exists movements_active_related_idx
    on public.movements(related_movement_id, tipo)
    where movement_status = 'ATIVA';
create index if not exists movements_reversal_movement_id_idx
    on public.movements(reversal_movement_id)
    where reversal_movement_id is not null;
create index if not exists movements_operation_id_idx
    on public.movements(operation_id, parent_movement_id)
    where operation_id is not null;
create index if not exists movements_parent_movement_id_idx
    on public.movements(parent_movement_id)
    where parent_movement_id is not null;
create index if not exists movements_link_updated_by_idx
    on public.movements(link_updated_by)
    where link_updated_by is not null;
create index if not exists movements_canceled_by_idx
    on public.movements(canceled_by)
    where canceled_by is not null;
create index if not exists erp_movement_reference_history_movement_idx
    on public.erp_movement_reference_history(movement_id, created_at);
create index if not exists erp_movement_reference_history_changed_by_idx
    on public.erp_movement_reference_history(changed_by, created_at)
    where changed_by is not null;

alter table public.movements validate constraint movements_context_kind_ck;
alter table public.movements validate constraint movements_status_ck;
alter table public.movements validate constraint movements_parent_not_self_ck;
alter table public.movements validate constraint movements_work_order_id_fk;
alter table public.movements validate constraint movements_link_updated_by_fk;
alter table public.movements validate constraint movements_canceled_by_fk;
alter table public.movements validate constraint movements_reversal_movement_id_fk;
alter table public.movements validate constraint movements_parent_movement_id_fk;

create or replace function public.erp_get_user_access(p_user_id integer)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $function$
    with selected_user as (
        select id, username, active, auth_version
        from public.users
        where id = p_user_id
    ),
    selected_roles as (
        select r.code
        from public.erp_user_roles ur
        join public.erp_roles r on r.code = ur.role_code
        where ur.user_id = p_user_id
          and r.active = true
    ),
    role_permissions as (
        select distinct rp.permission_code
        from public.erp_role_permissions rp
        join selected_roles r on r.code = rp.role_code
    ),
    effective_permissions as (
        select rp.permission_code
        from role_permissions rp
        where not exists (
            select 1
            from public.erp_user_permission_overrides o
            where o.user_id = p_user_id
              and o.permission_code = rp.permission_code
              and o.allowed = false
        )
        union
        select o.permission_code
        from public.erp_user_permission_overrides o
        where o.user_id = p_user_id
          and o.allowed = true
    )
    select coalesce(
        (
            select jsonb_build_object(
                'user_id', u.id,
                'username', u.username,
                'active', u.active,
                'auth_version', u.auth_version,
                'roles', coalesce(
                    (select jsonb_agg(code order by code) from selected_roles),
                    '[]'::jsonb
                ),
                'permissions', coalesce(
                    (
                        select jsonb_agg(permission_code order by permission_code)
                        from effective_permissions
                    ),
                    '[]'::jsonb
                )
            )
            from selected_user u
        ),
        '{}'::jsonb
    );
$function$;

-- Novas tabelas sao exclusivamente de backend.
do $$
declare
    table_name text;
    table_names constant text[] := array[
        'erp_roles',
        'erp_permissions',
        'erp_role_permissions',
        'erp_user_roles',
        'erp_user_permission_overrides',
        'erp_app_sessions',
        'erp_auth_audit_events',
        'erp_movement_reference_history'
    ];
begin
    foreach table_name in array table_names loop
        execute format('alter table public.%I enable row level security', table_name);
        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format('revoke all on table public.%I from anon', table_name);
        end if;
        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format('revoke all on table public.%I from authenticated', table_name);
        end if;
        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format(
                'grant select, insert, update, delete on table public.%I to service_role',
                table_name
            );
        end if;
    end loop;

    revoke all on function public.erp_get_user_access(integer) from public;
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on function public.erp_get_user_access(integer) from anon;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        revoke all on function public.erp_get_user_access(integer) from authenticated;
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant execute on function public.erp_get_user_access(integer) to service_role;
    end if;
end
$$;

create or replace function public.erp_users_auth_version_bump()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $function$
begin
    if new.username is distinct from old.username
       or new.password_hash is distinct from old.password_hash
       or new.active is distinct from old.active then
        if new.auth_version = old.auth_version then
            new.auth_version := old.auth_version + 1;
        end if;
    end if;
    return new;
end
$function$;

drop trigger if exists erp_users_auth_version_bump_trg on public.users;
create trigger erp_users_auth_version_bump_trg
before update on public.users
for each row execute function public.erp_users_auth_version_bump();

create or replace function public.erp_membership_auth_version_bump()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $function$
declare
    previous_user_id integer;
    current_user_id integer;
begin
    if tg_op <> 'INSERT' then
        previous_user_id := old.user_id;
    end if;
    if tg_op <> 'DELETE' then
        current_user_id := new.user_id;
    end if;

    update public.users
    set auth_version = auth_version + 1
    where id in (
        coalesce(previous_user_id, current_user_id),
        coalesce(current_user_id, previous_user_id)
    );
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end
$function$;

drop trigger if exists erp_user_roles_auth_version_trg on public.erp_user_roles;
create trigger erp_user_roles_auth_version_trg
after insert or update or delete on public.erp_user_roles
for each row execute function public.erp_membership_auth_version_bump();

drop trigger if exists erp_user_overrides_auth_version_trg
    on public.erp_user_permission_overrides;
create trigger erp_user_overrides_auth_version_trg
after insert or update or delete on public.erp_user_permission_overrides
for each row execute function public.erp_membership_auth_version_bump();

create or replace function public.erp_guard_last_admin_membership()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $function$
declare
    removing_admin boolean := false;
begin
    perform pg_advisory_xact_lock(982734621);

    if tg_op = 'DELETE' then
        removing_admin := true;
    else
        removing_admin :=
            new.role_code is distinct from old.role_code
            or new.user_id is distinct from old.user_id;
    end if;

    if old.role_code = 'ADMIN'
       and removing_admin
       and exists (
           select 1 from public.users u
           where u.id = old.user_id and u.active = true
       )
       and not exists (
           select 1
           from public.erp_user_roles ur
           join public.users u on u.id = ur.user_id
           where ur.role_code = 'ADMIN'
             and u.active = true
             and ur.user_id <> old.user_id
       ) then
        raise exception 'Nao e permitido remover o ultimo ADMIN ativo.';
    end if;
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end
$function$;

drop trigger if exists erp_guard_last_admin_membership_trg
    on public.erp_user_roles;
create trigger erp_guard_last_admin_membership_trg
before update or delete on public.erp_user_roles
for each row execute function public.erp_guard_last_admin_membership();

create or replace function public.erp_guard_last_admin_user()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $function$
declare
    removing_admin_user boolean := false;
begin
    perform pg_advisory_xact_lock(982734621);

    if tg_op = 'DELETE' then
        removing_admin_user := true;
    else
        removing_admin_user := old.active = true and new.active = false;
    end if;

    if exists (
        select 1
        from public.erp_user_roles ur
        where ur.user_id = old.id and ur.role_code = 'ADMIN'
    )
       and removing_admin_user
       and not exists (
           select 1
           from public.erp_user_roles ur
           join public.users u on u.id = ur.user_id
           where ur.role_code = 'ADMIN'
             and u.active = true
             and ur.user_id <> old.id
       ) then
        raise exception 'Nao e permitido desativar ou excluir o ultimo ADMIN ativo.';
    end if;
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end
$function$;

drop trigger if exists erp_guard_last_admin_user_trg on public.users;
create trigger erp_guard_last_admin_user_trg
before update of active or delete on public.users
for each row execute function public.erp_guard_last_admin_user();

create or replace function public.erp_guard_admin_role_active()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $function$
begin
    perform pg_catalog.pg_advisory_xact_lock(982734621);

    if tg_op = 'DELETE' then
        if old.code = 'ADMIN' then
            raise exception
                'O perfil ADMIN e estrutural e nao pode ser excluido.';
        end if;
        return old;
    end if;

    if new.code = 'ADMIN' and new.active is distinct from true then
        raise exception
            'O perfil ADMIN deve permanecer ativo.';
    end if;

    if tg_op = 'UPDATE'
       and old.code = 'ADMIN'
       and new.code is distinct from 'ADMIN' then
        raise exception
            'O codigo do perfil ADMIN nao pode ser alterado.';
    end if;

    return new;
end
$function$;

drop trigger if exists erp_guard_admin_role_active_trg on public.erp_roles;
create trigger erp_guard_admin_role_active_trg
before insert or update or delete on public.erp_roles
for each row execute function public.erp_guard_admin_role_active();

revoke all on function public.erp_users_auth_version_bump() from public;
revoke all on function public.erp_membership_auth_version_bump() from public;
revoke all on function public.erp_guard_last_admin_membership() from public;
revoke all on function public.erp_guard_last_admin_user() from public;
revoke all on function public.erp_guard_admin_role_active() from public;

comment on column public.users.auth_version is
    'Incrementar ao trocar senha, perfis, overrides ou estado ativo para revogar sessoes.';
comment on column public.movements.work_order_id is
    'Vinculo estrutural opcional com a ocorrencia operacional da O.S.; nunca inferir por chassi.';
comment on column public.movements.source_id is
    'Linhagem do documento de origem (ex.: recebimento). Nao reutilizar como vinculo de O.S.';
comment on table public.erp_movement_reference_history is
    'Auditoria de correcoes do vinculo operacional de empenhos e baixas.';

commit;
