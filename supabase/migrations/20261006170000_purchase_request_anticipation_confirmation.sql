-- Persist the supplier-negotiated delivery date when the buyer closes an anticipation.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

alter table public.erp_purchase_requests
    add column if not exists anticipation_confirmed_delivery_date date null;

commit;
