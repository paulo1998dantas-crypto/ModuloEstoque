-- Suporte aditivo à promoção segura de chassis legados reduzidos para VIN completo.
-- Não altera chassis existentes nem tenta inferir VIN a partir dos oito caracteres.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 60000;

alter table if exists public.erp_vehicles
    add column if not exists chassi_completo boolean not null default true,
    add column if not exists legacy_chassi_reduzido text null;

do $$
begin
    if to_regclass('public.erp_vehicles') is null then
        raise exception 'Tabela obrigatória public.erp_vehicles ainda não existe.';
    end if;

    if not exists (
        select 1
          from pg_constraint
         where conname = 'erp_vehicles_legacy_chassis_consistency_check'
           and conrelid = 'public.erp_vehicles'::regclass
    ) then
        alter table public.erp_vehicles
            add constraint erp_vehicles_legacy_chassis_consistency_check
            check (
                (
                    chassi_completo
                    and legacy_chassi_reduzido is null
                )
                or
                (
                    not chassi_completo
                    and legacy_chassi_reduzido ~ '^[A-Z0-9]{8}$'
                    and legacy_chassi_reduzido =
                        right(
                            regexp_replace(upper(chassi), '[^A-Z0-9]', '', 'g'),
                            8
                        )
                )
            )
            not valid;
    end if;
end
$$;

create unique index if not exists erp_vehicles_legacy_chassi_reduzido_uidx
    on public.erp_vehicles(legacy_chassi_reduzido)
    where not chassi_completo
      and legacy_chassi_reduzido is not null;

comment on column public.erp_vehicles.chassi_completo is
    'True quando chassi contém o VIN canônico completo; false para registro legado ainda não promovido.';

comment on column public.erp_vehicles.legacy_chassi_reduzido is
    'Últimos oito caracteres normalizados usados somente para reconciliar um registro legado incompleto.';

commit;

-- Validar somente depois da reconciliação:
-- alter table public.erp_vehicles
--     validate constraint erp_vehicles_legacy_chassis_consistency_check;
