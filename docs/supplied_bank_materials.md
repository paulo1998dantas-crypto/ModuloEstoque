# Bancos fornecidos como unidade

SKUs cuja descrição começa com `BCO ` ou `BANCO ` são materiais controlados
como unidade. Sua BOM técnica descreve a fabricação externa e não substitui
o banco por espuma, tecido, estrutura ou capa na necessidade interna.

O relatório de necessidades mantém o banco, desconta seu empenho ativo e
também reconhece a cobertura de um conjunto de bancos empenhado. Conjuntos
`CJ BANCOS` continuam explodindo até os bancos individuais. Linhas técnicas
já renderizadas sob o banco na O.S. não viram demanda adicional; materiais
explicitamente vinculados a outro item continuam independentes.

A mesma classificação impede a explosão do banco na projeção de compras,
na cobertura de pedidos pendentes e no backflush interno, mesmo se estiver
cadastrado nos grupos 20 ou 30. A composição técnica permanece disponível
para o controle externo de materiais do fornecedor.

O trânsito do Suprimentos lista os SKUs reais das linhas de O.C.; não explode
essas linhas nem elimina bancos. Testes de regressão cobrem necessidade,
empenho direto, empenho de conjunto, material independente e exportação.
