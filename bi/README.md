# Power BI operacional — JI Montadora

Este diretório contém a fonte versionada do BI consultivo conectado ao Supabase. O relatório não grava em tabelas operacionais e a senha de conexão não é armazenada no GitHub.

## Arquitetura

- Fonte: PostgreSQL do projeto Supabase `rodtxswtqbsbtukmvobn`.
- Camada semântica SQL: 14 views no schema privado `bi`.
- Segurança: grupo `powerbi_reader` com `USAGE` no schema e `SELECT` nas views, sem `INSERT`, `UPDATE` ou `DELETE`.
- Conexão: PostgreSQL direto com SSL, porta 5432, em modo **Importar**.
- Modelo: estrela, filtros unidirecionais e nenhuma relação fato a fato.
- Credenciais: cadastradas somente no Power BI Desktop/Gateway.

## Páginas publicadas

0. **Cockpit Industrial** — visão executiva de estoque, PCP, compras e MRP.
1. **Visão Estoque** — disponibilidade, comprometimento, movimentações e exceções.
2. **Visão PCP** — WIP, avanço, atrasos, material pendente e Forecast.
3. **Compras e Trânsito** — O.C. abertas, valor, prazo, recebimento e inspeção.
4. **Visão MRP** — demanda, cobertura, prioridade, trânsito e necessidade de compra.
5. **Histórico de Produção** — finalização, entrega e retirada separadas, SLA técnico e duração por veículo, tipo de serviço, mercado, cliente e linha de produto.

O relatório inclui páginas ocultas de drill-through para SKU, O.S. e fornecedor. O detalhamento e as interações estão documentados em `model/layout_paginas.md`.

## Salvaguarda de unidade de medida

Quantidades físicas de unidades diferentes não são somadas. Cartões e comparações físicas só exibem valor quando há uma única unidade selecionada. No consolidado executivo são usados contagens, percentuais e valores monetários. Todas as colunas numéricas importadas têm soma implícita desabilitada.

## Fontes entregues

- `sql/bi_reporting_layer.sql`: definição das views consultivas.
- `sql/create_powerbi_login.template.sql`: criação privada do login; não é alterada pelo gerador.
- `sql/validate_bi_reporting_layer.sql`: reconciliações somente leitura.
- `power-query/conexao_power_bi.md`: conexão e consultas M.
- `model/modelo_e_relacionamentos.md`: grão, cardinalidade e relacionamentos.
- `model/dicionario_metricas.md`: regras de negócio dos indicadores.
- `model/powerbi_measures.json`: fonte única das 89 medidas DAX.
- `model/layout_paginas.md`: composição dos visuais e interações.
- `power-bi/JI_Montadora_Operacional.pbip`: projeto textual versionável.
- `power-bi/JI_Montadora_Operacional.pbix`: cópia binária para revisão no Desktop.
- `model/build_powerbi_report.js`: geração determinística das seis páginas e dos drill-throughs.

## Regeneração e validação

```powershell
node bi/model/sync_powerbi_tmdl.js
node bi/model/normalize_powerbi_tmdl.js
node bi/model/build_powerbi_report.js
node bi/model/validate_powerbi_bindings.js
pnpm dlx @microsoft/powerbi-report-authoring-cli@latest validate bi/power-bi/JI_Montadora_Operacional.Report
```

Para atualizar o modelo já aberto no Desktop, executar `bi/model/refresh_powerbi_model.ps1`. O relatório detalhado de QA está em `VALIDACAO_POWER_BI_2026-08-08.md`.

## Regra central do MRP

`Necessidade de compra = máx(Necessidade O.S. pendente + Forecast firme + Forecast preditivo ponderado - Estoque disponível - Trânsito, 0)`

O estoque disponível já desconta empenhos. A necessidade pendente por O.S. também já desconta a cobertura vinculada à O.S., evitando dupla contagem. Empenhos em fluxo reduzem o disponível, mas só cobrem uma ordem após sua alocação.

## Regras industriais incorporadas

- O início de produção é a maior data entre aprovação da proposta e chegada do veículo.
- O limite técnico é de 30 dias corridos para `LAB`/`LB` e linhas vazias, `-` ou não mapeadas; para `LE`/`LAE`, é de 45 dias corridos.
- O.S. com material pendente possuem demanda ainda não empenhada e nenhum estoque disponível para o SKU.
- SKUs críticos estão pendentes em uma O.S. e sem disponibilidade; SKUs em risco possuem demanda e disponibilidade menor ou igual a zero.
- O.S. impactadas por compras aguardam SKU com quantidade efetivamente em trânsito.
- `RETIRADA` é um evento independente e não entra nos indicadores de carros finalizados ou entregues.
- Todas as visões possuem filtro de tipo de serviço para separar transformação, pós-venda, instalação de acessório, outros e não informado.

## Estado validado em 08/08/2026

O modelo aberto no Power BI foi atualizado diretamente das 14 views e reconciliado com o Supabase. Foram validados 610 veículos finalizados, 581 entregues, 40 retirados, 288 finalizações em atraso, três O.S. WIP atrasadas, 153 SKUs críticos e 170 SKUs em risco. A validação do projeto PBIR terminou sem erros estruturais.

Alertas atuais da fonte: quatro SKUs ativos sem unidade de medida, 16 linhas em trânsito sem data, quatro linhas com datas inválidas anteriores a 2000, nenhum Forecast ativo, 39 O.S. concluídas sem data final e oito durações de produção inválidas. Esses pontos ficam visíveis no relatório e não foram corrigidos artificialmente na base.
