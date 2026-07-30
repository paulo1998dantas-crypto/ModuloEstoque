-- Endurece a consistência entre VIN completo e o identificador legado reduzido.
-- Esta migration sucede 202607290800 sem reescrever o histórico já aplicado.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 60000;

do $$
begin
    if to_regclass('public.erp_vehicles') is null then
        raise exception 'Tabela obrigatória public.erp_vehicles ainda não existe.';
    end if;
end
$$;

alter table public.erp_vehicles
    drop constraint if exists erp_vehicles_legacy_chassis_consistency_check;

alter table public.erp_vehicles
    add constraint erp_vehicles_legacy_chassis_consistency_check
    check (
        (
            chassi_completo
            and legacy_chassi_reduzido is null
            and chassi ~ '^[A-Z0-9]{17}$'
        )
        or
        (
            not chassi_completo
            and legacy_chassi_reduzido is not null
            and legacy_chassi_reduzido ~ '^[A-Z0-9]{8}$'
            and legacy_chassi_reduzido =
                right(
                    regexp_replace(upper(chassi), '[^A-Z0-9]', '', 'g'),
                    8
                )
        )
    )
    not valid;

alter table public.erp_vehicles
    validate constraint erp_vehicles_legacy_chassis_consistency_check;

commit;
