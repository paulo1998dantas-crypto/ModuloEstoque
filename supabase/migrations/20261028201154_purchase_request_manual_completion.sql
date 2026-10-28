-- Manual completion is a workflow decision, never a stock receipt or invented O.C.
alter table public.erp_purchase_requests
  add column completion_mode varchar(20) not null default 'PEDIDO',
  add column completion_reason varchar(4000),
  add column completion_evidence varchar(4000);

alter table public.erp_purchase_requests
  drop constraint erp_purchase_requests_check,
  add constraint erp_purchase_requests_completion_mode_check
    check (completion_mode in ('PEDIDO','MANUAL')),
  add constraint erp_purchase_requests_check
    check ((status = 'CONCLUIDA') = (
      completed_at is not null and completed_by is not null and (
        (completion_mode = 'PEDIDO' and purchase_order_id is not null)
        or (completion_mode = 'MANUAL'
          and length(trim(coalesce(completion_reason,''))) >= 10
          and length(trim(coalesce(completion_evidence,''))) >= 10)
      )
    ));

comment on column public.erp_purchase_requests.completion_mode is
  'PEDIDO: conclusão por alocação. MANUAL: atendimento confirmado por comprador/ADMIN com motivo e evidência auditados; não movimenta estoque.';
comment on column public.erp_purchase_requests.completion_evidence is
  'Evidência de atendimento da solicitação. Preservada no histórico em CONCLUIR_MANUAL e REABRIR.';
