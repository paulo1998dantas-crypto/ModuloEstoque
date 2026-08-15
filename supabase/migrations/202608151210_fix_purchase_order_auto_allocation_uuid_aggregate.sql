-- Corrige a seleção do candidato único sem aplicar min(uuid), que não é
-- suportado de forma uniforme pelas versões do PostgreSQL.
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
        concat_ws(' ',current_order.allocation_reference,current_order.destino)
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
      into match_count,matched_work_order
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
end
$$;

revoke all on function public.erp_try_auto_allocate_purchase_order(uuid,text,text)
    from public, anon, authenticated;
grant execute on function public.erp_try_auto_allocate_purchase_order(uuid,text,text)
    to service_role;
