-- Additive workflow. Apply BEFORE deploying Estoque, then Suprimentos.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';
create table if not exists public.erp_purchase_requests (
 id varchar(36) primary key,
 origin varchar(20) not null check (origin in ('ESTOQUE','PCP')),
 sku_id integer not null references public.skus(id) on delete restrict,
 sku_codigo varchar(80) not null, descricao varchar(255) not null, unidade varchar(20) not null,
 quantity numeric(14,3) not null check (quantity > 0),
 needed_at date not null, reference varchar(255) not null default '', notes varchar(4000) not null default '',
 status varchar(20) not null check (status in ('SOLICITADA','EM_COMPRAS','CONCLUIDA','CANCELADA')),
 requested_by_id integer not null references public.users(id) on delete restrict,
 requested_by varchar(80) not null, created_at timestamptz not null, updated_at timestamptz not null,
 buyer_id integer references public.users(id) on delete restrict, buyer varchar(80),
 purchase_order_id uuid references public.erp_purchase_orders(id) on delete restrict,
 completed_at timestamptz, completed_by varchar(80), version integer not null default 1,
 idempotency_key varchar(80) not null,
 unique(requested_by_id,idempotency_key),
 check ((status = 'CONCLUIDA') = (purchase_order_id is not null and completed_at is not null and completed_by is not null))
);
create index if not exists erp_purchase_requests_status_needed on public.erp_purchase_requests(status,needed_at);
create index if not exists erp_purchase_requests_purchase_order on public.erp_purchase_requests(purchase_order_id);
create index if not exists erp_purchase_requests_created on public.erp_purchase_requests(created_at desc);
create table if not exists public.erp_purchase_request_events (
 id varchar(36) primary key, request_id varchar(36) not null references public.erp_purchase_requests(id) on delete restrict,
 action varchar(40) not null, actor_id integer not null references public.users(id) on delete restrict,
 actor varchar(80) not null, created_at timestamptz not null,
 before_data jsonb, after_data jsonb, reason varchar(4000) not null default ''
);
create index if not exists erp_purchase_request_events_timeline on public.erp_purchase_request_events(request_id,created_at);
create or replace function public.erp_preserve_purchase_request_history() returns trigger
language plpgsql as $$
begin
 raise exception 'Histórico de solicitações é imutável; registre um novo evento.';
end;
$$;
drop trigger if exists preserve_purchase_request_events on public.erp_purchase_request_events;
create trigger preserve_purchase_request_events before update or delete on public.erp_purchase_request_events
for each row execute function public.erp_preserve_purchase_request_history();
alter table public.erp_purchase_requests enable row level security;
alter table public.erp_purchase_request_events enable row level security;
-- These operational tables are accessed only by authenticated backend DB services.
revoke all on public.erp_purchase_requests, public.erp_purchase_request_events from public;
do $$
declare role_name text;
begin
 foreach role_name in array array['anon','authenticated'] loop
  if exists(select 1 from pg_roles where rolname=role_name) then
   execute format('revoke all on public.erp_purchase_requests, public.erp_purchase_request_events from %I',role_name);
  end if;
 end loop;
 if exists(select 1 from pg_roles where rolname='service_role') then
  grant select,insert,update on public.erp_purchase_requests to service_role;
  grant select,insert on public.erp_purchase_request_events to service_role;
 end if;
end;
$$;
commit;

