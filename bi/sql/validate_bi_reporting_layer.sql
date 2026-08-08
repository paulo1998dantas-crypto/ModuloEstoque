-- QA somente leitura da camada Power BI.
-- Cada linha deve retornar passou = true.

with checks as (
    select
        'estoque_fisico_reconciliado'::text as verificacao,
        (select coalesce(sum(estoque_atual), 0) from bi.fato_estoque_atual)::numeric as atual,
        (select coalesce(sum(saldo_atual), 0) from public.stock_balances)::numeric as esperado
    union all
    select
        'empenhos_abertos_reconciliados',
        (select coalesce(sum(quantidade_pendente), 0) from bi.fato_empenhos_abertos),
        (
            select coalesce(sum(greatest(m.quantidade - coalesce(b.baixada, 0), 0)), 0)
            from public.movements m
            left join (
                select related_movement_id, sum(quantidade) as baixada
                from public.movements
                where tipo = 'BAIXA' and movement_status = 'ATIVA'
                group by related_movement_id
            ) b on b.related_movement_id = m.id
            where m.tipo in ('EMPENHO', 'SAIDA') and m.movement_status = 'ATIVA'
        )
    union all
    select
        'transito_sem_saldo_negativo',
        (select count(*)::numeric from bi.fato_compras_transito where quantidade_pendente < 0),
        0::numeric
    union all
    select
        'necessidade_os_formula',
        (
            select count(*)::numeric
            from bi.fato_necessidades_os
            where quantidade_pendente <> greatest(quantidade_necessaria - quantidade_coberta, 0)
        ),
        0::numeric
    union all
    select
        'mrp_formula_compra',
        (
            select count(*)::numeric
            from bi.fato_mrp
            where necessidade_compra < 0
               or necessidade_compra <> greatest(necessidade_total - estoque_disponivel - quantidade_transito, 0)
        ),
        0::numeric
    union all
    select
        'necessidades_sem_sku_cadastrado',
        (select count(*)::numeric from bi.fato_necessidades_os where sku_id is null),
        0::numeric
    union all
    select
        'dim_sku_sem_chave_duplicada',
        (
            select count(*)::numeric
            from (
                select sku_id
                from bi.dim_sku
                group by sku_id
                having count(*) > 1
            ) d
        ),
        0::numeric
    union all
    select
        'dim_os_sem_chave_duplicada',
        (
            select count(*)::numeric
            from (
                select work_order_id
                from bi.dim_ordem_servico
                group by work_order_id
                having count(*) > 1
            ) d
        ),
        0::numeric
    union all
    select
        'papel_powerbi_com_select',
        has_table_privilege('powerbi_reader', 'bi.fato_mrp', 'SELECT')::int::numeric,
        1::numeric
    union all
    select
        'papel_powerbi_sem_escrita',
        (
            not has_table_privilege('powerbi_reader', 'bi.fato_mrp', 'INSERT')
            and not has_table_privilege('powerbi_reader', 'bi.fato_mrp', 'UPDATE')
            and not has_table_privilege('powerbi_reader', 'bi.fato_mrp', 'DELETE')
        )::int::numeric,
        1::numeric
)
select
    verificacao,
    atual = esperado as passou,
    atual,
    esperado
from checks
order by verificacao;

-- Alertas de qualidade: não interrompem a reconciliação, mas devem aparecer no BI.
select
    (select count(*) from bi.dim_sku where ativo and nullif(btrim(unidade), '') is null)
        as skus_ativos_sem_unidade,
    (select count(*) from bi.fato_compras_transito where em_transito and data_necessidade is null)
        as linhas_transito_sem_data,
    (
        select count(*)
        from bi.fato_compras_transito
        where em_transito
          and data_necessidade < date '2000-01-01'
    ) as linhas_transito_data_invalida;
