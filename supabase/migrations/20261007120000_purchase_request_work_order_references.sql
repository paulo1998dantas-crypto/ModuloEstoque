-- Structured sector and work-order references for purchasing requests.
-- Legacy free text is preserved; only exact, unique O.S./item tokens are linked.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';

alter table public.erp_purchase_requests
    add column if not exists sector varchar(20) not null default 'GERAL';
alter table public.erp_purchase_requests
    drop constraint if exists erp_purchase_requests_sector_check;
alter table public.erp_purchase_requests
    add constraint erp_purchase_requests_sector_check
    check (sector in ('PRODUÇÃO','ADMINISTRATIVO','GERAL'));

create table if not exists public.erp_purchase_request_work_orders (
    request_id varchar(36) not null references public.erp_purchase_requests(id) on delete restrict,
    work_order_id uuid not null references public.erp_work_orders(id) on delete restrict,
    source varchar(20) not null default 'USER' check (source in ('USER','LEGACY_BACKFILL')),
    linked_at timestamptz not null default now(),
    linked_by integer null references public.users(id) on delete set null,
    primary key (request_id, work_order_id)
);
create index if not exists erp_purchase_request_work_orders_work_order
    on public.erp_purchase_request_work_orders(work_order_id, request_id);
create index if not exists erp_purchase_requests_pending_material_sector
    on public.erp_purchase_requests(sku_id, sector)
    where status in ('SOLICITADA','EM_COMPRAS');

create table if not exists public.erp_purchase_request_reference_backfill (
    request_id varchar(36) primary key references public.erp_purchase_requests(id) on delete restrict,
    original_reference varchar(255) not null default '',
    result varchar(24) not null check (result in (
        'VINCULADA','VINCULADA_PARCIAL','AMBIGUA','SEM_MATCH','SEM_REFERENCIA','REVISADA_MANUALMENTE')),
    linked_work_orders integer not null default 0,
    unresolved_tokens text[] not null default '{}',
    resolved_by_id integer null references public.users(id) on delete set null,
    resolved_by varchar(80) null,
    reviewed_at timestamptz not null default now()
);

-- Keep reruns from overwriting references already reviewed by a user.
create temporary table purchase_request_legacy_references on commit drop as
select r.id as request_id, coalesce(r.reference,'') as reference, r.origin
  from public.erp_purchase_requests r
 where not exists (select 1 from public.erp_purchase_request_reference_backfill b
                    where b.request_id = r.id);

create temporary table purchase_request_reference_tokens on commit drop as
with reference_parts as (
    -- Explicit O.S./ITEM prefixes may introduce a list, e.g. O.S. 3185 / 3186.
    -- An O.C. number embedded in prose must never become an O.S. reference.
    select r.request_id, upper(part.captures[1]) as reference_part
      from purchase_request_legacy_references r
      cross join lateral regexp_matches(r.reference,
          '\m(?:O[.[:space:]]*S(?:[.]?S)?|ITEM)[.[:space:]:#;-]*((?:[A-Z]{1,8}[[:space:]-]?[0-9]{3,}|[0-9]{4,})(?:[[:space:]]*(?:[,;/+]|E)[[:space:]]*(?:[A-Z]{1,8}[[:space:]-]?[0-9]{3,}|[0-9]{4,}))*)\M', 'gi'
      ) as part(captures)
    union
    select r.request_id, upper(r.reference)
      from purchase_request_legacy_references r
     where r.reference ~* '^[[:space:]]*(?:[A-Z]{1,8}[[:space:]-]?[0-9]{3,}|[0-9]{4,})(?:[[:space:]]*(?:[,;/+]|E)[[:space:]]*(?:[A-Z]{1,8}[[:space:]-]?[0-9]{3,}|[0-9]{4,}))*[[:space:]]*$'
       and r.reference !~* '^[[:space:]]*(?:O[.[:space:]]*S(?:[.]?S)?|ITEM|O[.[:space:]]*C)[.[:space:]:#;-]*[A-Z0-9]'
), token_rows as (
    select distinct r.request_id, upper(token.captures[1]) as token
      from reference_parts r
      cross join lateral regexp_matches(regexp_replace(r.reference_part, '\mE\M', '/', 'g'),
          '([A-Z]{1,8}[[:space:]-]?[0-9]{3,}|[0-9]{4,})', 'gi') as token(captures)
), candidates as (
    select distinct t.request_id, t.token, w.id as work_order_id
      from token_rows t
      join public.erp_work_orders w
        on regexp_replace(upper(coalesce(w.numero_os,'')), '[^A-Z0-9]', '', 'g') =
           regexp_replace(t.token, '[^A-Z0-9]', '', 'g')
        or (t.token ~ '^[0-9]{4,}$' and exists (
            select 1 from public.erp_vehicle_entries e
             where e.id = w.vehicle_entry_id
               and regexp_replace(e.item_number::text, '[^A-Z0-9]', '', 'g') = t.token
        ))
), token_resolution as (
    select t.request_id, t.token, count(distinct c.work_order_id) as matches,
           min(c.work_order_id::text)::uuid as unique_work_order_id
      from token_rows t
      left join candidates c on c.request_id=t.request_id and c.token=t.token
     group by t.request_id, t.token
)
select * from token_resolution;

with resolved as (
    select distinct request_id, unique_work_order_id as work_order_id
      from purchase_request_reference_tokens
     where matches = 1
)
insert into public.erp_purchase_request_work_orders
    (request_id, work_order_id, source, linked_at, linked_by)
select request_id, work_order_id, 'LEGACY_BACKFILL', now(), null
  from resolved
on conflict (request_id, work_order_id) do nothing;

update public.erp_purchase_requests r
   set sector = case
       when coalesce(r.reference,'') ~* '(^|[^[:alnum:]])ADMINISTRATIVO([^[:alnum:]]|$)' then 'ADMINISTRATIVO'
       when coalesce(r.reference,'') ~* '(^|[^[:alnum:]])GERAL([^[:alnum:]]|$)' then 'GERAL'
       when coalesce(r.reference,'') ~* '(^|[^[:alnum:]])PRODU[CÇ][AÃ]O([^[:alnum:]]|$)' then 'PRODUÇÃO'
       when exists (select 1 from public.erp_purchase_request_work_orders l where l.request_id = r.id) then 'PRODUÇÃO'
       when r.origin = 'PCP' then 'PRODUÇÃO'
       else 'GERAL'
   end
 where exists (select 1 from purchase_request_legacy_references old where old.request_id=r.id);

with link_counts as (
    select r.id as request_id, coalesce(count(l.work_order_id),0)::integer as linked
      from public.erp_purchase_requests r
      left join public.erp_purchase_request_work_orders l on l.request_id = r.id
     where exists (select 1 from purchase_request_legacy_references old where old.request_id=r.id)
     group by r.id
), unresolved as (
    select request_id, array_agg(token order by token) as tokens
      from purchase_request_reference_tokens
     where matches <> 1
     group by request_id
), ambiguous_requests as (
    select distinct request_id from purchase_request_reference_tokens where matches > 1
), report_rows as (
    select r.id as request_id, coalesce(r.reference,'') as original_reference,
           coalesce(l.linked,0) as linked,
           coalesce(u.tokens,'{}'::text[]) as unresolved_tokens,
           case
             when coalesce(l.linked,0) > 0 and cardinality(coalesce(u.tokens,'{}'::text[])) > 0 then 'VINCULADA_PARCIAL'
             when coalesce(l.linked,0) > 0 then 'VINCULADA'
             when ar.request_id is not null then 'AMBIGUA'
             when cardinality(coalesce(u.tokens,'{}'::text[])) > 0 then 'SEM_MATCH'
             when nullif(trim(coalesce(r.reference,'')),'') is null then 'SEM_REFERENCIA'
             when trim(coalesce(r.reference,'')) ~* '^(PRODU[CÇ][AÃ]O|ADMINISTRATIVO|GERAL|ALMOXARIFADO|[-–—]+)$' then 'SEM_REFERENCIA'
             else 'SEM_MATCH'
           end as result
      from public.erp_purchase_requests r
      left join link_counts l on l.request_id = r.id
      left join unresolved u on u.request_id = r.id
      left join ambiguous_requests ar on ar.request_id = r.id
     where exists (select 1 from purchase_request_legacy_references old where old.request_id=r.id)
)
insert into public.erp_purchase_request_reference_backfill
    (request_id, original_reference, result, linked_work_orders, unresolved_tokens)
select request_id, original_reference, result, linked, unresolved_tokens
  from report_rows
on conflict (request_id) do nothing;

-- A pending need can use the single open replacement of a cancelled O.S.
-- Never guess when two open candidates exist, and never rewrite manual reviews.
with candidate_links as (
    select b.request_id, token.value as token, w.id as work_order_id
      from public.erp_purchase_request_reference_backfill b
      join public.erp_purchase_requests r on r.id=b.request_id
      cross join lateral unnest(b.unresolved_tokens) token(value)
      join public.erp_work_orders w
        on regexp_replace(upper(coalesce(w.numero_os,'')), '[^A-Z0-9]', '', 'g') =
           regexp_replace(token.value, '[^A-Z0-9]', '', 'g')
        or (token.value ~ '^[0-9]{4,}$' and exists (
            select 1 from public.erp_vehicle_entries e
             where e.id=w.vehicle_entry_id and e.item_number::text=token.value))
     where b.result='AMBIGUA' and r.status in ('SOLICITADA','EM_COMPRAS')
       and w.status in ('ATIVA','EM_PRODUÇÃO','EM_PRODUCAO')
       and coalesce(w.technical_status,'ABERTA')='ABERTA'
), unique_tokens as (
    select request_id, token, min(work_order_id::text)::uuid as work_order_id
      from candidate_links group by request_id, token
    having count(distinct work_order_id)=1
), safe_requests as (
    select b.request_id from public.erp_purchase_request_reference_backfill b
      join unique_tokens u on u.request_id=b.request_id
     where b.result='AMBIGUA'
     group by b.request_id,b.unresolved_tokens
    having count(*)=cardinality(b.unresolved_tokens)
), inserted as (
    insert into public.erp_purchase_request_work_orders
        (request_id,work_order_id,source,linked_at,linked_by)
    select distinct u.request_id,u.work_order_id,'LEGACY_BACKFILL',now(),null::integer
      from unique_tokens u join safe_requests s on s.request_id=u.request_id
    on conflict (request_id,work_order_id) do nothing
    returning request_id
)
update public.erp_purchase_request_reference_backfill b
   set result='VINCULADA',unresolved_tokens='{}',reviewed_at=now(),
       linked_work_orders=(select count(*) from public.erp_purchase_request_work_orders l
                            where l.request_id=b.request_id)
                         +(select count(*) from inserted i where i.request_id=b.request_id)
 where exists(select 1 from safe_requests s where s.request_id=b.request_id);

alter table public.erp_purchase_request_work_orders enable row level security;
alter table public.erp_purchase_request_reference_backfill enable row level security;
revoke all on public.erp_purchase_request_work_orders,
    public.erp_purchase_request_reference_backfill from public;
do $$
declare role_name text;
begin
    foreach role_name in array array['anon','authenticated'] loop
        if exists(select 1 from pg_roles where rolname=role_name) then
            execute format('revoke all on public.erp_purchase_request_work_orders, public.erp_purchase_request_reference_backfill from %I', role_name);
        end if;
    end loop;
    if exists(select 1 from pg_roles where rolname='service_role') then
        grant select,insert,delete on public.erp_purchase_request_work_orders to service_role;
        grant select,update on public.erp_purchase_request_reference_backfill to service_role;
    end if;
end;
$$;
commit;
