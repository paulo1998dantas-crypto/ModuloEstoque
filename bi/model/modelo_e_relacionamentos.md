# Modelo e relacionamentos

## Modo de armazenamento

Usar **Importar** para reduzir carga na base operacional e manter navegação rápida. Agendar atualização conforme a operação; recomendação inicial: a cada 60 minutos em horário produtivo. DirectQuery fica reservado a uma fase posterior, se a latência operacional exigir.

## Tabelas

| Tabela | Grão | Uso principal |
|---|---|---|
| `dim_sku` | 1 linha por SKU | Filtro comum de material |
| `dim_ordem_servico` | 1 linha por O.S. | Cliente, veículo, WIP e prazo |
| `fato_estoque_atual` | 1 linha por SKU | Snapshot físico, empenhado e disponível |
| `fato_empenhos_abertos` | 1 linha por empenho aberto | Detalhe de reservas, O.S. e fluxo |
| `fato_movimentacoes_estoque` | 1 linha por movimento | Entradas, consumo e histórico |
| `fato_inventarios` | 1 linha por contagem | Acuracidade e divergências |
| `fato_necessidades_os` | 1 linha por O.S./SKU | Necessidade bruta, coberta e pendente |
| `fato_etapas_producao` | 1 linha por etapa de O.S. | WIP, fila, avanço e duração |
| `fato_compras_transito` | 1 linha por linha de O.C. | Compra aberta e trânsito |
| `fato_recebimentos_inspecao` | 1 linha por linha recebida | Recebimento e qualidade |
| `fato_forecast` | 1 linha por Forecast | Demanda futura e qualidade estrutural |
| `fato_forecast_necessidades` | 1 linha por Forecast/SKU | Explosão de material futura |
| `fato_mrp` | 1 linha por SKU | Resultado consolidado do MRP I |

## Relacionamentos

Criar relacionamentos de direção única, da dimensão para o fato:

- `dim_sku[sku_id]` 1:* para todos os fatos que possuam `sku_id`.
- `dim_ordem_servico[work_order_id]` 1:* para necessidades, empenhos, movimentos, etapas e compras.
- `dCalendario[Data]` 1:* para a data principal de cada fato.

Datas principais: movimento=`data`; inventário=`data_contagem`; compras=`data_necessidade`; recebimento=`data`; Forecast=`data_entrega_prevista`; O.S.=`data_entrega_vigente`. Datas alternativas devem usar relacionamentos inativos e `USERELATIONSHIP` nas medidas específicas.

Não relacionar fato com fato. `fato_mrp` já é a consolidação governada por SKU e deve permanecer ligada apenas à `dim_sku`.

## Calendário

```DAX
dCalendario =
VAR DataInicial = DATE(2024, 1, 1)
VAR DataFinal = DATE(YEAR(TODAY()) + 2, 12, 31)
RETURN
ADDCOLUMNS(
    CALENDAR(DataInicial, DataFinal),
    "Ano", YEAR([Date]),
    "MesNumero", MONTH([Date]),
    "Mes", FORMAT([Date], "mmm"),
    "AnoMes", FORMAT([Date], "yyyy-MM"),
    "Trimestre", "T" & FORMAT([Date], "Q"),
    "Semana", WEEKNUM([Date], 2)
)
```

Renomear `Date` para `Data`, marcar como tabela de datas e ordenar `Mes` por `MesNumero`.
