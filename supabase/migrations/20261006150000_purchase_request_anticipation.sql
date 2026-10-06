-- Classify requests that can be fulfilled by expediting an existing open PO.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

alter table public.erp_purchase_requests
    add column if not exists request_type varchar(20) not null default 'COMPRA_NOVA';
alter table public.erp_purchase_requests
    add column if not exists anticipation_order_id uuid null
        references public.erp_purchase_orders(id) on delete restrict;

do $$
begin
    if not exists (
        select 1 from pg_constraint
         where conrelid = 'public.erp_purchase_requests'::regclass
           and conname = 'erp_purchase_requests_request_type_check'
    ) then
        alter table public.erp_purchase_requests
            add constraint erp_purchase_requests_request_type_check
            check (request_type in ('COMPRA_NOVA', 'ANTECIPACAO'));
    end if;
end;
$$;

create index if not exists erp_purchase_requests_type_created
    on public.erp_purchase_requests(request_type, created_at desc);
create index if not exists erp_purchase_requests_anticipation_order
    on public.erp_purchase_requests(anticipation_order_id);

commit;
