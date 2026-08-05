# Medidas DAX iniciais

Os nomes abaixo pressupõem que as views foram importadas com o mesmo nome do schema `bi`.

## Estoque

```DAX
Estoque Atual =
SUM(fato_estoque_atual[estoque_atual])

Empenhado Total =
SUM(fato_estoque_atual[empenhado_total])

Empenhado O.S. =
IF(HASONEVALUE(dim_sku[unidade]), SUM(fato_estoque_atual[empenhado_os]))

Empenhado em Fluxo =
IF(HASONEVALUE(dim_sku[unidade]), SUM(fato_estoque_atual[empenhado_fluxo]))

Estoque Disponível =
SUM(fato_estoque_atual[estoque_disponivel])

SKUs com Saldo =
CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[estoque_atual] > 0)

SKUs Empenhados =
CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[empenhado_total] > 0)

SKUs com Disponível =
CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[estoque_disponivel] > 0)

SKUs Zerados =
CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[status_estoque] = "ZERADO")

SKUs Baixos =
CALCULATE(DISTINCTCOUNT(fato_estoque_atual[sku_id]), fato_estoque_atual[status_estoque] IN {"BAIXO", "SALDO_COMPROMETIDO"})

Entradas =
CALCULATE(
    SUM(fato_movimentacoes_estoque[quantidade]),
    fato_movimentacoes_estoque[tipo] = "ENTRADA",
    fato_movimentacoes_estoque[movement_status] = "ATIVA"
)

Consumo =
ABS(CALCULATE(
    SUM(fato_movimentacoes_estoque[quantidade]),
    fato_movimentacoes_estoque[tipo] = "BAIXA",
    fato_movimentacoes_estoque[movement_status] = "ATIVA"
))

Divergência Inventário = SUM(fato_inventarios[diferenca])
```

## PCP

```DAX
O.S. no WIP =
CALCULATE(DISTINCTCOUNT(dim_ordem_servico[work_order_id]), dim_ordem_servico[em_wip] = TRUE())

O.S. em Produção =
CALCULATE(DISTINCTCOUNT(dim_ordem_servico[work_order_id]), dim_ordem_servico[fase_wip] = "PRODUCAO")

O.S. Atrasadas =
CALCULATE(DISTINCTCOUNT(dim_ordem_servico[work_order_id]), dim_ordem_servico[entrega_atrasada] = TRUE())

Avanço Médio % =
AVERAGEX(FILTER(dim_ordem_servico, dim_ordem_servico[em_wip] = TRUE()), dim_ordem_servico[percentual_avanco])

Necessidade O.S. Bruta =
IF(HASONEVALUE(dim_sku[unidade]), SUM(fato_necessidades_os[quantidade_necessaria]))

Cobertura O.S. =
IF(HASONEVALUE(dim_sku[unidade]), SUM(fato_necessidades_os[quantidade_coberta]))

Necessidade O.S. Pendente =
IF(HASONEVALUE(dim_sku[unidade]), SUM(fato_necessidades_os[quantidade_pendente]))

O.S. com Material Pendente =
CALCULATE(
    DISTINCTCOUNT(fato_necessidades_os[work_order_id]),
    fato_necessidades_os[quantidade_pendente] > 0
)
```

## Compras e recebimentos

```DAX
Quantidade em Trânsito =
IF(
    HASONEVALUE(dim_sku[unidade]),
    CALCULATE(SUM(fato_compras_transito[quantidade_pendente]), fato_compras_transito[em_transito] = TRUE())
)

Linhas em Trânsito =
CALCULATE(COUNTROWS(fato_compras_transito), fato_compras_transito[em_transito] = TRUE())

Valor em Trânsito =
CALCULATE(SUM(fato_compras_transito[valor_pendente]), fato_compras_transito[em_transito] = TRUE())

Linhas Atrasadas =
CALCULATE(COUNTROWS(fato_compras_transito), fato_compras_transito[em_transito] = TRUE(), fato_compras_transito[situacao_transito] = "ATRASADA")

O.C. Abertas =
CALCULATE(DISTINCTCOUNT(fato_compras_transito[purchase_order_id]), fato_compras_transito[em_transito] = TRUE())

Recebido Físico =
CALCULATE(SUM(fato_recebimentos_inspecao[quantidade_fisica]), fato_recebimentos_inspecao[status_recebimento] = "CONFIRMADO")

Recebido Aprovado =
CALCULATE(SUM(fato_recebimentos_inspecao[quantidade_aprovada]), fato_recebimentos_inspecao[status_recebimento] = "CONFIRMADO")

Recebido Condicional =
CALCULATE(SUM(fato_recebimentos_inspecao[quantidade_condicional]), fato_recebimentos_inspecao[status_recebimento] = "CONFIRMADO")

Recebido Rejeitado =
CALCULATE(SUM(fato_recebimentos_inspecao[quantidade_rejeitada]), fato_recebimentos_inspecao[status_recebimento] = "CONFIRMADO")

Taxa Aprovação % = DIVIDE([Recebido Aprovado], [Recebido Físico])
```

## Forecast e MRP

```DAX
Forecasts Ativos =
CALCULATE(DISTINCTCOUNT(fato_forecast[forecast_id]), fato_forecast[status] = "ATIVO")

Forecasts sem Estrutura =
CALCULATE(DISTINCTCOUNT(fato_forecast[forecast_id]), fato_forecast[status] = "ATIVO", fato_forecast[possui_estrutura_materiais] = FALSE())

Necessidade Total =
SUM(fato_mrp[necessidade_total])

Em Trânsito =
SUM(fato_mrp[quantidade_transito])

MRP Estoque Disponível =
SUM(fato_mrp[estoque_disponivel])

Saldo Projetado =
IF(HASONEVALUE(dim_sku[unidade]), SUM(fato_mrp[saldo_projetado]))

Necessidade de Compra =
SUM(fato_mrp[necessidade_compra])

Necessidade de Compra c/ Mínimo =
SUM(fato_mrp[necessidade_compra_com_estoque_minimo])

SKUs com Demanda =
CALCULATE(DISTINCTCOUNT(fato_mrp[sku_id]), fato_mrp[necessidade_total] > 0)

SKUs Cobertos =
CALCULATE(DISTINCTCOUNT(fato_mrp[sku_id]), fato_mrp[status_mrp] = "COBERTO")

SKUs a Comprar =
CALCULATE(DISTINCTCOUNT(fato_mrp[sku_id]), fato_mrp[status_mrp] = "COMPRAR")

Última Atualização = MAX(fato_mrp[atualizado_em])
```

Formatar percentuais com uma casa decimal, quantidades com até três casas e valores monetários em BRL. Os cartões executivos somam todas as unidades para atender à visão global solicitada; use o filtro de unidade quando a comparação física entre SKUs exigir homogeneidade.
