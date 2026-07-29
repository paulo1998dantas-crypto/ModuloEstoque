"""Validação local, reversível, do fluxo O.C. -> recebimento -> saldo.

Executar somente contra o Docker local. O script cria documentos de teste,
estorna todos os recebimentos e cancela as O.C. antes de terminar.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

from database import SessionLocal
from models import SKU
from services.erp_service import (
    cancel_purchase_order,
    close_purchase_order_financial,
    confirm_receipt,
    create_purchase_order,
    reverse_receipt,
)


ACTOR = "validacao-local"


def cleanup_previous_validation_orders():
    """Close only leftovers created by this validation actor."""
    db = SessionLocal()
    cleaned = 0
    try:
        orders = db.execute(__import__("sqlalchemy").text("""
            select id
              from erp_purchase_orders
             where criado_por=:actor
               and status <> 'CANCELADA'
        """), {"actor": ACTOR}).scalars().all()
        for order_id in orders:
            receipts = db.execute(__import__("sqlalchemy").text("""
                select id
                  from erp_goods_receipts
                 where purchase_order_id=:id
                   and status='CONFIRMADO'
            """), {"id": order_id}).scalars().all()
            for receipt_id in receipts:
                reverse_receipt(
                    db, str(receipt_id), ACTOR, 1,
                    "Limpeza de validação local interrompida",
                )
            cancel_purchase_order(
                db, str(order_id), ACTOR,
                "Limpeza de validação local interrompida",
            )
            cleaned += 1
        return cleaned
    finally:
        db.close()


def make_order(sku_id, quantity):
    db = SessionLocal()
    try:
        order = create_purchase_order(db, {
            "numero_oc": f"TESTE-{uuid4().hex[:10]}", "fornecedor_nome": "VALIDACAO LOCAL",
            "idempotency_key": str(uuid4()),
            "lines": [{"sku_id": sku_id, "sku_codigo": "ERP-002", "descricao_original": "Teste local", "unidade": "UN", "quantidade_pedida": quantity, "valor_unitario_pedido": 1}],
        }, ACTOR)
        line_id = db.execute(__import__("sqlalchemy").text("select id from erp_purchase_order_lines where purchase_order_id=:id"), {"id": order["id"]}).scalar_one()
        return order["id"], str(line_id)
    finally:
        db.close()


def receive(order_id, line_id, sku_id, key, physical, approved, conditional=0, rejected=0, result="A"):
    db = SessionLocal()
    try:
        return confirm_receipt(db, {
            "purchase_order_id": order_id, "numero_nf": f"NF-{key[:8]}", "idempotency_key": key,
            "lines": [{"purchase_order_line_id": line_id, "sku_id": sku_id, "sku_codigo": "ERP-002", "quantidade_fisica": physical, "quantidade_aprovada": approved, "quantidade_condicional": conditional, "quantidade_rejeitada": rejected, "resultado_inspecao": result, "valor_unitario_real": 1}],
        }, ACTOR, 1)
    finally:
        db.close()


def reverse_and_cancel(order_id, receipt_ids, expect_financial_reopen=False):
    db = SessionLocal()
    try:
        for receipt_id in receipt_ids:
            reverse_receipt(db, receipt_id, ACTOR, 1, "Fim da validação local")
        if expect_financial_reopen:
            state = db.execute(__import__("sqlalchemy").text("""
                select status,financial_status
                  from erp_purchase_orders
                 where id=:id
            """), {"id": order_id}).first()
            if tuple(state) != ("EMITIDA", "PENDENTE"):
                raise AssertionError(
                    f"Estorno não reabriu financeiro/status físico: {tuple(state)}"
                )
            audit = db.execute(__import__("sqlalchemy").text("""
                select count(*)
                  from erp_audit_events
                 where entity_id=:id
                   and action='REABERTURA_FINANCEIRA_POR_ESTORNO'
            """), {"id": order_id}).scalar_one()
            if audit != 1:
                raise AssertionError("Reabertura financeira por estorno não foi auditada.")
        cancel_purchase_order(db, order_id, ACTOR, "Fim da validação local")
    finally:
        db.close()


def main():
    cleaned_previous = cleanup_previous_validation_orders()
    db = SessionLocal()
    try:
        sku = db.query(SKU).filter_by(sku="ERP-002").first()
        if not sku:
            raise RuntimeError("SKU ERP-002 de validação não encontrado.")
        sku_id = sku.id
        opening_balance = Decimal(str(sku.balance.saldo_atual))
    finally:
        db.close()

    results = {
        "saldo_inicial": str(opening_balance),
        "validacoes_anteriores_encerradas": cleaned_previous,
    }
    order_id, line_id = make_order(sku_id, 10)
    keys = [str(uuid4()), str(uuid4())]
    def concurrent_call(key):
        try:
            return {"ok": True, "result": receive(order_id, line_id, sku_id, key, 6, 6)}
        except Exception as exc:  # expected for one simultaneous request
            return {"ok": False, "error": str(exc)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = list(pool.map(concurrent_call, keys))
    succeeded = [item["result"] for item in concurrent if item["ok"]]
    if len(succeeded) != 1 or sum(not item["ok"] for item in concurrent) != 1:
        raise AssertionError(f"Concorrência insegura: {concurrent}")
    receipt_id = succeeded[0]["id"]
    replay = receive(order_id, line_id, sku_id, keys[0] if concurrent[0]["ok"] else keys[1], 6, 6)
    if not replay.get("replayed"):
        raise AssertionError("A mesma chave de idempotência não foi reconhecida.")
    reverse_and_cancel(order_id, [receipt_id])

    # A, AC e D: apenas A aprovado produz saldo disponível.
    inspection = []
    for result, approved, conditional, rejected in (("A", 2, 0, 0), ("AC", 0, 2, 0), ("D", 0, 0, 2)):
        order_id, line_id = make_order(sku_id, 2)
        receipt = receive(order_id, line_id, sku_id, str(uuid4()), 2, approved, conditional, rejected, result)
        db = SessionLocal()
        try:
            order_state = db.execute(__import__("sqlalchemy").text("""
                select o.status,l.quantidade_recebida
                  from erp_purchase_orders o
                  join erp_purchase_order_lines l on l.purchase_order_id=o.id
                 where o.id=:id
            """), {"id": order_id}).first()
            if result == "D" and tuple(order_state) != ("EMITIDA", Decimal("0.000")):
                raise AssertionError(
                    f"Item devolvido satisfez indevidamente a O.C.: {tuple(order_state)}"
                )
            if result == "A":
                close_purchase_order_financial(
                    db, order_id, ACTOR, "Teste de reabertura por estorno"
                )
        finally:
            db.close()
        inspection.append({"resultado": result, "receipt": receipt["id"]})
        reverse_and_cancel(
            order_id,
            [receipt["id"]],
            expect_financial_reopen=(result == "A"),
        )

    db = SessionLocal()
    try:
        final_balance = Decimal(str(db.get(SKU, sku_id).balance.saldo_atual))
    finally:
        db.close()
    if final_balance != opening_balance:
        raise AssertionError(f"Saldo alterado após estornos: {opening_balance} -> {final_balance}")
    results.update({"concorrencia": concurrent, "idempotencia": "ok", "inspecao": inspection, "saldo_final": str(final_balance), "status": "PASS"})
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
