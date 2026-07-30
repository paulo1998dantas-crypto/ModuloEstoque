-- Índices de apoio às foreign keys do domínio ERP.
-- São aditivos e não alteram saldos, movimentos ou dados operacionais.
begin;

set local lock_timeout = 5000;
set local statement_timeout = 60000;

create index if not exists erp_goods_receipt_lines_po_line_idx
    on public.erp_goods_receipt_lines(purchase_order_line_id);

create index if not exists erp_goods_receipts_po_idx
    on public.erp_goods_receipts(purchase_order_id);

create index if not exists erp_stock_receipt_links_receipt_line_idx
    on public.erp_stock_receipt_links(goods_receipt_line_id);

create index if not exists erp_vehicle_entries_vehicle_idx
    on public.erp_vehicle_entries(vehicle_id);

create index if not exists erp_work_order_schedules_order_idx
    on public.erp_work_order_schedules(work_order_id);

create index if not exists erp_work_order_stage_events_stage_idx
    on public.erp_work_order_stage_events(work_order_stage_id);

create index if not exists erp_work_order_status_history_order_idx
    on public.erp_work_order_status_history(work_order_id);

commit;
