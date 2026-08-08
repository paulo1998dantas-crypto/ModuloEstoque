# Modelo e relacionamentos

## Armazenamento e segurança

O modelo usa **Importar**, com conexão PostgreSQL direta por SSL. A atualização lê somente as views do schema privado `bi` por meio do papel `powerbi_reader`; esse papel não possui `INSERT`, `UPDATE` ou `DELETE` na camada consultiva. Credenciais não são versionadas.

## Grãos do modelo

| Tabela | Grão | Uso principal |
|---|---|---|
| `dim_sku` | uma linha por SKU | filtro comum de material, grupo e unidade |
| `dim_ordem_servico` | uma linha por O.S. | cliente, setor, veículo, WIP e prazo |
| `fato_estoque_atual` | uma linha por SKU | snapshot físico, empenhado e disponível |
| `fato_empenhos_abertos` | uma linha por empenho aberto | reserva por O.S. ou fluxo compartilhado |
| `fato_movimentacoes_estoque` | uma linha por movimento | entradas, baixas e histórico |
| `fato_inventarios` | uma linha por contagem | acuracidade e divergência |
| `fato_necessidades_os` | uma linha por O.S./SKU | necessidade bruta, coberta e pendente |
| `fato_etapas_producao` | uma linha por etapa de O.S. | WIP, fila, avanço e duração |
| `fato_compras_transito` | uma linha por linha de O.C. | compra aberta e trânsito |
| `fato_recebimentos_inspecao` | uma linha por linha recebida | recebimento e qualidade |
| `fato_forecast` | uma linha por Forecast | demanda futura e qualidade estrutural |
| `fato_forecast_necessidades` | uma linha por Forecast/SKU | explosão de materiais futura |
| `fato_mrp` | uma linha por SKU | consolidação do MRP I |
| `dCalendario` | uma linha por dia | filtro temporal compartilhado |

## Cardinalidade e direção

- `dim_sku[sku_id]` tem cardinalidade 1:* para todos os fatos que contêm `sku_id`.
- `dim_ordem_servico[work_order_id]` tem cardinalidade 1:* para necessidades, empenhos, movimentos, etapas e compras.
- `dCalendario[Data]` tem cardinalidade 1:* para a data principal de cada fato.
- A filtragem é unidirecional, da dimensão para o fato.
- Não existe relacionamento fato a fato. `fato_mrp` é uma consolidação governada por SKU.

Datas principais: movimento=`data`; inventário=`data_contagem`; compras=`data_necessidade`; recebimento=`data`; Forecast=`data_entrega_prevista`; O.S.=`data_entrega_vigente`. Datas alternativas devem ficar inativas e ser acionadas por `USERELATIONSHIP` em medidas específicas.

## Segurança semântica de quantidades

Todas as colunas numéricas importadas estão configuradas com `summarizeBy: none`, eliminando soma implícita. Quantidades físicas são expostas somente por medidas explícitas e retornam vazio quando mais de uma unidade está no contexto. Contagens, percentuais e valores monetários permanecem consolidados no nível executivo.
