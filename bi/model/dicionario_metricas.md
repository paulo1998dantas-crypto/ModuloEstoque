# Dicionário de métricas

| Métrica | Definição | Grão/filtro | Observação |
|---|---|---|---|
| Estoque atual | Saldo físico vigente em `stock_balances` | SKU | Não desconta reservas |
| Empenhado total | Saldo dos EMPENHOS/SAÍDAS ativos menos BAIXAS relacionadas ativas | SKU | Soma O.S. + fluxo |
| Empenhado O.S. | Empenho aberto com `work_order_id` | SKU/O.S. | Reserva já alocada |
| Empenhado em fluxo | Empenho aberto sem `work_order_id` | SKU | Pool compartilhado; não repetir por O.S. |
| Estoque disponível | `máx(estoque atual - empenhado total, 0)` | SKU | Base disponível usada pelo MRP |
| Consumo | Quantidade de movimentos BAIXA ativos | SKU/período | BAIXA efetivamente movimenta estoque |
| Entrada | Quantidade de movimentos ENTRADA ativos | SKU/período | Recebimento já apropriado ao estoque |
| Necessidade O.S. bruta | Soma da composição vigente das O.S. abertas | O.S./SKU | Inclui árvore registrada na composição |
| Cobertura O.S. | EMPENHO/SAÍDA vinculado e BAIXA avulsa; conjunto cobre a árvore BOM recursiva | O.S./SKU | BAIXA filha do empenho não duplica cobertura |
| Necessidade O.S. pendente | `máx(necessidade bruta - cobertura, 0)` | O.S./SKU | É a necessidade real do PCP |
| Avanço da O.S. | Etapas aplicáveis concluídas / etapas aplicáveis | O.S. | Etapas não aplicáveis ficam fora do denominador |
| WIP | O.S. em status ATIVA ou EM_PRODUÇÃO | O.S. | ATIVA=Pátio; EM_PRODUÇÃO=Produção |
| Trânsito | Saldo de linha O.C. emitida/parcial, tecnicamente aberta e pendente | O.C./SKU | `pedida - recebida` |
| Atrasado em trânsito | Trânsito cuja data de necessidade é anterior a hoje | O.C./SKU | Sem data fica em categoria própria |
| Recebido aprovado | Quantidade aprovada em recebimento confirmado | Recebimento/SKU | Pode ser menor que a física |
| Forecast firme | Material de Forecast ATIVO/AGUARDANDO_CHEGADA | Forecast/SKU | Só entra quando houver estrutura explodida |
| Forecast preditivo ponderado | Quantidade planejada × probabilidade | Forecast/SKU | Usado no cenário base do MRP |
| Necessidade total MRP | O.S. pendente + firme + preditivo ponderado | SKU | Não inclui estoque mínimo |
| Saldo projetado | Disponível + trânsito - necessidade total | SKU | Negativo indica ruptura projetada |
| Necessidade de compra | `máx(necessidade total - disponível - trânsito, 0)` | SKU | Resultado principal |
| Necessidade c/ mínimo | Compra + recomposição conceitual do estoque mínimo | SKU | Cenário mais conservador |

## Salvaguardas

- Quantidades de unidades diferentes não são aditivas. Cartões de quantidade só exibem valor quando houver uma única `unidade` no contexto; o consolidado usa contagem de SKUs/O.S./linhas e valor monetário.
- O relatório de necessidades pode mostrar todas as linhas ou filtrar `status_necessidade = PENDENTE`; uma linha totalmente coberta terá pendência zero.
- Empenho não é expedição nem consumo. Apenas BAIXA altera o saldo físico.
- O saldo de fluxo compartilhado nunca deve ser somado a partir de linhas por O.S.; usar `fato_empenhos_abertos` ou os agregados de `fato_estoque_atual`.
- Forecast sem `fato_forecast_necessidades` fica visível como alerta de qualidade, mas não entra artificialmente no MRP.
