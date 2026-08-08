# Medidas DAX

A fonte única das medidas é [`powerbi_measures.json`](powerbi_measures.json). O arquivo contém nome, expressão DAX, formato e descrição das 77 medidas publicadas. Os scripts `sync_powerbi_tmdl.js` e `sync_powerbi_measures.ps1` mantêm o projeto PBIP e o modelo aberto no Power BI Desktop sincronizados com essa definição.

## Indicadores executivos

- Estoque: `SKUs Ativos`, `SKUs com Saldo`, `SKUs Zerados`, `SKUs em Risco Estoque`, `Movimentações Ativas` e `Baixas Registradas`.
- PCP: `O.S. no WIP`, `O.S. Atrasadas`, `% O.S. Atrasadas`, `Avanço Médio %`, `O.S. com Material Pendente` e `Forecasts Ativos`.
- Compras: `Linhas em Trânsito`, `Valor em Trânsito`, `Linhas Atrasadas`, `Linhas sem Data`, `Linhas Data Inválida`, `O.C. Abertas` e `Taxa Aprovação Média %`.
- MRP: `SKUs com Demanda`, `SKUs Cobertos`, `% SKUs MRP Cobertos`, `SKUs a Comprar`, `SKUs Críticos`, `SKUs Urgentes` e `O.S. Impactadas por Compra`.

## Quantidades físicas protegidas por unidade

As medidas `Estoque Atual (U.M.)`, `Empenhado Total (U.M.)`, `Estoque Disponível (U.M.)`, `Entradas (U.M.)`, `Consumo (U.M.)`, `Necessidade Total (U.M.)`, `Em Trânsito (U.M.)`, `Estoque Disponível MRP (U.M.)` e `Necessidade Compra (U.M.)` usam `HASONEVALUE(dim_sku[unidade])`.

Sem uma única unidade selecionada, elas retornam `BLANK()`. Isso evita resultados fisicamente inválidos, como somar peças, metros e quilogramas. Os aliases legados permanecem por compatibilidade e obedecem à mesma proteção.

## Regras operacionais relevantes

- `Linhas Atrasadas` considera somente linhas em trânsito com data válida entre 01/01/2000 e ontem.
- `Linhas Data Inválida` evidencia datas muito anteriores ao horizonte operacional.
- `Dias para Remessa Válido` suprime durações absurdas causadas por datas inválidas.
- `Prioridade MRP` classifica compra vencida como **CRÍTICO**, necessidade nos próximos sete dias como **URGENTE** e as demais como **ATENÇÃO**.
- `Taxa Aprovação Média %` calcula a média da proporção aprovada por linha confirmada, sem misturar quantidades de unidades diferentes.

Percentuais usam uma casa decimal, quantidades até três casas e valores monetários usam BRL.
