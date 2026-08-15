-- Vínculo operacional auditável entre O.C. e O.S./veículo.
-- Migração exclusivamente aditiva: não altera saldos, recebimentos ou movimentos.

alter table public.erp_purchase_orders
    add column if not exists allocation_mode text not null default 'ESTOQUE',
    add column if not exists work_order_id uuid null,
    add column if not exists allocation_reference text not null default '',
    add column if not exists allocation_updated_at timestamptz null,
    add column if not exists allocation_updated_by text null;

do $$
begin
    if not exists (
        select 1 from pg_constraint
         where conrelid='public.erp_purchase_orders'::regclass
           and conname='erp_purchase_orders_allocation_mode_check'
    ) then
        alter table public.erp_purchase_orders
            add constraint erp_purchase_orders_allocation_mode_check
            check (allocation_mode in ('ESTOQUE','WORK_ORDER','AG_CHEGADA'));
    end if;
    if not exists (
        select 1 from pg_constraint
         where conrelid='public.erp_purchase_orders'::regclass
           and conname='erp_purchase_orders_work_order_id_fk'
    ) then
        alter table public.erp_purchase_orders
            add constraint erp_purchase_orders_work_order_id_fk
            foreign key (work_order_id) references public.erp_work_orders(id)
            on update cascade on delete restrict;
    end if;
end $$;

create index if not exists erp_purchase_orders_work_order_id_idx
    on public.erp_purchase_orders(work_order_id)
    where work_order_id is not null;
create index if not exists erp_purchase_orders_unresolved_ag_chegada_idx
    on public.erp_purchase_orders(updated_at)
    where allocation_mode='AG_CHEGADA' and work_order_id is null;

create table if not exists public.erp_purchase_order_allocation_events (
    id uuid primary key default gen_random_uuid(),
    purchase_order_id uuid not null references public.erp_purchase_orders(id) on delete cascade,
    from_mode text null,
    to_mode text not null,
    from_work_order_id uuid null references public.erp_work_orders(id) on delete restrict,
    to_work_order_id uuid null references public.erp_work_orders(id) on delete restrict,
    reference_text text not null default '',
    action text not null,
    actor text not null,
    origin text not null default 'ERP',
    reason text not null default '',
    created_at timestamptz not null default now(),
    constraint erp_po_allocation_events_to_mode_check
        check (to_mode in ('ESTOQUE','WORK_ORDER','AG_CHEGADA'))
);

create index if not exists erp_po_allocation_events_order_created_idx
    on public.erp_purchase_order_allocation_events(purchase_order_id,created_at desc);
create index if not exists erp_po_allocation_events_work_created_idx
    on public.erp_purchase_order_allocation_events(to_work_order_id,created_at desc)
    where to_work_order_id is not null;

create table if not exists public.erp_work_order_notes (
    id uuid primary key default gen_random_uuid(),
    work_order_id uuid not null references public.erp_work_orders(id) on delete cascade,
    note text not null,
    actor text not null,
    origin text not null default 'MES',
    created_at timestamptz not null default now(),
    constraint erp_work_order_notes_note_check check (length(trim(note)) between 1 and 4000)
);

create index if not exists erp_work_order_notes_work_created_idx
    on public.erp_work_order_notes(work_order_id,created_at desc);

alter table public.erp_purchase_order_allocation_events enable row level security;
alter table public.erp_work_order_notes enable row level security;
revoke all on public.erp_purchase_order_allocation_events from public, anon, authenticated;
revoke all on public.erp_work_order_notes from public, anon, authenticated;
grant select,insert,update,delete on public.erp_purchase_order_allocation_events to service_role;
grant select,insert,update,delete on public.erp_work_order_notes to service_role;

create or replace function public.erp_reference_token(value text)
returns text
language sql
immutable
parallel safe
set search_path = ''
as $$
    select regexp_replace(upper(coalesce(value,'')), '[^A-Z0-9]', '', 'g')
$$;

create or replace function public.erp_try_auto_allocate_purchase_order(
    p_purchase_order_id uuid,
    p_actor text default 'sistema:conciliacao',
    p_origin text default 'ERP'
)
returns uuid
language plpgsql
security definer
set search_path=public
as $$
declare
    current_order public.erp_purchase_orders%rowtype;
    reference_token text;
    matched_work_order uuid;
    match_count integer;
begin
    select * into current_order
      from public.erp_purchase_orders
     where id=p_purchase_order_id
     for update;
    if not found
       or current_order.allocation_mode<>'AG_CHEGADA'
       or current_order.work_order_id is not null then
        return current_order.work_order_id;
    end if;

    reference_token := public.erp_reference_token(
        concat_ws(' ', current_order.allocation_reference, current_order.destino)
    );
    if length(reference_token)<3 then
        return null;
    end if;

    with candidates as (
        select distinct w.id
          from public.erp_work_orders w
          join public.erp_vehicle_entries e on e.id=w.vehicle_entry_id
          join public.erp_vehicles v on v.id=e.vehicle_id
         where w.is_current=true
           and coalesce(w.technical_status,'ABERTA')='ABERTA'
           and w.status in ('RASCUNHO','AGUARDANDO_O_S','ATIVA','EM_PRODUÇÃO')
           and (
                (length(public.erp_reference_token(w.numero_os))>=3
                 and position(public.erp_reference_token(w.numero_os) in reference_token)>0)
             or (length(public.erp_reference_token(e.item_number::text))>=3
                 and position(public.erp_reference_token(e.item_number::text) in reference_token)>0)
             or (length(public.erp_reference_token(w.proposta_numero))>=3
                 and position(public.erp_reference_token(w.proposta_numero) in reference_token)>0)
             or (length(public.erp_reference_token(v.chassi))>=6
                 and position(public.erp_reference_token(v.chassi) in reference_token)>0)
             or (length(public.erp_reference_token(right(v.chassi,8)))>=6
                 and position(public.erp_reference_token(right(v.chassi,8)) in reference_token)>0)
           )
    )
    select count(*), (array_agg(id order by id::text))[1]
      into match_count, matched_work_order
      from candidates;

    if match_count<>1 then
        return null;
    end if;

    update public.erp_purchase_orders
       set allocation_mode='WORK_ORDER',
           work_order_id=matched_work_order,
           allocation_updated_at=now(),
           allocation_updated_by=coalesce(nullif(trim(p_actor),''),'sistema:conciliacao'),
           version=version+1,
           updated_at=now()
     where id=p_purchase_order_id;
    update public.erp_purchase_order_lines
       set work_order_id=matched_work_order
     where purchase_order_id=p_purchase_order_id;
    insert into public.erp_purchase_order_allocation_events(
        purchase_order_id,from_mode,to_mode,from_work_order_id,to_work_order_id,
        reference_text,action,actor,origin,reason
    ) values (
        p_purchase_order_id,'AG_CHEGADA','WORK_ORDER',null,matched_work_order,
        concat_ws(' ',current_order.allocation_reference,current_order.destino),
        'AUTO_LINK',coalesce(nullif(trim(p_actor),''),'sistema:conciliacao'),
        coalesce(nullif(trim(p_origin),''),'ERP'),
        'Correspondência única por O.S., ITEM, proposta ou chassi.'
    );
    return matched_work_order;
end $$;

create or replace function public.erp_reconcile_ag_chegada_allocations(
    p_actor text default 'sistema:conciliacao',
    p_origin text default 'ERP'
)
returns integer
language plpgsql
security definer
set search_path=public
as $$
declare
    candidate record;
    linked uuid;
    reconciled integer := 0;
begin
    for candidate in
        select id from public.erp_purchase_orders
         where allocation_mode='AG_CHEGADA' and work_order_id is null
         order by updated_at,id
    loop
        linked := public.erp_try_auto_allocate_purchase_order(candidate.id,p_actor,p_origin);
        if linked is not null then reconciled := reconciled+1; end if;
    end loop;
    return reconciled;
end $$;

revoke all on function public.erp_reference_token(text) from public, anon, authenticated;
revoke all on function public.erp_try_auto_allocate_purchase_order(uuid,text,text) from public, anon, authenticated;
revoke all on function public.erp_reconcile_ag_chegada_allocations(text,text) from public, anon, authenticated;
grant execute on function public.erp_reference_token(text) to service_role;
grant execute on function public.erp_try_auto_allocate_purchase_order(uuid,text,text) to service_role;
grant execute on function public.erp_reconcile_ag_chegada_allocations(text,text) to service_role;

comment on column public.erp_purchase_orders.allocation_mode is
    'ESTOQUE, WORK_ORDER ou AG_CHEGADA. Não altera saldo; classifica o destino operacional da compra.';
comment on table public.erp_purchase_order_allocation_events is
    'Histórico imutável de vínculo, desvínculo, realocação e conciliação automática entre O.C. e O.S.';
comment on table public.erp_work_order_notes is
    'Observações cronológicas manuais exibidas no card MES.';
