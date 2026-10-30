# Tecidos Spandy — ampliação da Baixa externa

Complementa `external_foam_stock.md`, sem substituir o controle das espumas.

## Materiais

- 10220269: tecido liso preto, em metros.
- 10220270: tecido gofrado preto, em metros (a planilha o chama de COLORIDO;
  o código informado é a referência, sem renomear o cadastro).
- 10280062: courvin liso preto Uruguai, em metros.

O conjunto de materiais externos passa de cinco para oito SKUs, idêntico no
Estoque e no Suprimentos. Os consumos das colunas C:E da PLANILHA DE TECIDO
já são finais por banco: não multiplicar novamente pelos fatores dos títulos.

## Comportamento

Os três materiais permanecem na composição técnica, mas são excluídos da
explosão operacional interna de pedidos, inspeção, separação e necessidades.
O banco continua comprado e recebido como banco. A confirmação de uma nova
entrada aprovada consome os metros correspondentes no razão externo, junto
das espumas, proporcionalmente à quantidade efetivamente recebida. O estorno
devolve apenas o snapshot original; não recalcula recebimentos antigos.

O provisionamento no início da aplicação acrescenta apenas as configurações
ausentes em `erp_external_stock_items`, com ON CONFLICT DO NOTHING. Não cria
movimentos nem saldos iniciais, não altera localização/ativação existente e
não cria tabelas se a migração original não estiver presente. Saldo inicial,
entrada, baixa manual e ajuste continuam auditados e separados do estoque
interno. Uma baixa manual não deve repetir um recebimento automático.

## Aplicação em produção

1. Publicar a proteção dos três SKUs no Estoque e Suprimentos e verificar os
   oito materiais na Baixa externa, preservando os saldos das espumas.
2. Validar novamente a B.O.M. atual contra o plano de importação. Acrescentar
   somente os consumos positivos, sem excluir os componentes existentes.
3. Conferência de 30/10/2026: 372 bancos, 561 novos vínculos; preservar 786
   vínculos de espuma, criar 31 cabeçalhos ausentes e manter os dois bancos
   inativos. Estes números são o plano conferido, não prova de importação.
4. Registrar auditoria com o usuário responsável e conferir todos os vínculos
   após a gravação. Nunca importar somente os tecidos por uma rotina que
   substitua a B.O.M. inteira.
5. Informar apenas os saldos reais da Spandy. Nenhum saldo inicial é presumido
   a partir da planilha de consumo ou do estoque interno.

Quantidades aceitam seis casas decimais; ruído binário de Excel abaixo de
1e-9 pode ser normalizado (1.7999999999999998 torna-se 1.8). Valores com
precisão realmente maior exigem revisão, não arredondamento silencioso.

## Testes

`python -m pytest tests/test_external_stock.py tests/test_erp_sku_resolution.py tests/test_receipt_reversal.py`

São usados bancos isolados. Cobrem os três tecidos, recursão, frações,
preservação das espumas, replay, estorno, recebimentos históricos, demanda
interna e provisionamento idempotente sem movimentos de estoque.
