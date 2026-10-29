# Estoque externo de espumas — PCP / Baixa externa

## Contrato

Espumas 10220272, 10220273, 10220274, 10220275 e 10220276 ficam na
B.O.M. técnica do Cadastro. Não entram na projeção operacional de compras,
recebimento, backflush, separação ou necessidades internas quando componentes
de bancos. O banco continua sendo o material comprado, recebido e necessário.
Isso não apaga movimentos internos antigos nem modifica pedidos já emitidos.

O saldo externo é um razão independente, em `erp_external_stock_movements`.
Não há escrita em `stock_balances` ou `movements` por esse serviço.
Acesso pela aba **BAIXA EXTERNA** do Suprimentos, perfis PCP e ADMIN,
conferidos também no backend Estoque. O browser usa sessão/CSRF; a integração
usa o token de backend já configurado e resolve o usuário operacional ativo.

## Movimentos

- **Saldo inicial:** uma vez por espuma, sem inventar um saldo a partir do
  estoque interno. Refere-se ao início do controle externo, não ao saldo de hoje.
- **Entrada externa:** reposição na Spandy, quantidade positiva e motivo.
- **Baixa manual avulsa:** perda ou consumo não representado por uma entrada de
  bancos no ERP. Não usar para antecipar uma entrada que depois será baixada
  automaticamente; nesse caso, aguardar o recebimento. O sistema não pode
  deduzir de um texto livre que dois lançamentos representam o mesmo evento.
- **Ajustar para saldo contado:** lança apenas a diferença entre o saldo atual
  e a contagem, sem sobrescrever/apagar histórico.
- **Banco recebido:** confirmação de uma nova inspeção baixa o consumo da
  quantidade aprovada, multiplicando todos os níveis da B.O.M. vigente do
  Cadastro na mesma transação do recebimento. Insumos-banco também consultam
  sua composição, mesmo sem explodir o banco no estoque interno.
- **Estorno:** devolve exatamente a baixa registrada na entrada original,
  usando o snapshot, não a B.O.M. eventualmente alterada depois.

Rejeitados não consomem espuma. Recebimento parcial consome só a parte
aprovada. Repetição da mesma confirmação não duplica baixa. Não há varredura
de entradas antigas nem consumo retroativo automático. Sem saldo inicial
informado, o saldo é explicitamente sinalizado como desconhecido; consumo
maior que o saldo informado é mostrado negativo, sem bloquear a entrada real
do banco nem criar reposição fictícia.

Razão append-only na aplicação, chaves únicas para comando, linha/espuma de
recebimento, saldo inicial e estorno. Transações adquirem as linhas de itens em
ordem estável para serializar movimentos concorrentes. A Data API pública não
tem acesso às tabelas (RLS e revogação de anon/authenticated).

## Publicação segura

1. Aplicar `20261029180000_external_foam_stock.sql` (não cria saldos).
2. Publicar e verificar Estoque e Suprimentos com as exclusões operacionais.
3. Só então incluir os consumos positivos de T:X nas B.O.M.s dos bancos em B.
   A planilha de 29/10 tem 341 bancos e 786 vínculos positivos. Linhas zeradas
   são preservadas sem estimar consumo; cadastros inativos não são reativados.
4. Conferir B.O.M., usuário/auditoria e tela externa sem registrar um saldo
   fictício para teste em produção.

## Validação

`python -m pytest tests/test_external_stock.py tests/test_erp_sku_resolution.py tests/test_receipt_reversal.py`

Testes usam SQLite isolado, nunca o banco real. Incluem proporcionalidade,
recursão, inspeção parcial/rejeitada, replay, estorno pós-alteração de B.O.M.,
rollback, saldo inicial/ajuste e preservação do banco na necessidade interna.
