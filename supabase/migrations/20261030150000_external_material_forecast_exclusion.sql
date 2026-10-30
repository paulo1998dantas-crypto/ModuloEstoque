-- The BOM remains technical; external Spandy leaves are not internal demand.
-- Preserve the original recursion/cycle/depth rules for all other materials.
create or replace function public.suprimentos_recalcular_necessidades_forecast(p_forecast_id uuid)
returns void language plpgsql set search_path to '' as $function$
begin
    delete from public.suprimentos_forecast_necessidades
    where forecast_id=p_forecast_id;

    with recursive exploded as (
        select fi.id as forecast_item_id,fi.forecast_id,fi.sku_id as root_sku_id,
               fi.sku_id,fi.sku_codigo,fi.quantidade_planejada,0 as nivel,
               array[fi.sku_id]::integer[] as caminho_ids,
               array[fi.sku_codigo]::text[] as caminho_codigos
        from public.suprimentos_forecast_itens fi
        where fi.forecast_id=p_forecast_id
          and not exists (select 1 from public.erp_external_stock_items ex
                          where ex.sku=upper(trim(fi.sku_codigo)))
        union all
        select e.forecast_item_id,e.forecast_id,e.root_sku_id,component.id,
               component.sku,e.quantidade_planejada*bom.quantidade,e.nivel+1,
               e.caminho_ids||component.id,e.caminho_codigos||component.sku
        from exploded e
        join public.cadastro_bom_componentes bom
          on upper(trim(bom.parent_sku))=upper(trim(e.sku_codigo))
        join public.skus component
          on upper(trim(component.sku))=upper(trim(bom.component_sku))
         and component.active is true
        where e.nivel<12 and not(component.id=any(e.caminho_ids))
          and not exists (select 1 from public.erp_external_stock_items ex
                          where ex.sku=upper(trim(component.sku)))
    ), leaves as (
        select e.* from exploded e where not exists (
            select 1 from public.cadastro_bom_componentes bom
            join public.skus child
              on upper(trim(child.sku))=upper(trim(bom.component_sku))
             and child.active is true
            where upper(trim(bom.parent_sku))=upper(trim(e.sku_codigo))
              and not exists (select 1 from public.erp_external_stock_items ex
                              where ex.sku=upper(trim(child.sku)))
        )
    )
    insert into public.suprimentos_forecast_necessidades(
        forecast_id,forecast_item_id,sku_id,sku_codigo,descricao,unidade,
        quantidade_planejada,nivel_maximo,origem,caminho_bom)
    select l.forecast_id,l.forecast_item_id,l.sku_id,sku.sku,sku.descricao,
           coalesce(sku.unidade,''),sum(l.quantidade_planejada),max(l.nivel),
           case when max(l.nivel)=0 then 'SKU_SEM_BOM' else 'BOM' end,
           array[]::text[]
    from leaves l join public.skus sku on sku.id=l.sku_id
    group by l.forecast_id,l.forecast_item_id,l.sku_id,sku.sku,sku.descricao,sku.unidade;

    update public.suprimentos_forecast_itens fi set
        possui_bom=exists(select 1 from public.cadastro_bom_componentes bom
                         where upper(trim(bom.parent_sku))=upper(trim(fi.sku_codigo))),
        bom_explodida_em=now(),updated_at=now()
    where fi.forecast_id=p_forecast_id;
end;
$function$;
