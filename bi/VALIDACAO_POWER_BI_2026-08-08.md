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
| O.S. atrasadas pelo SLA técnico | 3 |
| O.S. com material pendente | 16 |
| SKUs com demanda MRP | 400 |
| SKUs cobertos | 193 |
| SKUs a comprar | 207 |
| SKUs em risco | 170 |
| SKUs críticos | 153 |
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
| Veículos finalizados | 610 |
| Veículos entregues | 581 |
| Veículos retirados | 40 |
| Tempo médio de produção | 31,5 dias |
| Mediana de produção | 30 dias |
| Finalizados com limite técnico | 610 |
| Finalizados em atraso | 288 |
| Percentual finalizado em atraso | 47,2% |
| Finalizados sem limite técnico | 0 |

## Qualidade e segurança

- Não foram encontrados identificadores duplicados nas dimensões e fatos verificados.
- Não foram encontrados fatos órfãos em relação a SKU ou O.S.
- As fórmulas de necessidade pendente e necessidade de compra não apresentaram divergências.
- O papel `powerbi_reader` possui `USAGE` no schema `bi` e `SELECT` nas views, sem privilégios de escrita.
- As 9 unidades não vazias encontradas (`pc`, `cj`, `un`, `br`, `ch`, `m`, `kg`, `m²`, `cx`) confirmam que um total físico global seria inválido. Medidas físicas retornam vazio sem uma única unidade selecionada.
- Quatro SKUs ativos estão sem unidade de medida e foram mantidos como alerta de cadastro.
- A view histórica possui 667 linhas para 667 O.S., sem duplicação de veículo/O.S.
- As 40 retiradas foram reconciliadas pelo histórico de status e ficaram fora dos 610 finalizados e 581 entregues.
- Foi preservada uma O.S. final sem data final e oito durações negativas como alertas de qualidade; nenhuma data foi fabricada e essas durações não entram nas médias.
- A segmentação por tipo de serviço reconciliou 586 finalizados de transformação, 22 de pós-venda e dois de instalação de acessório; as 40 retiradas estão em `OUTROS`.

## Datas de compras

Quatro linhas possuem ano `0002` na origem. O relatório as classifica como **data inválida**, suprime o número de dias absurdo e não as inclui nas 22 linhas realmente atrasadas. Outras 16 linhas em trânsito não possuem data.

## Validação técnica e visual

- Atualização bem-sucedida das 28 tabelas do modelo local, incluindo tabelas de data automáticas.
- As 89 medidas DAX foram sincronizadas; os principais KPIs foram executados no modelo local após o refresh e reconciliados com consultas somente leitura ao Supabase.
- Total físico global testado como vazio; ao filtrar a unidade `pc`, a medida de estoque retornou 63.974,001.
- Validador oficial de autoria PBIR: zero erros. Permaneceram apenas dois avisos de indisponibilidade de URLs externas de schema, sem impacto na estrutura do relatório.
- Inspeção visual concluída nas páginas PCP, MRP e Histórico após a alteração; filtro de tipo de serviço, logo, datas de SLA e retiradas foram renderizados corretamente.
- Páginas ocultas de drill-through, filtros, bindings, medidas e referências foram validados estruturalmente.

## Limitações atuais da fonte

- Não há Forecast ativo no momento, portanto a demanda futura não aparece artificialmente.
- O histórico de movimentos disponível é recente e não sustenta ainda indicadores robustos de sazonalidade ou dias de cobertura.
- As quatro datas inválidas e os quatro SKUs sem unidade devem ser saneados na origem após a revisão do relatório.
- A conclusão sem data e as oito durações negativas devem ser saneadas no MES; permanecem explicitamente visíveis no BI até a correção da origem.

## Regras validadas nesta revisão

- SLA de produção em dias corridos: 30 dias para `LAB`, `LB`, `-`, vazio e demais linhas; 45 dias para `LE` e `LAE`.
- O atraso começa somente quando a data atual/finalização ultrapassa a data limite.
- Início do SLA: maior data entre aprovação da proposta e chegada do veículo.
- O.S. com material pendente: quantidade pendente positiva e estoque disponível menor ou igual a zero.
- SKU crítico: pendente em O.S. e sem estoque disponível; SKU em risco: qualquer demanda com estoque disponível menor ou igual a zero.
- O.S. impactada por compra exige SKU pendente com saldo efetivamente em trânsito.
- RETIRADA usa data própria e nunca é contabilizada como finalização ou entrega.
- `categoria_servico` permite alternar entre transformação, pós-venda, instalação de acessório, outros e não informado.
