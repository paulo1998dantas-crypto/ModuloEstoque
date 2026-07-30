"""Read-only reconciliation report for the ERP integration."""
import json
from sqlalchemy import text
from database import engine

QUERIES={
 # AJUSTE ja e gravado com sinal (positivo ou negativo); inverter o sinal aqui
 # faria um estorno parecer uma nova entrada.
 'stock_balance_vs_movements':"select s.sku,sb.saldo_atual,coalesce(sum(case when m.tipo='ENTRADA' then m.quantidade when m.tipo='BAIXA' then -m.quantidade when m.tipo='AJUSTE' then m.quantidade else 0 end),0) movimentos from skus s join stock_balances sb on sb.sku_id=s.id left join movements m on m.sku_id=s.id group by s.sku,sb.saldo_atual having sb.saldo_atual<>coalesce(sum(case when m.tipo='ENTRADA' then m.quantidade when m.tipo='BAIXA' then -m.quantidade when m.tipo='AJUSTE' then m.quantidade else 0 end),0)",
 # Linhas historicas importadas da planilha podem nao possuir SKU. Elas sao
 # registradas para consulta e propositalmente nao alteram o saldo atual.
 'receipt_lines_without_movement':"select l.id,l.sku_codigo,l.quantidade_aprovada from erp_goods_receipt_lines l left join erp_stock_receipt_links x on x.goods_receipt_line_id=l.id where l.quantidade_aprovada>0 and l.sku_id is not null and x.movement_id is null",
 'purchase_orders':"select status,count(*) from erp_purchase_orders group by status order by status",
 'work_orders':"select status,count(*) from erp_work_orders group by status order by status",
 'legacy_imports':"select source_sheet,count(*) from erp_legacy_import_records group by source_sheet order by source_sheet",
}
with engine.connect() as conn:
    report={name:[dict(row._mapping) for row in conn.execute(text(sql))] for name,sql in QUERIES.items()}
print(json.dumps(report,default=str,ensure_ascii=False,indent=2))
