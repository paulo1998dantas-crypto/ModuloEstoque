-- Canonical closure states: the management status is CONCLUIDA after a
-- technical O.C. closure, a complete financial O.C. closure, or an O.S.
-- technical closure.  No operational row is changed by this migration.
begin;

alter table public.erp_purchase_orders
    drop constraint if exists erp_purchase_orders_status_check;
alter table public.erp_purchase_orders
    add constraint erp_purchase_orders_status_check
    check (status = any (array[
        'RASCUNHO', 'EMITIDA', 'PARCIALMENTE_RECEBIDA', 'RECEBIDA',
        'CONCLUIDA', 'CANCELADA', 'ENCERRADA_COM_SALDO'
    ]));

alter table public.erp_work_orders
    add column if not exists technical_previous_status text;

alter table public.erp_work_orders
    drop constraint if exists erp_work_orders_status_check;
alter table public.erp_work_orders
    add constraint erp_work_orders_status_check
    check (status = any (array[
        'RASCUNHO', 'AGUARDANDO_O_S', 'ATIVA', 'EM_PRODUÇÃO',
        'FINALIZADA', 'ENTREGUE', 'RETIRADA', 'CONCLUIDA',
        'CANCELADA', 'ARQUIVADA'
    ]));

comment on column public.erp_work_orders.technical_previous_status is
    'Estado canônico da O.S. antes da conclusão técnica; usado apenas para reabertura auditável.';

commit;
