-- Reconcile active purchase documents from Suprimentos into the shared ERP
-- purchase-order read/write model.
--
-- Safety properties:
--   * additive and idempotent by suprimentos_documentos.id;
--   * only documents explicitly marked "emitido" are made receivable;
--   * does not create receipts, stock movements or change stock balances;
--   * repeated execution does not duplicate orders or lines;
--   * visible O.C. numbers are not treated as primary keys.

begin;
set local lock_timeout = '5s';
set local statement_timeout = '2min';

do $$
declare
    invalid_documents integer;
begin
    select count(*)
      into invalid_documents
      from public.suprimentos_documentos document
     where document.tipo = 'oc'
       and document.status = 'emitido'
       and (
           jsonb_typeof(document.itens) <> 'array'
           or jsonb_array_length(document.itens) = 0
           or exists (
               select 1
                 from jsonb_array_elements(document.itens) item
                where coalesce(item->>'qtd', '') !~ '^[+]?[0-9]+([.][0-9]+)?$'
                   or (item->>'qtd')::numeric <= 0
           )
       );

    if invalid_documents > 0 then
        raise exception
            'Reconciliation blocked: % active purchase document(s) have no valid positive-quantity lines.',
            invalid_documents;
    end if;
end
$$;

insert into public.erp_purchase_orders (
    id,
    numero_oc,
    categoria,
    fornecedor_nome,
    data_criacao,
    data_emissao,
    criado_por,
    status,
    destino,
    frete,
    data_necessidade,
    observacoes,
    valor_total_pedido,
    idempotency_key,
    created_at,
    updated_at
)
select
    gen_random_uuid(),
    document.numero,
    case
        when upper(coalesce(document.dados->>'oc_categoria', '')) in ('GERAL', 'BANCOS')
            then upper(document.dados->>'oc_categoria')
        else 'GERAL'
    end,
    coalesce(
        nullif(trim(document.dados->>'fornecedor'), ''),
        nullif(trim(document.dados->>'razao_social'), ''),
        'FORNECEDOR NÃO INFORMADO'
    ),
    document.created_at,
    document.data_criacao::timestamp at time zone 'America/Sao_Paulo',
    coalesce(nullif(document.criado_por, ''), 'reconciliacao-suprimentos'),
    'EMITIDA',
    coalesce(document.dados->>'destino', ''),
    case
        when coalesce(document.dados->>'frete', '') ~ '^[-+]?[0-9]+([.][0-9]+)?$'
            then (document.dados->>'frete')::numeric
        else 0
    end,
    case
        when coalesce(document.dados->>'previsao', '') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            then (document.dados->>'previsao')::date
        else null
    end,
    coalesce(document.dados->>'obs', ''),
    (
        select coalesce(sum(
            (item->>'qtd')::numeric
            * case
                when coalesce(item->>'valor', '') ~ '^[-+]?[0-9]+([.][0-9]+)?$'
                    then (item->>'valor')::numeric
                else 0
              end
        ), 0)
          from jsonb_array_elements(document.itens) item
    ),
    'suprimentos-oc:' || document.id::text,
    document.created_at,
    document.updated_at
from public.suprimentos_documentos document
where document.tipo = 'oc'
  and document.status = 'emitido'
  and document.erp_purchase_order_id is null
on conflict (idempotency_key) do nothing;

insert into public.erp_purchase_order_lines (
    id,
    purchase_order_id,
    numero_linha,
    sku_id,
    sku_codigo,
    descricao_original,
    unidade,
    quantidade_pedida,
    quantidade_recebida,
    valor_unitario_pedido,
    destino,
    data_necessidade,
    status
)
select
    gen_random_uuid(),
    purchase_order.id,
    expanded.numero_linha::integer,
    stock_sku.id,
    nullif(trim(expanded.item->>'codigo'), ''),
    coalesce(
        nullif(trim(expanded.item->>'descricao'), ''),
        nullif(trim(expanded.item->>'descricao_primaria'), ''),
        'ITEM SEM DESCRIÇÃO'
    ),
    coalesce(nullif(trim(expanded.item->>'unidade'), ''), 'UN'),
    (expanded.item->>'qtd')::numeric,
    0,
    case
        when coalesce(expanded.item->>'valor', '') ~ '^[-+]?[0-9]+([.][0-9]+)?$'
            then (expanded.item->>'valor')::numeric
        else 0
    end,
    coalesce(document.dados->>'destino', ''),
    case
        when coalesce(document.dados->>'previsao', '') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            then (document.dados->>'previsao')::date
        else null
    end,
    'PENDENTE'
from public.suprimentos_documentos document
join public.erp_purchase_orders purchase_order
  on purchase_order.idempotency_key = 'suprimentos-oc:' || document.id::text
cross join lateral jsonb_array_elements(document.itens)
    with ordinality as expanded(item, numero_linha)
left join lateral (
    select sku.id
      from public.skus sku
     where sku.active is true
       and trim(sku.sku) = trim(expanded.item->>'codigo')
     order by sku.id
     limit 1
) stock_sku on true
where document.tipo = 'oc'
  and document.status = 'emitido'
on conflict (purchase_order_id, numero_linha) do nothing;

update public.suprimentos_documentos document
   set erp_purchase_order_id = purchase_order.id,
       updated_at = greatest(document.updated_at, now())
  from public.erp_purchase_orders purchase_order
 where document.tipo = 'oc'
   and document.status = 'emitido'
   and document.erp_purchase_order_id is null
   and purchase_order.idempotency_key = 'suprimentos-oc:' || document.id::text;

insert into public.erp_audit_events (
    entity_type,
    entity_id,
    action,
    actor,
    origin,
    after_data,
    reason
)
select
    'PURCHASE_ORDER',
    purchase_order.id,
    'RECONCILIADA_ORIGEM_SUPRIMENTOS',
    'migration-202607301200',
    'SUPRIMENTOS',
    jsonb_build_object(
        'legacy_document_id', document.id,
        'numero_oc', document.numero,
        'idempotency_key', purchase_order.idempotency_key
    ),
    'Espelho ERP aditivo de O.C. ativa; nenhuma movimentação de estoque foi criada.'
from public.suprimentos_documentos document
join public.erp_purchase_orders purchase_order
  on purchase_order.id = document.erp_purchase_order_id
where document.tipo = 'oc'
  and purchase_order.idempotency_key = 'suprimentos-oc:' || document.id::text
  and not exists (
      select 1
        from public.erp_audit_events audit
       where audit.entity_type = 'PURCHASE_ORDER'
         and audit.entity_id = purchase_order.id
         and audit.action = 'RECONCILIADA_ORIGEM_SUPRIMENTOS'
  );

commit;
