"""Purchase edits preserve historical destinations without reopening production."""
import unittest
from uuid import uuid4

from sqlalchemy import text

import test_erp_sku_resolution as fixture
from services.erp_service import _purchase_allocation, create_purchase_order, sync_legacy_purchase_order


class PurchaseAllocationHistoryTest(unittest.TestCase):
    tearDown = fixture.ErpSkuResolutionTest.tearDown

    def setUp(self):
        fixture.ErpSkuResolutionTest.setUp(self)
        self.db.execute(text("""
            create table erp_work_orders (
                id text primary key, numero_os text, vehicle_entry_id text,
                is_current boolean, technical_status text, status text
            )
        """))
        self.work_order = str(uuid4())
        self.vehicle = str(uuid4())
        self.db.execute(text("""
            insert into erp_work_orders values
            (:id,'3181',:vehicle,true,'ABERTA','ATIVA')
        """), {"id": self.work_order, "vehicle": self.vehicle})
        self.db.commit()

    def payload(self, key=None):
        payload = fixture.ErpSkuResolutionTest._payload(key or str(uuid4()), "ANTIGO", 1)
        payload.update(
            numero_oc="2831", allocation_mode="WORK_ORDER",
            work_order_id=self.work_order, allocation_reference="O.S. 3181 · histórico",
            frete="12.34", observacoes="Condições preservadas", data_necessidade="2026-10-02",
        )
        payload["lines"][0]["valor_unitario_pedido"] = "1210"
        return payload

    def close_work_order(self):
        self.db.execute(text("""
            update erp_work_orders set status='ENTREGUE',technical_status='CONCLUIDA',
                is_current=false where id=:id
        """), {"id": self.work_order})
        self.db.commit()

    def test_edit_sku_preserves_delivered_work_order_and_commercial_conditions(self):
        payload = self.payload()
        order = create_purchase_order(self.db, payload, "PAULO")
        self.close_work_order()
        historical = dict(self.db.execute(text("select * from erp_work_orders")).mappings().one())
        payload["lines"][0]["sku_codigo"] = "MAT-001"
        result = sync_legacy_purchase_order(self.db, payload, "PAULO")
        self.assertTrue(result["updated"])
        self.assertEqual(order["id"], result["id"])
        saved = self.db.execute(text("select * from erp_purchase_orders")).mappings().one()
        self.assertEqual(self.work_order, saved["work_order_id"])
        self.assertEqual(self.vehicle, saved["vehicle_entry_id"])
        self.assertEqual(payload["allocation_reference"], saved["allocation_reference"])
        self.assertEqual("2831", saved["numero_oc"])
        self.assertEqual(1210, saved["valor_total_pedido"])
        self.assertAlmostEqual(12.34, float(saved["frete"]))
        self.assertEqual("2026-10-02", saved["data_necessidade"])
        self.assertEqual(payload["observacoes"], saved["observacoes"])
        line = self.db.execute(text("select * from erp_purchase_order_lines")).mappings().one()
        self.assertEqual("MAT-001", line["sku_codigo"])
        self.assertEqual(1, line["quantidade_pedida"])
        self.assertEqual(1210, line["valor_unitario_pedido"])
        self.assertEqual(self.work_order, line["work_order_id"])
        self.assertEqual(historical, dict(self.db.execute(text("select * from erp_work_orders")).mappings().one()))
        events = self.db.execute(text("select action from erp_purchase_order_allocation_events")).scalars().all()
        self.assertEqual(["CREATED"], events)

    def test_new_purchase_cannot_select_delivered_work_order(self):
        self.close_work_order()
        with self.assertRaisesRegex(ValueError, "não está ativa"):
            create_purchase_order(self.db, self.payload(), "PAULO")
        self.assertEqual(0, self.db.execute(text("select count(*) from erp_purchase_orders")).scalar_one())

    def test_destination_change_cannot_select_delivered_work_order(self):
        self.close_work_order()
        for current in (
            {"allocation_mode": "ESTOQUE"},
            {"allocation_mode": "WORK_ORDER", "work_order_id": str(uuid4())},
            {"allocation_mode": "AG_CHEGADA", "work_order_id": self.work_order},
        ):
            with self.subTest(current=current), self.assertRaisesRegex(ValueError, "não está ativa"):
                _purchase_allocation(self.db, self.payload(), current=current)

    def test_omitted_allocation_keeps_persisted_historical_link(self):
        self.close_work_order()
        current = dict(allocation_mode="WORK_ORDER", work_order_id=self.work_order,
                       vehicle_entry_id=self.vehicle, allocation_reference="O.S. 3181")
        self.assertEqual(("WORK_ORDER", self.work_order, self.vehicle, "O.S. 3181"),
                         _purchase_allocation(self.db, {}, current=current))

    def test_confirmed_receipt_still_locks_commercial_lines(self):
        payload = self.payload()
        order = create_purchase_order(self.db, payload, "PAULO")
        self.db.execute(text("""
            insert into erp_goods_receipts
            (id,purchase_order_id,origem,data_recebimento,operador,idempotency_key)
            values (:id,:order_id,'PURCHASE_ORDER',now(),'PAULO',:key)
        """), {"id": str(uuid4()), "order_id": order["id"], "key": str(uuid4())})
        self.db.commit()
        self.close_work_order()
        payload["lines"][0]["sku_codigo"] = "MAT-001"
        result = sync_legacy_purchase_order(self.db, payload, "PAULO")
        self.assertTrue(result["locked"])
        line = self.db.execute(text("select * from erp_purchase_order_lines")).mappings().one()
        self.assertEqual("ANTIGO", line["sku_codigo"])
        self.assertEqual(1210, line["valor_unitario_pedido"])

    def test_real_destination_change_to_active_work_order_is_allowed(self):
        current = dict(allocation_mode="WORK_ORDER", work_order_id=str(uuid4()),
                       vehicle_entry_id=str(uuid4()))
        self.assertEqual(("WORK_ORDER", self.work_order, self.vehicle, "O.S. 3181 · histórico"),
                         _purchase_allocation(self.db, self.payload(), current=current))

    def test_same_link_ignores_incoming_vehicle_and_preserves_persisted_vehicle(self):
        current = dict(allocation_mode="WORK_ORDER", work_order_id=self.work_order,
                       vehicle_entry_id=self.vehicle, allocation_reference="O.S. 3181")
        payload = self.payload()
        payload.update(vehicle_entry_id=str(uuid4()), allocation_reference="")
        self.assertEqual(("WORK_ORDER", self.work_order, self.vehicle, "O.S. 3181"),
                         _purchase_allocation(self.db, payload, current=current))


if __name__ == "__main__":
    unittest.main()
