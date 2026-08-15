-- Harden the allocation helper and cover both sides of historical foreign keys.
-- Additive only: no operational records, receipts, movements or balances change.

create or replace function public.erp_reference_token(value text)
returns text
language sql
immutable
parallel safe
set search_path = ''
as $$
    select regexp_replace(upper(coalesce(value,'')), '[^A-Z0-9]', '', 'g')
$$;

create index if not exists erp_po_allocation_events_from_work_idx
    on public.erp_purchase_order_allocation_events(from_work_order_id)
    where from_work_order_id is not null;

create index if not exists erp_po_allocation_events_from_entry_idx
    on public.erp_purchase_order_allocation_events(from_vehicle_entry_id)
    where from_vehicle_entry_id is not null;

comment on function public.erp_reference_token(text) is
    'Normaliza referências para conciliação determinística de O.C. por O.S., ITEM, proposta ou chassi.';
