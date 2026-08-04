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
)
select
    verificacao,
    atual = esperado as passou,
    atual,
    esperado
from checks
order by verificacao;
