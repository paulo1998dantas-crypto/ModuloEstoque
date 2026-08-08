# Layout do cockpit industrial

O relatório usa canvas 16:9 (1280 × 720), navegação por abas, fundo claro e cores semânticas: azul para informação, verde para condição coberta, âmbar para atenção e vermelho para ruptura ou atraso. A composição é regenerada de forma determinística por `build_powerbi_report.js`.

## 0. Cockpit Industrial

- KPIs executivos: O.S. no WIP, O.S. atrasadas pelo SLA técnico, O.S. com material pendente, SKUs críticos, SKUs a comprar e linhas atrasadas.
- Barras: O.S. atrasadas por cliente e SKUs críticos por categoria de material.
- Tabela de ação: materiais sem estoque disponível, prioridade, O.S. impactadas e próxima necessidade.
- Segmentadores: tipo de serviço, cliente e categoria de material.

## 1. Visão Estoque

- KPIs: SKUs ativos, com saldo, zerados, em risco, empenhados, movimentações ativas e baixas registradas.
- Barras: situação dos SKUs e movimentações por tipo.
- Tabela de exceção: SKU, descrição, categoria, unidade, saldos físicos protegidos por unidade e ação recomendada.
- Segmentadores: tipo de serviço, unidade de medida e categoria de material. Os saldos físicos permanecem globais; o tipo de serviço afeta os indicadores ligados à demanda.
- Drill-through por SKU para saldos, empenhos e histórico de movimentos.

## 2. Visão PCP

- KPIs: O.S. no WIP, atrasadas, percentual de atraso, avanço médio, O.S. com material pendente, linhas pendentes e Forecasts ativos.
- Barras: O.S. por fase do WIP e avanço por cliente.
- Tabela de exceção: O.S., cliente, linha, início, SLA de 30/45 dias, data limite, avanço, dias de atraso, material pendente e risco.
- Segmentadores: tipo de serviço, cliente e linha de produto.
- Drill-through por O.S. para necessidades e etapas produtivas.

## 3. Compras e Trânsito

- KPIs: linhas em trânsito, valor em trânsito, atrasadas válidas, sem data, datas inválidas, O.C. abertas e taxa média de aprovação.
- Barras: valor em trânsito por fornecedor e linhas por fornecedor.
- Tabela de ação: O.C., fornecedor, SKU, situação, data, dias válidos, valor e ação recomendada.
- Segmentadores: tipo de serviço, fornecedor e situação de trânsito.
- Drill-through por fornecedor para compras e recebimentos.

## 4. Visão MRP

- KPIs: SKUs com demanda, em risco, críticos, a comprar, O.S. com material pendente e O.S. impactadas por compras em trânsito.
- Barras: SKUs críticos por categoria de material e O.S. impactadas por compras por linha de produto.
- Tabela de ação: SKU, descrição, unidade, prioridade, necessidade, estoque, trânsito, compra, próxima necessidade e O.S. impactadas.
- Segmentadores: tipo de serviço, unidade de medida e categoria de material.
- A comparação física só aparece com uma única unidade de medida selecionada.

## 5. Histórico de Produção

- KPIs: carros finalizados, carros entregues, carros retirados, tempo médio e mediano, finalizados em atraso e percentual de atraso.
- Linhas: finalizados, entregues e retirados por mês, cada medida usando sua própria data de evento.
- Barras: finalizados por mercado e tempo médio por linha de produto.
- Tabela histórica: O.S., chassi, cliente, tipo de serviço, mercado, linha, início, limite técnico, finalização, entrega, retirada, duração e situação.
- Segmentadores: mês, tipo de serviço, mercado (`LICITAÇÃO`/`VAREJO`), cliente e linha de produto (`LE`, `LAB`, `LB`, `LAE` etc.).
- A data inicial de produção é a maior entre aprovação da proposta e chegada do veículo; durações negativas são sinalizadas e excluídas das médias.
- RETIRADA é exibida em indicador próprio e não compõe finalizados nem entregues.

## 6. Fechamento de Produção

- Página exclusivamente gráfica para reunião e fechamento produtivo.
- Colunas empilhadas: etapas concluídas por setor e semana, com a data real de término de cada etapa.
- Rosca: participação dos setores no período filtrado.
- Linhas: veículos finalizados e entregues por semana, como eventos distintos.
- Barras adicionais: consolidação mensal e anual das etapas concluídas.
- Segmentadores: tipo de serviço, linha de produto, setor produtivo e ano.
- `A/C`/`AC`, `ELÉTRICA`/`ELETRICA`, `LIBERAÇÃO`/`LIBERACAO` e demais códigos foram normalizados para não dividir o mesmo setor nos gráficos.

## Páginas ocultas de drill-through

- **D. Detalhe SKU**: estoque, empenhos, movimentos e necessidade do material.
- **D. Detalhe O.S.**: necessidade, cobertura, etapa e prazo da ordem.
- **D. Detalhe Fornecedor**: compras em trânsito, datas e inspeções.

## Interações e salvaguardas

- Segmentadores compactos em modo dropdown e filtros visuais de exceção nas tabelas de ação.
- Relacionamentos sempre fluem das dimensões para os fatos; não há relacionamento fato a fato.
- Quantidades físicas não são somadas entre `pc`, `kg`, `m`, `m²`, `cj` ou outras unidades. Medidas físicas retornam vazio sem um único contexto de unidade.
- Datas de remessa anteriores ao ano 2000 são classificadas como inválidas e nunca como atraso operacional.
- O logo da JI Montadora é um recurso local versionado em gradiente azul e aparece ampliado no cabeçalho de todas as páginas, inclusive drill-throughs.
