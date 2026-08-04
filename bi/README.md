# Power BI operacional — JI Montadora

Este diretório contém a fonte versionada do BI consultivo conectado ao Supabase. Nenhuma consulta do Power BI grava em tabelas operacionais.

## Arquitetura

- Fonte: PostgreSQL do projeto Supabase `rodtxswtqbsbtukmvobn`.
- Camada semântica SQL: schema privado `bi`.
- Segurança: grupo `powerbi_reader` sem login e sem privilégios nas tabelas `public`.
- Conexão recomendada: PostgreSQL Session Pooler, porta 5432, em modo **Importar**.
- Credenciais: cadastradas somente na interface do Power BI/Gateway; nunca no GitHub.

## Páginas

1. **Visão Estoque** — estoque físico, empenhado, disponível, consumo, entradas e inventário.
2. **Visão PCP** — necessidades reais por O.S., WIP, avanço das etapas, atraso e forecast.
3. **Compras e Trânsito** — O.C. abertas, material em trânsito, atraso, recebimento e inspeção.
4. **Visão MRP** — necessidade pendente + forecast versus estoque disponível + trânsito.

Filtros globais: período, SKU, cliente e setor. Fornecedor é global nas páginas de compras e recebimentos.

## Fontes entregues

- `sql/bi_reporting_layer.sql`: definição completa das views consultivas.
- `sql/create_powerbi_login.template.sql`: criação privada do login do Power BI.
- `sql/validate_bi_reporting_layer.sql`: reconciliações somente leitura pós-implantação.
- `power-query/conexao_power_bi.md`: conexão e consultas M.
- `model/modelo_e_relacionamentos.md`: modelo estrela e relacionamentos.
- `model/dicionario_metricas.md`: regras de negócio dos indicadores.
- `model/medidas_dax.md`: medidas DAX iniciais.
- `model/layout_paginas.md`: composição dos visuais e interações.

## Regra central do MRP

`Necessidade de compra = máx(Necessidade O.S. pendente + Forecast firme + Forecast preditivo ponderado - Estoque disponível - Trânsito, 0)`

O estoque disponível já desconta empenhos. A necessidade pendente por O.S. também já desconta a cobertura vinculada àquela O.S. Essa combinação impede que o mesmo empenho seja contado duas vezes.

Empenhos em fluxo reduzem o disponível, mas não cobrem uma O.S. específica até serem alocados. O detalhamento usa `fato_empenhos_abertos`, com uma linha por empenho, para não repetir nem somar o mesmo saldo em várias O.S.

## Estado validado em 04/08/2026

O SQL completo foi executado em transação descartável no banco real e revertido após os testes. Baseline de validação: 2.236 SKUs, 20 O.S. ativas, 1.539 empenhos abertos, 63 linhas em trânsito e 12 linhas de recebimento confirmadas. Os três Forecasts ativos atuais ainda não possuem estrutura de materiais e, corretamente, não geram demanda de SKU no MRP.
