import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import select, func, text
APP = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP))
from services import purchase_requests as pr
from services.erp_service import create_purchase_order, sync_legacy_purchase_order, cancel_purchase_order
from models import User, Movement, StockBalance
import test_erp_sku_resolution as legacy

class PurchaseRequestTests(unittest.TestCase):
    setUpBase = legacy.ErpSkuResolutionTest.setUp
    tearDown = legacy.ErpSkuResolutionTest.tearDown

    def setUp(self):
        self.setUpBase()
        pr.metadata.create_all(self.engine)
        self.operator = User(username="OPERADOR",password_hash="hash",role="OPERADOR",active=True)
        self.buyer = User(username="COMPRADOR",password_hash="hash",role="COMPRADOR",active=True)
        self.pcp = User(username="PCP",password_hash="hash",role="PCP",active=True)
        self.db.add_all([self.operator,self.buyer,self.pcp])
        self.db.commit()
        self.rbac = patch("auth.shared_rbac_enabled",return_value=False)
        self.rbac.start()
        self.addCleanup(self.rbac.stop)

    def request(self, quantity="2", origin="ESTOQUE", user=None, key=None, **values):
        payload=dict(sku_codigo="MAT-001",quantity=quantity,needed_at="2026-10-15",
                     idempotency_key=key or str(uuid4()),reference="OS 3185",notes="Reposição")
        payload.update(values)
        result=pr.create(self.db,payload,user or self.operator,origin)
        self.db.commit()
        return result["request"]

    def order(self, ids, qty="2", actor=None, key=None, sku="MAT-001"):
        data=legacy.ErpSkuResolutionTest._payload(key or str(uuid4()),sku,qty)
        data["purchase_request_ids"]=ids
        return create_purchase_order(self.db,data,actor or self.buyer.username)

    def test_two_origins_and_automatic_requester(self):
        stock=self.request()
        pcp=self.request(origin="PCP",user=self.pcp)
        self.assertEqual("ESTOQUE",stock["origin"])
        self.assertEqual("PCP",pcp["origin"])
        self.assertEqual("OPERADOR",stock["requested_by"])
        self.assertEqual("SOLICITADA",stock["status"])
        self.assertEqual(1,len(pr.history(self.db,stock["id"])["events"]))

    def test_retry_is_idempotent(self):
        key=str(uuid4())
        a=self.request(key=key)
        b=self.request(key=key)
        self.assertEqual(a["id"],b["id"])
        self.assertEqual(1,self.db.execute(select(func.count()).select_from(pr.events)).scalar())

    def test_quantity_validation(self):
        for qty in ("0","-1","NaN","Infinity","0.0001","9999999999999999"):
            with self.subTest(qty=qty),self.assertRaises(ValueError):
                self.request(quantity=qty)

    def test_invalid_date_or_inactive_sku(self):
        for vals in ({"needed_at":"bad"},{"sku_codigo":"MAT-002"},{"sku_codigo":"missing"}):
            with self.subTest(vals=vals),self.assertRaises(ValueError):
                self.request(**vals)

    def test_operator_cannot_submit_for_pcp(self):
        with self.assertRaises(PermissionError): self.request(origin="PCP")

    def test_pcp_cannot_treat_workflow_even_with_legacy_broad_permissions(self):
        row=self.request()
        with self.assertRaises(PermissionError):
            pr.transition(self.db,row["id"],{"action":"ASSUMIR","version":1,"reason":"Vou comprar"},self.pcp)

    def test_buyer_assume_note_cancel_and_reopen_are_audited(self):
        row=self.request()
        for action in ("ASSUMIR","OBSERVACAO","CANCELAR","REABRIR"):
            row=pr.transition(self.db,row["id"],{"action":action,"version":row["version"],"reason":"Teste"},self.buyer)["request"]
            self.db.commit()
        self.assertEqual("SOLICITADA",row["status"])
        self.assertEqual(5,len(pr.history(self.db,row["id"])["events"]))

    def test_stale_version_rejected(self):
        row=self.request()
        pr.transition(self.db,row["id"],{"action":"ASSUMIR","version":1,"reason":"Teste"},self.buyer)
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"outro usuário"):
            pr.transition(self.db,row["id"],{"action":"CANCELAR","version":1,"reason":"Teste"},self.buyer)

    def test_no_manual_conclusion(self):
        row=self.request()
        with self.assertRaisesRegex(ValueError,"somente"):
            pr.transition(self.db,row["id"],{"action":"CONCLUIR","version":1,"reason":"Teste"},self.buyer)

    def test_confirm_order_closes_in_same_transaction_and_links_uuid(self):
        row=self.request()
        order=self.order([row["id"]])
        updated=pr.get(self.db,row["id"])
        self.assertEqual("CONCLUIDA",updated["status"])
        self.assertEqual(order["id"],updated["purchase_order_id"])
        self.assertIsNotNone(updated["completed_at"])
        listing=pr.listing(self.db,{})
        self.assertEqual("Fornecedor teste",listing["items"][0]["fornecedor_nome"])

    def test_failed_conversion_does_not_create_order_or_conclude(self):
        row=self.request(quantity="4")
        with self.assertRaisesRegex(ValueError,"quantidade"):
            self.order([row["id"]],qty="2")
        self.db.rollback()
        self.assertEqual("SOLICITADA",pr.get(self.db,row["id"])["status"])
        self.assertEqual(0,self.db.execute(text("select count(*) from erp_purchase_orders")).scalar())

    def test_wrong_sku_is_rejected(self):
        row=self.request()
        with self.assertRaises(ValueError): self.order([row["id"]],sku="OTHER")
        self.db.rollback()

    def test_sum_multiple_requests_same_sku(self):
        a,b=self.request(quantity="2"),self.request(quantity="3")
        with self.assertRaises(ValueError): self.order([a["id"],b["id"]],qty="4")
        self.db.rollback()
        self.order([a["id"],b["id"]],qty="5")
        self.assertEqual(2,pr.listing(self.db,{})["counts"]["CONCLUIDA"])

    def test_request_cannot_complete_in_two_orders(self):
        row=self.request()
        self.order([row["id"]])
        with self.assertRaisesRegex(ValueError,"outro pedido"): self.order([row["id"]])
        self.db.rollback()
        self.assertEqual(1,self.db.execute(text("select count(*) from erp_purchase_orders")).scalar())

    def test_cancelled_request_cannot_convert(self):
        row=self.request()
        pr.transition(self.db,row["id"],{"action":"CANCELAR","version":1,"reason":"Teste"},self.buyer)
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"cancelada"): self.order([row["id"]])
        self.db.rollback()

    def test_order_retry_does_not_duplicate_completion_event(self):
        row=self.request()
        key=str(uuid4())
        a=self.order([row["id"]],key=key)
        b=self.order([row["id"]],key=key)
        self.assertEqual(a["id"],b["id"])
        self.assertEqual(2,len(pr.history(self.db,row["id"])["events"]))

    def test_non_buyer_cannot_emit_linked_order(self):
        row=self.request()
        with self.assertRaises(PermissionError): self.order([row["id"]],actor=self.pcp.username)
        self.db.rollback()

    def test_edit_cannot_drop_quantity_even_if_client_omits_request_ids(self):
        row=self.request(quantity="3")
        key=str(uuid4())
        self.order([row["id"]],qty="3",key=key)
        data=legacy.ErpSkuResolutionTest._payload(key,"MAT-001",2)
        with self.assertRaisesRegex(ValueError,"quantidade"):
            sync_legacy_purchase_order(self.db,data,self.buyer.username)
        self.db.rollback()
        qty=self.db.execute(text("select quantidade_pedida from erp_purchase_order_lines")).scalar()
        self.assertEqual(3,qty)

    def test_edit_keeps_completion_date_and_audits_order_snapshot(self):
        row=self.request()
        key=str(uuid4())
        self.order([row["id"]],key=key)
        completed=pr.get(self.db,row["id"])["completed_at"]
        sync_legacy_purchase_order(self.db,legacy.ErpSkuResolutionTest._payload(key,"MAT-001",4),self.buyer.username)
        self.assertEqual(completed,pr.get(self.db,row["id"])["completed_at"])
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual("PEDIDO_ATUALIZADO",event["action"])
        self.assertEqual("Fornecedor teste",event["after_data"]["purchase_order"]["fornecedor_nome"])

    def test_order_cancellation_reopens_and_preserves_history(self):
        row=self.request()
        order=self.order([row["id"]])
        cancel_purchase_order(self.db,order["id"],self.buyer.username,"Compra incorreta")
        current=pr.get(self.db,row["id"])
        self.assertEqual("SOLICITADA",current["status"])
        self.assertIsNone(current["purchase_order_id"])
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual(order["id"],event["before_data"]["purchase_order_id"])

    def test_request_and_confirmation_do_not_change_stock(self):
        row=self.request()
        self.order([row["id"]])
        self.assertEqual(0,self.db.query(Movement).count())
        self.assertEqual(0,self.db.query(StockBalance).count())

    def test_listing_filters_and_notifications(self):
        self.request(origin="PCP",user=self.pcp,needed_at="2020-01-01")
        self.request()
        data=pr.listing(self.db,{"origin":"PCP","q":"MAT","from":"2019-01-01","to":"2021-01-01"})
        self.assertEqual(1,data["total"])
        self.assertEqual(1,data["overdue"])
        self.assertEqual(2,pr.notifications(self.db)["new"])

    def test_prepare_is_buyer_only_and_rejects_completed(self):
        row=self.request()
        with self.assertRaises(PermissionError): pr.prepare(self.db,[row["id"]],self.operator)
        self.assertEqual(1,len(pr.prepare(self.db,[row["id"]],self.buyer)["items"]))
        self.order([row["id"]])
        with self.assertRaises(ValueError): pr.prepare(self.db,[row["id"]],self.buyer)

class PurchaseRequestRouteTests(unittest.TestCase):
    # Reuse fixtures only; run route tests without inheriting the domain suite.
    setUpBase = legacy.ErpSkuResolutionTest.setUp
    tearDown = legacy.ErpSkuResolutionTest.tearDown
    request = PurchaseRequestTests.request
    def setUp(self):
        PurchaseRequestTests.setUp(self)
        from flask import Flask
        from purchase_request_routes import register
        self.web=Flask(__name__, template_folder=str(APP/"templates"))
        self.web.secret_key="tests-only"
        self.actor=self.operator
        register(self.web,lambda:self.db,lambda:self.actor,lambda:True,
                 lambda db,actor:self.actor,lambda f:f,lambda f:f)
        self.client=self.web.test_client()
        with self.client.session_transaction() as session:
            session["purchase_requests_csrf"]="csrf"

    def test_local_creation_requires_csrf_and_forces_stock_origin(self):
        payload={"sku_codigo":"MAT-001","quantity":1,"needed_at":"2026-10-15",
                 "idempotency_key":str(uuid4()),"origin":"PCP","requested_by":"FAKE"}
        denied=self.client.post("/api/erp/purchase-requests",json=payload)
        self.assertEqual(403,denied.status_code)
        response=self.client.post("/api/erp/purchase-requests",json=payload,headers={"X-CSRF-Token":"csrf"})
        self.assertEqual(200,response.status_code)
        self.assertEqual("ESTOQUE",response.json["request"]["origin"])
        self.assertEqual("OPERADOR",response.json["request"]["requested_by"])

    def test_internal_pcp_creation_checks_actual_role(self):
        payload={"sku_codigo":"MAT-001","quantity":1,"needed_at":"2026-10-15","idempotency_key":str(uuid4())}
        self.assertEqual(403,self.client.post("/api/erp/internal/purchase-requests",json=payload).status_code)
        self.actor=self.pcp
        response=self.client.post("/api/erp/internal/purchase-requests",json=payload)
        self.assertEqual(200,response.status_code)
        self.assertEqual("PCP",response.json["request"]["origin"])

    def test_internal_action_checks_buyer_in_database(self):
        row=self.request()
        path="/api/erp/internal/purchase-requests/"+row["id"]+"/action"
        payload={"action":"ASSUMIR","version":1,"reason":"Tratamento"}
        self.actor=self.pcp
        self.assertEqual(403,self.client.post(path,json=payload).status_code)
        self.actor=self.buyer
        self.assertEqual(200,self.client.post(path,json=payload).status_code)

    def test_get_history_and_active_sku_search(self):
        row=self.request()
        response=self.client.get("/api/erp/purchase-requests/"+row["id"]+"/history")
        self.assertEqual(200,response.status_code)
        self.assertEqual(1,len(response.json["events"]))
        options=self.client.get("/api/erp/purchase-requests/options?q=MAT")
        self.assertEqual(["MAT-001"],[r["sku_codigo"] for r in options.json["items"]])

    def test_invalid_payload_no_write(self):
        response=self.client.post("/api/erp/purchase-requests",json=["bad"],headers={"X-CSRF-Token":"csrf"})
        self.assertEqual(400,response.status_code)
        self.assertEqual(0,self.db.execute(select(func.count()).select_from(pr.requests)).scalar())

    def test_database_not_migrated_returns_actionable_503(self):
        pr.metadata.drop_all(self.engine)
        response=self.client.get("/api/erp/purchase-requests")
        self.assertEqual(503,response.status_code)
        self.assertIn("migração",response.json["error"])

if __name__ == "__main__": unittest.main()

