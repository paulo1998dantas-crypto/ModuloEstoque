# Validação do Power BI — 08/08/2026

## Resultado

O cockpit industrial foi validado contra as views do Supabase e contra o modelo aberto no Power BI Desktop. O projeto PBIR não apresentou erros estruturais. Nenhuma tabela operacional, credencial ou senha foi alterada.

## Reconciliação funcional

| Indicador | Resultado validado |
|---|---:|
| SKUs ativos | 2.044 |
| SKUs ativos com saldo | 626 |
| SKUs ativos zerados | 1.411 |
| SKUs ativos com saldo comprometido | 7 |
| O.S. no WIP | 16 |
| O.S. atrasadas | 4 |
| O.S. com material pendente | 16 |
| SKUs com demanda MRP | 400 |
| SKUs cobertos | 193 |
| SKUs a comprar | 207 |
| SKUs críticos | 79 |
| SKUs urgentes, próximos 7 dias | 10 |
| O.S. impactadas por compra | 16 |
| Linhas em trânsito | 54 |
| Linhas atrasadas com data válida | 22 |
| Linhas sem data | 16 |
| Linhas com data inválida | 4 |
| O.C. abertas | 17 |
| Valor em trânsito | R$ 509.413,30 |
| Recebimentos confirmados | 31 |
| Taxa média de aprovação | 100% |
| Forecasts ativos | 0 |

## Qualidade e segurança

- Não foram encontrados identificadores duplicados nas dimensões e fatos verificados.
- Não foram encontrados fatos órfãos em relação a SKU ou O.S.
- As fórmulas de necessidade pendente e necessidade de compra não apresentaram divergências.
- O papel `powerbi_reader` possui `USAGE` no schema `bi` e `SELECT` nas views, sem privilégios de escrita.
- As 9 unidades não vazias encontradas (`pc`, `cj`, `un`, `br`, `ch`, `m`, `kg`, `m²`, `cx`) confirmam que um total físico global seria inválido. Medidas físicas retornam vazio sem uma única unidade selecionada.
- Quatro SKUs ativos estão sem unidade de medida e foram mantidos como alerta de cadastro.

## Datas de compras

Quatro linhas possuem ano `0002` na origem. O relatório as classifica como **data inválida**, suprime o número de dias absurdo e não as inclui nas 22 linhas realmente atrasadas. Outras 16 linhas em trânsito não possuem data.

## Validação técnica e visual

- Atualização bem-sucedida das 27 tabelas do modelo local, incluindo tabelas de data automáticas.
- Medidas DAX principais reconciliadas diretamente com consultas somente leitura ao Supabase.
- Total físico global testado como vazio; ao filtrar a unidade `pc`, a medida de estoque retornou 63.974,001.
- Validador oficial de autoria PBIR: zero erros. Permaneceram apenas dois avisos de indisponibilidade de URLs externas de schema, sem impacto na estrutura do relatório.
- Inspeção visual concluída nas cinco páginas visíveis: Cockpit, Estoque, PCP, Compras e MRP.
- Páginas ocultas de drill-through, filtros, bindings, medidas e referências foram validados estruturalmente.

## Limitações atuais da fonte

- Não há Forecast ativo no momento, portanto a demanda futura não aparece artificialmente.
- O histórico de movimentos disponível é recente e não sustenta ainda indicadores robustos de sazonalidade ou dias de cobertura.
- As quatro datas inválidas e os quatro SKUs sem unidade devem ser saneados na origem após a revisão do relatório.
