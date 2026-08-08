# Dicionário de métricas

| Métrica | Definição | Grão/filtro | Salvaguarda |
|---|---|---|---|
| SKUs ativos | contagem distinta de materiais ativos | SKU | consolidável |
| Estoque atual | saldo físico vigente | SKU/unidade | só soma com uma unidade |
| Empenhado total | saldo de EMPENHOS/SAÍDAS ativos menos BAIXAS relacionadas | SKU/unidade | soma O.S. e fluxo; uma unidade |
| Empenhado O.S. | empenho aberto com `work_order_id` | SKU/O.S. | reserva alocada |
| Empenhado em fluxo | empenho aberto sem `work_order_id` | SKU | pool compartilhado; não repetir por O.S. |
| Estoque disponível | `máx(estoque atual - empenhado total, 0)` | SKU/unidade | base do MRP; uma unidade |
| Movimentações ativas | contagem de movimentos vigentes | movimento/período | não mistura quantidade |
| Baixas registradas | contagem de movimentos BAIXA ativos | movimento/período | consumo físico já apropriado |
| Necessidade O.S. bruta | composição vigente das O.S. abertas | O.S./SKU | uma unidade |
| Cobertura O.S. | EMPENHO/SAÍDA vinculada e BAIXA avulsa | O.S./SKU | BAIXA filha não duplica cobertura |
| Necessidade O.S. pendente | `máx(necessidade bruta - cobertura, 0)` | O.S./SKU | necessidade real do PCP |
| Avanço da O.S. | etapas aplicáveis concluídas / etapas aplicáveis | O.S. | etapas não aplicáveis fora do denominador |
| WIP | O.S. em status ATIVA ou EM_PRODUÇÃO | O.S. | ATIVA=Pátio; EM_PRODUÇÃO=Produção |
| O.S. atrasada | O.S. WIP com entrega vigente anterior a hoje | O.S. | contagem distinta |
| Material em trânsito | saldo de linha O.C. emitida/parcial e pendente | O.C./SKU | quantidade só com uma unidade |
| Valor em trânsito | quantidade pendente × preço unitário | O.C./fornecedor | monetário, consolidável |
| Linha atrasada | em trânsito, data válida entre 01/01/2000 e ontem | linha de O.C. | datas inválidas ficam separadas |
| Linha sem data | em trânsito sem data de necessidade | linha de O.C. | exceção operacional |
| Linha com data inválida | data anterior a 01/01/2000 | linha de O.C. | não conta como atraso |
| Taxa média de aprovação | média de `aprovada / física` por linha confirmada | recebimento | evita somar unidades heterogêneas |
| Forecast ativo | Forecast com status ATIVO | Forecast | alerta se não houver estrutura |
| Necessidade total MRP | O.S. pendente + Forecast firme + preditivo ponderado | SKU/unidade | uma unidade |
| Saldo projetado | disponível + trânsito - necessidade total | SKU/unidade | negativo indica ruptura |
| Necessidade de compra | `máx(necessidade total - disponível - trânsito, 0)` | SKU/unidade | uma unidade |
| SKUs cobertos | SKUs em demanda com `status_mrp = COBERTO` | SKU | consolidável |
| SKUs a comprar | SKUs em demanda com `status_mrp = COMPRAR` | SKU | consolidável |
| Prioridade crítica | comprar e próxima necessidade vencida ou para hoje | SKU | ação imediata |
| Prioridade urgente | comprar e próxima necessidade nos próximos sete dias | SKU | horizonte operacional de uma semana |
| Prioridade atenção | comprar com necessidade posterior ou sem data | SKU | monitorar e programar |
| O.S. impactadas por compra | O.S. distintas com material pendente em SKU a comprar | O.S./SKU | consolidável por contagem distinta |
| Carros finalizados | O.S. distintas com data real de finalização | veículo/mês de finalização | não infere data ausente |
| Carros entregues | O.S. distintas com data real de entrega | veículo/mês de entrega | usa relação temporal específica de entrega |
| Início de produção histórico | maior data entre aprovação da proposta e chegada do veículo | veículo | registra também a origem escolhida |
| Tempo de produção | dias entre início histórico e finalização | veículo | duração negativa fica nula e é sinalizada |
| Finalizado em atraso | finalização posterior ao prazo vigente da O.S. | veículo | exige prazo e data final válidos |
| Percentual finalizado em atraso | finalizados em atraso / finalizados com prazo | período/segmento | O.S. sem prazo ficam fora do denominador |

## Regras de interpretação

- O relatório de necessidade do PCP trabalha com `quantidade_pendente > 0`; material totalmente coberto não aparece como ação pendente.
- Empenho não é expedição nem consumo. Apenas uma BAIXA ativa reduz o estoque físico.
- O saldo em trânsito exibido em uma linha pertence àquela linha de O.C.; totais físicos exigem filtro de unidade.
- O saldo de empenho em fluxo pertence ao SKU, não a cada O.S. O detalhamento deve usar `fato_empenhos_abertos` para evitar repetição.
- Forecast sem estrutura de materiais fica como alerta e não gera demanda fictícia no MRP.
- Datas de compra anteriores ao ano 2000 são tratadas como erro de qualidade, não como milhares de dias de atraso.
- O mês de `Carros Finalizados` vem de `data_finalizacao`; o mês de `Carros Entregues` vem de `data_entrega`. A mesma segmentação de calendário aciona a data correta para cada medida.
- A data final segue a precedência `termino_producao`, `finalizado_at` e evento explícito do histórico MES. Status final sem data permanece como alerta, sem data fabricada.
