# Layout do cockpit industrial

O relatório usa canvas 16:9 (1280 × 720), navegação por abas, fundo claro e cores semânticas: azul para informação, verde para condição coberta, âmbar para atenção e vermelho para ruptura ou atraso. A composição é regenerada de forma determinística por `build_powerbi_report.js`.

## 0. Cockpit Industrial

- KPIs executivos: SKUs ativos, SKUs zerados, O.S. no WIP, O.S. atrasadas, SKUs a comprar, linhas de compra atrasadas e valor em trânsito.
- Barras: maiores fornecedores por valor em trânsito e grupos com mais SKUs a comprar.
- Tabela de ação: materiais com necessidade de compra, prioridade, O.S. impactadas e próxima necessidade.
- Segmentadores: unidade de medida e cliente.

## 1. Visão Estoque

- KPIs: SKUs ativos, com saldo, zerados, em risco, empenhados, movimentações ativas e baixas registradas.
- Barras: situação dos SKUs e movimentações por tipo.
- Tabela de exceção: SKU, descrição, grupo, unidade, saldos físicos protegidos por unidade e ação recomendada.
- Segmentadores: unidade de medida e grupo.
- Drill-through por SKU para saldos, empenhos e histórico de movimentos.

## 2. Visão PCP

- KPIs: O.S. no WIP, atrasadas, percentual de atraso, avanço médio, O.S. com material pendente, linhas pendentes e Forecasts ativos.
- Barras: O.S. por fase do WIP e avanço por cliente.
- Tabela de exceção: O.S., cliente, fase, entrega, dias, avanço, material pendente e risco.
- Segmentadores: cliente e setor.
- Drill-through por O.S. para necessidades e etapas produtivas.

## 3. Compras e Trânsito

- KPIs: linhas em trânsito, valor em trânsito, atrasadas válidas, sem data, datas inválidas, O.C. abertas e taxa média de aprovação.
- Barras: valor em trânsito por fornecedor e linhas por fornecedor.
- Tabela de ação: O.C., fornecedor, SKU, situação, data, dias válidos, valor e ação recomendada.
- Segmentadores: fornecedor e unidade de medida.
- Drill-through por fornecedor para compras e recebimentos.

## 4. Visão MRP

- KPIs: SKUs com demanda, cobertos, cobertura percentual, a comprar, críticos, urgentes e O.S. impactadas.
- Barras: prioridades do MRP e maiores grupos com necessidade de compra.
- Tabela de ação: SKU, descrição, unidade, prioridade, necessidade, estoque, trânsito, compra, próxima necessidade e O.S. impactadas.
- Segmentadores: unidade de medida e grupo.
- A comparação física só aparece com uma única unidade de medida selecionada.

## 5. Histórico de Produção

- KPIs: carros finalizados, carros entregues, tempo médio e mediano de produção, finalizados em atraso, percentual de atraso e durações inválidas.
- Linhas: finalizados e entregues por mês, cada medida usando sua própria data de evento.
- Barras: finalizados por mercado e tempo médio por linha de produto.
- Tabela histórica: O.S., chassi, cliente, mercado, linha, início de produção, finalização, entrega, duração, prazo e situação.
- Segmentadores: mês, mercado (`LICITAÇÃO`/`VAREJO`), cliente e linha de produto (`LE`, `LAB`, `LB`, `LAE` etc.).
- A data inicial de produção é a maior entre aprovação da proposta e chegada do veículo; durações negativas são sinalizadas e excluídas das médias.

## Páginas ocultas de drill-through

- **D. Detalhe SKU**: estoque, empenhos, movimentos e necessidade do material.
- **D. Detalhe O.S.**: necessidade, cobertura, etapa e prazo da ordem.
- **D. Detalhe Fornecedor**: compras em trânsito, datas e inspeções.

## Interações e salvaguardas

- Segmentadores compactos em modo dropdown e filtros visuais de exceção nas tabelas de ação.
- Relacionamentos sempre fluem das dimensões para os fatos; não há relacionamento fato a fato.
- Quantidades físicas não são somadas entre `pc`, `kg`, `m`, `m²`, `cj` ou outras unidades. Medidas físicas retornam vazio sem um único contexto de unidade.
- Datas de remessa anteriores ao ano 2000 são classificadas como inválidas e nunca como atraso operacional.
