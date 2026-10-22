-- Additive: apply before publishing Estoque, then Suprimentos.
-- Version synchronized with the production migration history after MCP application.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';
lock table public.erp_purchase_requests in share row exclusive mode;
create table public.erp_purchase_request_orders (
 request_id varchar(36) not null references public.erp_purchase_requests(id) on delete restrict,
 purchase_order_id uuid not null references public.erp_purchase_orders(id) on delete restrict,
 quantity numeric(14,3) not null check (quantity > 0),
 status varchar(20) not null check (status in ('ATIVA','CANCELADA')),
 linked_at timestamptz not null,
 linked_by varchar(80) not null,
 primary key (request_id,purchase_order_id)
);
create index erp_purchase_request_orders_order on public.erp_purchase_request_orders(purchase_order_id,status);
-- Existing completion remains exactly as recorded; no changes to requests or O.C.s.
insert into public.erp_purchase_request_orders(request_id,purchase_order_id,quantity,status,linked_at,linked_by)
 select id,purchase_order_id,quantity,'ATIVA',completed_at,completed_by
 from public.erp_purchase_requests where status='CONCLUIDA' and purchase_order_id is not null;
do $$
begin
 if exists (
  select 1 from public.erp_purchase_requests r
  left join public.erp_purchase_request_orders a
   on a.request_id=r.id and a.purchase_order_id=r.purchase_order_id
  where r.status='CONCLUIDA' and
   (a.request_id is null or a.quantity<>r.quantity or a.linked_at<>r.completed_at or a.linked_by<>r.completed_by)
 ) then
  raise exception 'Falha na preservação dos vínculos históricos de solicitações.';
 end if;
end;
$$;
alter table public.erp_purchase_request_orders enable row level security;
revoke all on public.erp_purchase_request_orders from public;
do $$
declare role_name text;
begin
 foreach role_name in array array['anon','authenticated'] loop
  if exists(select 1 from pg_roles where rolname=role_name) then
   execute format('revoke all on public.erp_purchase_request_orders from %I',role_name);
  end if;
 end loop;
 if exists(select 1 from pg_roles where rolname='service_role') then
  grant select,insert,update,delete on public.erp_purchase_request_orders to service_role;
 end if;
end;
$$;
commit;
