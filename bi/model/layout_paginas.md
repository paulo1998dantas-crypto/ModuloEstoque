# Layout das páginas

Formato recomendado: 16:9, fundo claro, faixa superior com título, horário da última atualização e filtros recolhíveis. Cores semânticas: azul=informação, verde=coberto/aprovado, âmbar=atenção, vermelho=ruptura/atraso.

## 1. Visão Estoque

- Cartões: estoque atual, empenhado total, estoque disponível, SKUs zerados e consumo.
- Tabela comparativa de atual × empenhado × disponível por SKU.
- Linha temporal: entradas e consumo por mês.
- Matriz principal: SKU, descrição, grupo, localização, atual, empenhado O.S., empenhado fluxo, disponível, mínimo e status.
- Painel inferior: divergência de inventário e últimos movimentos.
- Drill-through SKU: histórico de movimentos e empenhos abertos daquele material.

## 2. Visão PCP

- Cartões: O.S. no WIP, O.S. em produção, avanço médio, O.S. atrasadas e O.S. com material pendente.
- Funil/faixa: Pátio → Produção → Concluída.
- Barras: O.S. por etapa atual/etapa pendente.
- Matriz de necessidade: O.S., cliente, item/chassi, SKU, setor, necessária, coberta e pendente.
- Linha: demanda por data de entrega vigente, separando O.S., Forecast firme e preditivo.
- Tabela de exceções: O.S. atrasadas, baixo avanço e materiais pendentes.

## 3. Compras e Trânsito

- Cartões: valor em trânsito, linhas em trânsito, linhas atrasadas, O.C. abertas e taxa de aprovação.
- Colunas: trânsito por semana de necessidade.
- Barras: valor/quantidade pendente por fornecedor.
- Matriz: O.C., fornecedor, SKU, pedida, recebida, pendente, data, situação e O.S.
- Qualidade: aprovado × condicional × rejeitado e divergências por fornecedor.
- Drill-through fornecedor: histórico de entregas, atraso e inspeção.

## 4. Visão MRP

- Cartões: necessidade total, em trânsito, estoque disponível, necessidade de compra e SKUs a comprar.
- Gráfico principal: colunas agrupadas por SKU com Necessidade × Disponível × Trânsito; filtro inicial `status_mrp = COMPRAR`.
- Waterfall: necessidade → estoque disponível → trânsito → saldo projetado.
- Matriz de ação: SKU, descrição, grupo, necessidade O.S., Forecast, disponível, trânsito, saldo projetado, compra e próxima remessa.
- Segmentador de cenário: Base (ponderado) e Conservador (com estoque mínimo). A primeira versão publica os dois valores lado a lado; parâmetro What-if pode ser adicionado depois.

## Filtros e interação

- Globais: período, SKU/código, grupo/categoria.
- PCP/MRP: cliente, O.S., setor.
- Compras: fornecedor, situação do trânsito, O.C.
- Selecionar um SKU deve cruzar todos os visuais da página.
- Tooltips sempre exibem fórmula curta e data/hora da atualização.
