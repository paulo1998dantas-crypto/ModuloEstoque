"""Valida conclusões da O.C. sem persistir dados ou tocar em saldo."""
import json
from uuid import uuid4

from sqlalchemy import text

from database import engine
from services.erp_service import (
    close_purchase_order_financial,
    close_purchase_order_technical,
)


class RollbackSession:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def commit(self):
        # O teste usa uma transação externa que será sempre revertida.
        return None


def main():
    actor = "validacao-local"
    order_id = str(uuid4())
    before = {}
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            session = RollbackSession(connection)
            for table in ("movements", "stock_balances", "erp_goods_receipts"):
                before[table] = connection.execute(
                    text(f"select count(*) from {table}")
                ).scalar_one()
            connection.execute(text("""
                insert into erp_purchase_orders(
                    id,numero_oc,categoria,fornecedor_nome,criado_por,status,
                    idempotency_key
                ) values (
                    :id,:numero,'GERAL','VALIDACAO LOCAL',:actor,'EMITIDA',:key
                )
            """), {
                "id": order_id, "numero": f"VALIDA-{uuid4().hex[:8]}",
                "actor": actor, "key": f"validacao:{uuid4()}",
            })

            technical = close_purchase_order_technical(
                session, order_id, actor, "Teste sem movimento"
            )
            try:
                close_purchase_order_financial(
                    session, order_id, actor, "Deve bloquear sem recebimento"
                )
                raise AssertionError("Conclusao financeira liberada antes do Estoque.")
            except ValueError as exc:
                financial_block = str(exc)

            connection.execute(text("""
                update erp_purchase_orders set status='RECEBIDA' where id=:id
            """), {"id": order_id})
            financial = close_purchase_order_financial(
                session, order_id, actor, "Recebimento total validado"
            )
            after = {
                table: connection.execute(
                    text(f"select count(*) from {table}")
                ).scalar_one()
                for table in before
            }
            if after != before:
                raise AssertionError(
                    f"Conclusao alterou tabelas de estoque: {before} -> {after}"
                )
            audit_actions = connection.execute(text("""
                select action from erp_audit_events
                 where entity_id=:id
                 order by created_at
            """), {"id": order_id}).scalars().all()
            assert audit_actions == ["CONCLUSAO_TECNICA", "CONCLUSAO_FINANCEIRA"]
        finally:
            transaction.rollback()

    print(json.dumps({
        "status": "PASS",
        "technical": technical,
        "financial": financial,
        "financial_block": financial_block,
        "protected_counts": before,
        "audit_actions": audit_actions,
        "persisted": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
