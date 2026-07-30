-- A etapa bloqueada foi retirada do contrato operacional do MES.
-- Não altera apontamentos; falha se existir algum registro bloqueado.
begin;
set local lock_timeout = 5000;
set local statement_timeout = 60000;

do $$
begin
    if exists (
        select 1
        from public.erp_work_order_stages
        where status = 'BLOQUEADA'
    ) then
        raise exception
            'Existem etapas BLOQUEADA; normalize-as antes de alterar a constraint.';
    end if;
end
$$;

alter table public.erp_work_order_stages
    drop constraint if exists erp_work_order_stages_status_check;

alter table public.erp_work_order_stages
    drop constraint if exists ck_erp_work_order_stages_status;

alter table public.erp_work_order_stages
    add constraint ck_erp_work_order_stages_status
    check (
        status in (
            'NÃO_APLICÁVEL',
            'PENDENTE',
            'LIBERADA',
            'EM_ANDAMENTO',
            'CONCLUÍDA'
        )
    );

comment on constraint ck_erp_work_order_stages_status
    on public.erp_work_order_stages is
    'Etapas usam N, P, S ou N/A; estado bloqueado não integra o contrato MES.';

commit;
