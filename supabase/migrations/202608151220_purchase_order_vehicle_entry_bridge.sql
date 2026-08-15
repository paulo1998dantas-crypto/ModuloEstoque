-- Ponte entre uma O.C. AG CHEGADA e a entrada/ITEM antes da abertura da O.S.
-- Aditiva: não altera recebimentos, movimentos ou saldos.

alter table public.erp_purchase_orders
    add column if not exists vehicle_entry_id uuid null;

do $$
begin
    if not exists (
        select 1 from pg_constraint
         where conrelid='public.erp_purchase_orders'::regclass
           and conname='erp_purchase_orders_vehicle_entry_id_fk'
    ) then
        alter table public.erp_purchase_orders
            add constraint erp_purchase_orders_vehicle_entry_id_fk
            foreign key (vehicle_entry_id) references public.erp_vehicle_entries(id)
            on update cascade on delete restrict;
    end if;
end $$;

create index if not exists erp_purchase_orders_vehicle_entry_id_idx
    on public.erp_purchase_orders(vehicle_entry_id)
    where vehicle_entry_id is not null;

alter table public.erp_purchase_order_allocation_events
    add column if not exists from_vehicle_entry_id uuid null,
    add column if not exists to_vehicle_entry_id uuid null;

do $$
begin
    if not exists (
        select 1 from pg_constraint
         where conrelid='public.erp_purchase_order_allocation_events'::regclass
           and conname='erp_po_allocation_events_from_entry_fk'
    ) then
        alter table public.erp_purchase_order_allocation_events
            add constraint erp_po_allocation_events_from_entry_fk
            foreign key (from_vehicle_entry_id) references public.erp_vehicle_entries(id)
            on delete restrict;
    end if;
    if not exists (
        select 1 from pg_constraint
         where conrelid='public.erp_purchase_order_allocation_events'::regclass
           and conname='erp_po_allocation_events_to_entry_fk'
    ) then
        alter table public.erp_purchase_order_allocation_events
            add constraint erp_po_allocation_events_to_entry_fk
            foreign key (to_vehicle_entry_id) references public.erp_vehicle_entries(id)
            on delete restrict;
    end if;
end $$;

create index if not exists erp_po_allocation_events_entry_created_idx
    on public.erp_purchase_order_allocation_events(to_vehicle_entry_id,created_at desc)
    where to_vehicle_entry_id is not null;

create table if not exists public.erp_vehicle_entry_notes (
    id uuid primary key default gen_random_uuid(),
    vehicle_entry_id uuid not null references public.erp_vehicle_entries(id) on delete cascade,
    note text not null,
    actor text not null,
    origin text not null default 'MES',
    created_at timestamptz not null default now(),
    constraint erp_vehicle_entry_notes_note_check
        check (length(trim(note)) between 1 and 4000)
);

create index if not exists erp_vehicle_entry_notes_entry_created_idx
    on public.erp_vehicle_entry_notes(vehicle_entry_id,created_at desc);

alter table public.erp_vehicle_entry_notes enable row level security;
revoke all on public.erp_vehicle_entry_notes from public, anon, authenticated;
grant select,insert,update,delete on public.erp_vehicle_entry_notes to service_role;

create or replace function public.erp_try_auto_allocate_purchase_order(
    p_purchase_order_id uuid,
    p_actor text default 'sistema:conciliacao',
    p_origin text default 'ERP'
) returns uuid
language plpgsql
security definer
set search_path=public
as $$
declare
    current_order public.erp_purchase_orders%rowtype;
    reference_token text;
    matched_work_order uuid;
    matched_entry uuid;
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

    -- Se a chegada já foi reconhecida anteriormente, a abertura posterior da
    -- O.S. promove o vínculo pelo ITEM, sem depender novamente do texto livre.
    if current_order.vehicle_entry_id is not null then
        select w.id into matched_work_order
          from public.erp_work_orders w
         where w.vehicle_entry_id=current_order.vehicle_entry_id
           and w.is_current=true
           and coalesce(w.technical_status,'ABERTA')='ABERTA'
           and w.status in ('RASCUNHO','AGUARDANDO_O_S','ATIVA','EM_PRODUÇÃO')
         order by w.revision_number desc,w.created_at desc
         limit 1;
    end if;

    reference_token := public.erp_reference_token(
        concat_ws(' ',current_order.allocation_reference,current_order.destino)
    );
    if length(reference_token)<3 then
        return null;
    end if;

    if matched_work_order is null then
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
        select count(*),(array_agg(id order by id::text))[1]
          into match_count,matched_work_order
          from candidates;
        if match_count<>1 then matched_work_order:=null; end if;
    end if;

    if matched_work_order is not null then
        select vehicle_entry_id into matched_entry
          from public.erp_work_orders where id=matched_work_order;
        update public.erp_purchase_orders
           set allocation_mode='WORK_ORDER',work_order_id=matched_work_order,
               vehicle_entry_id=matched_entry,allocation_updated_at=now(),
               allocation_updated_by=coalesce(nullif(trim(p_actor),''),'sistema:conciliacao'),
               version=version+1,updated_at=now()
         where id=p_purchase_order_id;
        update public.erp_purchase_order_lines
           set work_order_id=matched_work_order
         where purchase_order_id=p_purchase_order_id;
        insert into public.erp_purchase_order_allocation_events(
            purchase_order_id,from_mode,to_mode,from_work_order_id,to_work_order_id,
            from_vehicle_entry_id,to_vehicle_entry_id,reference_text,action,actor,origin,reason
        ) values (
            p_purchase_order_id,'AG_CHEGADA','WORK_ORDER',null,matched_work_order,
            current_order.vehicle_entry_id,matched_entry,
            concat_ws(' ',current_order.allocation_reference,current_order.destino),
            'AUTO_LINK',coalesce(nullif(trim(p_actor),''),'sistema:conciliacao'),
            coalesce(nullif(trim(p_origin),''),'ERP'),
            'Correspondência única por O.S., ITEM, proposta ou chassi.'
        );
        return matched_work_order;
    end if;

    -- Sem O.S., reconhece a chegada pelo ITEM ou chassi e preserva AG CHEGADA.
    with entry_candidates as (
        select distinct e.id
          from public.erp_vehicle_entries e
          join public.erp_vehicles v on v.id=e.vehicle_id
         where coalesce(e.status,'AGUARDANDO_O_S')<>'RETIRADA'
           and (
                (length(public.erp_reference_token(e.item_number::text))>=3
                 and position(public.erp_reference_token(e.item_number::text) in reference_token)>0)
             or (length(public.erp_reference_token(v.chassi))>=6
                 and position(public.erp_reference_token(v.chassi) in reference_token)>0)
             or (length(public.erp_reference_token(right(v.chassi,8)))>=6
                 and position(public.erp_reference_token(right(v.chassi,8)) in reference_token)>0)
           )
    )
    select count(*),(array_agg(id order by id::text))[1]
      into match_count,matched_entry
      from entry_candidates;

    if match_count=1 and matched_entry is distinct from current_order.vehicle_entry_id then
        update public.erp_purchase_orders
           set vehicle_entry_id=matched_entry,allocation_updated_at=now(),
               allocation_updated_by=coalesce(nullif(trim(p_actor),''),'sistema:conciliacao'),
               version=version+1,updated_at=now()
         where id=p_purchase_order_id;
        insert into public.erp_purchase_order_allocation_events(
            purchase_order_id,from_mode,to_mode,from_work_order_id,to_work_order_id,
            from_vehicle_entry_id,to_vehicle_entry_id,reference_text,action,actor,origin,reason
        ) values (
            p_purchase_order_id,'AG_CHEGADA','AG_CHEGADA',null,null,
            current_order.vehicle_entry_id,matched_entry,
            concat_ws(' ',current_order.allocation_reference,current_order.destino),
            'AUTO_LINK_ENTRY',coalesce(nullif(trim(p_actor),''),'sistema:conciliacao'),
            coalesce(nullif(trim(p_origin),''),'ERP'),
            'Chegada reconhecida por ITEM ou chassi; aguardando abertura da O.S.'
        );
    end if;
    return null;
end
$$;

revoke all on function public.erp_try_auto_allocate_purchase_order(uuid,text,text)
    from public, anon, authenticated;
grant execute on function public.erp_try_auto_allocate_purchase_order(uuid,text,text)
    to service_role;

comment on column public.erp_purchase_orders.vehicle_entry_id is
    'Entrada/ITEM reconhecida antes da abertura da O.S.; promovida automaticamente para work_order_id.';
comment on table public.erp_vehicle_entry_notes is
    'Observações cronológicas no card de uma entrada que ainda não possui O.S.';
