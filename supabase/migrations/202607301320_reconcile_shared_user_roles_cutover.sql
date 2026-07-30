-- Reconciliacao final de perfis imediatamente antes do corte do RBAC.
-- Aditiva e idempotente: nao altera users.role e nao remove vinculos existentes.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 30000;

select pg_advisory_xact_lock(982734621);

do $$
begin
    if to_regclass('public.users') is null
       or to_regclass('public.erp_roles') is null
       or to_regclass('public.erp_user_roles') is null then
        raise exception
            'Schema RBAC compartilhado incompleto. Aplique primeiro a migration 202607301300.';
    end if;
end
$$;

-- Cobre usuarios criados entre a migration estrutural e o deploy do servico
-- que passa a persistir o perfil mesmo com a feature flag ainda desligada.
insert into public.erp_user_roles (user_id, role_code)
select
    u.id,
    case
        when upper(coalesce(u.role, '')) in ('ADM', 'ADMIN') then 'ADMIN'
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

do $$
declare
    missing_count bigint;
    missing_sample text;
    primary_mismatch_count bigint;
    primary_mismatch_sample text;
begin
    select
        count(*),
        string_agg(format('%s:%s', missing.id, missing.username), ', ')
            filter (where missing.sample_order <= 10)
    into missing_count, missing_sample
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

    if missing_count > 0 then
        raise exception
            'Corte RBAC bloqueado: % usuario(s) ativo(s) sem perfil ativo. Amostra: %',
            missing_count,
            coalesce(missing_sample, '(sem amostra)');
    end if;

    -- Nao sobrescreve multi-perfil. Se users.role mudou durante a janela e o
    -- vinculo antigo permaneceu, exige revisao explicita em vez de adivinhar.
    select
        count(*),
        string_agg(
            format(
                '%s:%s (users.role=%s)',
                mismatch.id,
                mismatch.username,
                mismatch.legacy_role
            ),
            ', '
        ) filter (where mismatch.sample_order <= 10)
    into primary_mismatch_count, primary_mismatch_sample
    from (
        select
            candidate.*,
            row_number() over (order by candidate.id) as sample_order
        from (
            select
                u.id,
                u.username,
                upper(coalesce(u.role, '')) as legacy_role,
                case
                    when upper(coalesce(u.role, '')) in ('ADM', 'ADMIN')
                        then 'ADMIN'
                    else upper(u.role)
                end as expected_role
            from public.users u
            where u.active = true
              and upper(coalesce(u.role, '')) in (
                  'ADM', 'ADMIN', 'OPERADOR', 'COMPRADOR',
                  'FINANCEIRO', 'PCP', 'ENGENHARIA'
              )
              and not exists (
                  select 1
                  from public.erp_user_roles ur
                  join public.erp_roles r on r.code = ur.role_code
                  where ur.user_id = u.id
                    and ur.role_code = case
                        when upper(coalesce(u.role, '')) in ('ADM', 'ADMIN')
                            then 'ADMIN'
                        else upper(u.role)
                    end
                    and r.active = true
              )
        ) candidate
    ) mismatch;

    if primary_mismatch_count > 0 then
        raise exception
            'Corte RBAC bloqueado: % usuario(s) com users.role sem o vinculo primario correspondente. Corrija manualmente sem remover outros perfis. Amostra: %',
            primary_mismatch_count,
            coalesce(primary_mismatch_sample, '(sem amostra)');
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
            'Corte RBAC bloqueado: nenhum ADMIN ativo foi reconciliado.';
    end if;
end
$$;

commit;
