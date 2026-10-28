"""Regressions for legacy EMITIDA orders with technical_status=CONCLUIDA."""
import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
import test_purchase_requests as fixtures
from models import BomComponent, Movement, SKU, StockBalance
from services import purchase_requests as pr


class TechnicalClosureTests(unittest.TestCase):
    setUpBase = fixtures.PurchaseRequestTests.setUpBase
    setUp = fixtures.PurchaseRequestTests.setUp
    tearDown = fixtures.PurchaseRequestTests.tearDown
    request = fixtures.PurchaseRequestTests.request
    order = fixtures.PurchaseRequestTests.order

    def close_legacy(self, order):
        # Deliberately retain physical status and unreceived lines, like O.C. 2707.
        self.db.execute(text("update erp_purchase_orders set technical_status='CONCLUIDA' where id=:id"),
                        {"id": order["id"]})
        self.db.commit()

    def allocate(self, row, order, quantity, replace=False):
        result = pr.transition(self.db, row["id"], dict(action="ALOCAR_PEDIDO",
            version=row["version"], reason="Distribuir atendimento", replace_allocations=replace,
            allocations=[dict(purchase_order_id=order["id"], quantity=quantity)]), self.buyer)
        self.db.commit()
        return result["request"]

    def test_closed_order_has_no_coverage_options_or_automatic_anticipation(self):
        order = self.order([], qty="5")
        self.close_legacy(order)
        row = self.request(quantity="3")
        self.assertEqual("COMPRA_NOVA", row["request_type"])
        self.assertIsNone(pr.closest_active_order(self.db, "MAT-001", date(2026, 10, 15)))
        self.assertEqual(Decimal(0), pr.order_component_pending(self.db, order["id"], "MAT-001")["pending_quantity"])
        self.assertEqual([], pr.existing_order_options(self.db, row["id"], self.buyer)["items"])
        before = pr.history(self.db, row["id"])
        with self.assertRaisesRegex(ValueError, "vigente"):
            self.allocate(row, order, "3")
        self.db.rollback()
        self.assertEqual(before, pr.history(self.db, row["id"]))

    def test_closed_kit_does_not_promise_its_components(self):
        kit = SKU(sku="CJ-TESTE", descricao="Kit", unidade="CJ", grupo="30", active=True)
        self.db.add(kit)
        self.db.flush()
        self.db.add(BomComponent(item_sku_id=kit.id, component_sku_id=self.active_sku.id, quantidade=Decimal("0.8")))
        self.db.commit()
        order = self.order([], qty="5", sku=kit.sku)
        self.close_legacy(order)
        self.assertEqual("COMPRA_NOVA", self.request()["request_type"])
        self.assertEqual({}, pr._order_component_balances(self.db, "MAT-001"))

    def test_partial_request_keeps_two_allocated_and_prepares_only_one(self):
        order = self.order([], qty="2")
        row = self.allocate(self.request(quantity="3"), order, "2")
        links = pr.active_allocations(self.db, row["id"])
        before = pr.history(self.db, row["id"])["events"]
        self.close_legacy(order)
        # Read-only projection must also stop presenting the closed anticipation.
        displayed = pr.listing(self.db, {})["items"][0]
        self.assertEqual("COMPRA_NOVA", displayed["request_type"])
        self.assertIsNone(displayed["anticipation_numero_oc"])
        listed = pr.listing(self.db, {}, self.buyer)["items"][0]
        self.db.commit()
        self.assertEqual("COMPRA_NOVA", listed["request_type"])
        self.assertEqual("EM_COMPRAS", listed["status"])
        self.assertEqual("2.000", listed["allocated_quantity"])
        self.assertEqual("1.000", listed["remaining_quantity"])
        self.assertEqual("CONCLUIDA", listed["purchase_orders"][0]["purchase_technical_status"])
        self.assertEqual(links, pr.active_allocations(self.db, row["id"]))
        after = pr.history(self.db, row["id"])["events"]
        self.assertEqual(before, after[:-1])
        self.assertIn("tecnicamente concluída", after[-1]["reason"])
        version = listed["version"]
        self.assertEqual(version, pr.listing(self.db, {}, self.buyer)["items"][0]["version"])
        prepared = pr.prepare(self.db, [row["id"]], self.buyer)["items"][0]
        self.assertEqual("1.000", pr.encode(prepared["quantity"]))
        self.assertEqual(0, self.db.query(Movement).count())
        self.assertEqual(0, self.db.query(StockBalance).count())
        self.assertEqual(("EMITIDA", "CONCLUIDA"), tuple(self.db.execute(text(
            "select status,technical_status from erp_purchase_orders where id=:id"), {"id": order["id"]}).one()))
        self.assertEqual(0, self.db.execute(text(
            "select quantidade_recebida from erp_purchase_order_lines where purchase_order_id=:id"), {"id": order["id"]}).scalar_one())

    def test_historical_options_are_capped_and_new_additions_are_rejected(self):
        order = self.order([], qty="8")
        row = self.allocate(self.request(quantity="3"), order, "2")
        self.close_legacy(order)
        option = pr.existing_order_options(self.db, row["id"], self.buyer)["items"][0]
        self.assertTrue(option["historical_only"])
        self.assertEqual("CONCLUIDA", option["technical_status"])
        self.assertEqual("2.000", option["available_quantity"])
        listed = pr.listing(self.db, {}, self.buyer)["items"][0]
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "vigente"):
            self.allocate(listed, order, "3", replace=True)
        self.db.rollback()
        with self.assertRaisesRegex(ValueError, "vigente"):
            self.allocate(listed, order, "1")
        self.db.rollback()
        kept = self.allocate(listed, order, "2", replace=True)
        self.assertEqual("2.000", kept["allocated_quantity"])
        self.assertEqual("1.000", kept["remaining_quantity"])

    def test_completed_request_and_legacy_primary_history_are_not_rewritten(self):
        order = self.order([], qty="3")
        row = self.allocate(self.request(quantity="3"), order, "3")
        before = pr.history(self.db, row["id"])["events"]
        self.close_legacy(order)
        listed = pr.listing(self.db, {}, self.buyer)["items"][0]
        self.assertEqual("CONCLUIDA", listed["status"])
        self.assertEqual(row["version"], listed["version"])
        self.assertEqual(row["completed_at"], listed["completed_at"])
        self.assertEqual(before, pr.history(self.db, row["id"])["events"])
        self.db.execute(pr.request_orders.delete().where(pr.request_orders.c.request_id == row["id"]))
        self.db.commit()
        legacy = pr.history(self.db, row["id"])["request"]["purchase_orders"][0]
        self.assertEqual("CONCLUIDA", legacy["purchase_technical_status"])
        self.assertEqual(order["id"], legacy["purchase_order_id"])

    def test_confirmation_cannot_attach_new_request_to_technically_closed_order(self):
        order = self.order([], qty="3")
        self.close_legacy(order)
        row = self.request(quantity="3")
        with self.assertRaisesRegex(ValueError, "tecnicamente concluída"):
            pr.confirm_order(self.db, order["id"], [row["id"]], self.buyer.username)
        self.db.rollback()
        self.assertEqual([], pr.active_allocations(self.db, row["id"]))

    def test_existing_completed_confirmation_can_replay_without_rewriting_history(self):
        row = self.request(quantity="3")
        order = self.order([row["id"]], qty="3")
        self.close_legacy(order)
        before = pr.history(self.db, row["id"])
        pr.confirm_order(self.db, order["id"], [row["id"]], self.buyer.username)
        self.assertEqual(before, pr.history(self.db, row["id"]))

    def test_unallocated_anticipation_reclassifies_and_does_not_confirm(self):
        order = self.order([], qty="3")
        row = self.request(quantity="3")
        self.close_legacy(order)
        current = pr.listing(self.db, {}, self.buyer)["items"][0]
        self.db.commit()
        self.assertEqual("COMPRA_NOVA", current["request_type"])
        with self.assertRaises(ValueError):
            pr.transition(self.db, row["id"], dict(action="CONFIRMAR_ANTECIPACAO",
                version=current["version"], reason="Fornecedor confirmou",
                confirmed_delivery_date="2026-10-29"), self.buyer)
        self.db.rollback()
        self.assertIsNone(pr.get(self.db, row["id"])["completed_at"])

    def test_open_partial_order_remains_eligible_and_reopening_restores_coverage(self):
        order = self.order([], qty="3")
        self.db.execute(text("update erp_purchase_orders set status='PARCIALMENTE_RECEBIDA' where id=:id"), {"id": order["id"]})
        self.db.commit()
        self.assertEqual(Decimal(3), pr.order_component_pending(self.db, order["id"], "MAT-001")["pending_quantity"])
        self.close_legacy(order)
        self.assertEqual(Decimal(0), pr.order_component_pending(self.db, order["id"], "MAT-001")["pending_quantity"])
        self.db.execute(text("update erp_purchase_orders set technical_status='ABERTA' where id=:id"), {"id": order["id"]})
        self.db.commit()
        self.assertEqual(Decimal(3), pr.order_component_pending(self.db, order["id"], "MAT-001")["pending_quantity"])


if __name__ == "__main__":
    unittest.main()
