# Explosão no ato da exportação

O download `/erp/relatorios/compras-transito.xlsx` lê novamente as linhas de
O.C., o catálogo e a BOM vigentes. Não usa `os_componentes.json`, nem mantém
uma projeção antiga se a consulta ao Cadastro falhar.

`material_export_bom.py` é a mesma política usada pelo Estoque na exportação
de solicitações. CJ/PP dos grupos 30/20 são fantasmas: explodem recursivamente
até materiais finais. Descrições iniciadas por BCO/BANCO identificam bancos
fornecidos como unidade, inclusive se o grupo estiver 20/30. CJ BANCOS abre
em bancos, sem abrir a fabricação externa dos bancos.

Exceção operacional confirmada para porta-malas elevados PME completos:
30240037 (1 andar), 30240038 (2 andares), 30240039 (3 andares) e 30240103
(variante 2 andares). Mantêm o SKU de conjunto, inclusive quando há BOM
técnica. A exceção não se aplica a luzes, bancos CJ ou peças com PME no nome.

O trânsito mantém uma aba e as 14 colunas originais. Cada peça herda O.C.,
linha, fornecedor da O.C., remessa, destino e status. Quantidades pedida,
recebida e pendente são multiplicadas pelo fator acumulado da composição.
Peças repetidas se somam somente dentro da mesma linha de origem. Não se
fundem pedidos/remessas; o número de linha continua referenciando a O.C.

Falha de consulta, SKU ausente, descrição/unidade ausente, CJ/PP sem BOM,
ciclo, quantidade inválida, componente inativo ou pendência inconsistente
interrompem o download com erro explícito. Um item direto histórico inativo
pode continuar na fila; não se inventa composição nem se omite a linha.
Nenhuma O.C., solicitação, recebimento ou saldo é alterado pela exportação.

Os testes cobrem os cinco conjuntos SALT 30120029–30120033, bancos, PP
aninhado, recebimento parcial, soma de peças compartilhadas, remessas
distintas e erros que devem impedir um arquivo parcial. A confiabilidade
continua dependendo de a composição e as quantidades do Cadastro estarem
corretas; a exportação valida integridade, não substitui a engenharia.
