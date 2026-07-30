-- Guarded rollback for migration 202607301200.
--
-- Run only if the integration must be reverted before these reconciled orders
-- receive any operational transaction.  The guard aborts instead of deleting
-- an order that already has a receipt or financial entry.

begin;
set local lock_timeout = '5s';
set local statement_timeout = '2min';

do $$
declare
    used_orders integer;
begin
    select count(distinct purchase_order.id)
      into used_orders
      from public.erp_purchase_orders purchase_order
     where purchase_order.idempotency_key like 'suprimentos-oc:%'
       and (
           exists (
               select 1
                 from public.erp_goods_receipts receipt
                where receipt.purchase_order_id = purchase_order.id
           )
           or exists (
               select 1
                 from public.erp_purchase_order_financial_entries financial
                where financial.purchase_order_id = purchase_order.id
           )
       );

    if used_orders > 0 then
        raise exception
            'Rollback blocked: % reconciled order(s) already have operational usage.',
            used_orders;
    end if;
end
$$;

update public.suprimentos_documentos document
   set erp_purchase_order_id = null
  from public.erp_purchase_orders purchase_order
 where document.erp_purchase_order_id = purchase_order.id
   and purchase_order.idempotency_key = 'suprimentos-oc:' || document.id::text;

delete from public.erp_audit_events audit
using public.erp_purchase_orders purchase_order
where audit.entity_type = 'PURCHASE_ORDER'
  and audit.entity_id = purchase_order.id
  and audit.action = 'RECONCILIADA_ORIGEM_SUPRIMENTOS'
  and purchase_order.idempotency_key like 'suprimentos-oc:%';

delete from public.erp_purchase_order_lines line
using public.erp_purchase_orders purchase_order
where line.purchase_order_id = purchase_order.id
  and purchase_order.idempotency_key like 'suprimentos-oc:%';

delete from public.erp_purchase_orders
where idempotency_key like 'suprimentos-oc:%';

commit;
