"""Valida baixas financeiras parciais/completas sem alterar estoque."""
import json
from datetime import date
from uuid import uuid4

from sqlalchemy import text

from database import engine
from services.erp_service import (
    purchase_order_financial_detail,
    register_purchase_order_financial_entry,
)


class RollbackSession:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def commit(self):
        return None


def main():
    order_id, line_id = str(uuid4()), str(uuid4())
    actor = "validacao-local"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            session = RollbackSession(connection)
            protected_before = {
                table: connection.execute(text(f"select count(*) from {table}")).scalar_one()
                for table in ("movements", "stock_balances", "erp_goods_receipts")
            }
            connection.execute(text("""
                insert into erp_purchase_orders(
                    id,numero_oc,categoria,fornecedor_nome,criado_por,status,
                    valor_total_pedido,idempotency_key
                ) values(
                    :id,:numero,'GERAL','VALIDAÇÃO FINANCEIRA',:actor,
                    'PARCIALMENTE_RECEBIDA',1000,:key
                )
            """), {
                "id": order_id, "numero": f"FIN-{uuid4().hex[:8]}",
                "actor": actor, "key": f"validacao-fin:{uuid4()}",
            })
            connection.execute(text("""
                insert into erp_purchase_order_lines(
                    id,purchase_order_id,numero_linha,descricao_original,unidade,
                    quantidade_pedida,quantidade_recebida,valor_unitario_pedido,status
                ) values(
                    :id,:order,1,'ITEM TESTE','UN',10,4,100,'PARCIALMENTE_RECEBIDA'
                )
            """), {"id": line_id, "order": order_id})

            partial = register_purchase_order_financial_entry(
                session,
                order_id,
                actor,
                {
                    "tipo_lancamento": "PARCIAL",
                    "data_lancamento": date(2026, 7, 29),
                    "numero_nf": "NF-PARCIAL",
                    "valor_lancado": 400,
                    "idempotency_key": f"fin:{uuid4()}",
                    "lines": [{
                        "purchase_order_line_id": line_id,
                        "quantidade_baixada": 4,
                    }],
                },
            )
            detail_partial = purchase_order_financial_detail(session, order_id)
            assert partial["financial_status"] == "PARCIALMENTE_CONCLUIDA"
            assert float(detail_partial["order"]["saldo_financeiro"]) == 600
            assert float(detail_partial["lines"][0]["quantidade_disponivel_financeiro"]) == 0

            connection.execute(text("""
                update erp_purchase_order_lines
                   set quantidade_recebida=10,status='RECEBIDA'
                 where id=:id
            """), {"id": line_id})
            connection.execute(text("""
                update erp_purchase_orders set status='RECEBIDA' where id=:id
            """), {"id": order_id})
            complete = register_purchase_order_financial_entry(
                session,
                order_id,
                actor,
                {
                    "tipo_lancamento": "COMPLETA",
                    "data_lancamento": date(2026, 7, 30),
                    "numero_nf": "NF-FINAL",
                    "valor_lancado": 600,
                    "idempotency_key": f"fin:{uuid4()}",
                    "lines": [{
                        "purchase_order_line_id": line_id,
                        "quantidade_baixada": 6,
                    }],
                },
            )
            detail_complete = purchase_order_financial_detail(session, order_id)
            assert complete["financial_status"] == "CONCLUIDA"
            assert float(detail_complete["order"]["saldo_financeiro"]) == 0
            assert len(detail_complete["financial_entries"]) == 2
            protected_after = {
                table: connection.execute(text(f"select count(*) from {table}")).scalar_one()
                for table in protected_before
            }
            assert protected_after == protected_before
            print(json.dumps({
                "status": "PASS",
                "partial_status": partial["financial_status"],
                "partial_balance": float(detail_partial["order"]["saldo_financeiro"]),
                "complete_status": complete["financial_status"],
                "final_balance": float(detail_complete["order"]["saldo_financeiro"]),
                "entries": len(detail_complete["financial_entries"]),
                "protected_counts": protected_before,
                "transaction": "ROLLBACK",
            }, ensure_ascii=False, indent=2))
        finally:
            transaction.rollback()


if __name__ == "__main__":
    main()
