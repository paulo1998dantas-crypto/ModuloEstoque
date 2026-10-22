import unittest
from unittest.mock import patch
from decimal import Decimal
from sqlalchemy import select, func, text
import test_purchase_requests as fixtures
from services import purchase_requests as pr
from purchase_request_export import build_workbook
from openpyxl import load_workbook


class PurchaseRequestAdminTests(unittest.TestCase):
    setUpBase = fixtures.PurchaseRequestTests.setUpBase
    setUp = fixtures.PurchaseRequestTests.setUp
    tearDown = fixtures.PurchaseRequestTests.tearDown
    request = fixtures.PurchaseRequestTests.request
    order = fixtures.PurchaseRequestTests.order
    work_order = fixtures.PurchaseRequestTests.work_order

    def act(self, row, action, **values):
        return pr.transition(self.db, row["id"], dict(
            action=action, version=row["version"], reason="Correção administrativa",
            **values), self.user)

    def test_admin_quantity_in_all_statuses_preserves_other_fields_and_audits(self):
        for status in ("SOLICITADA", "EM_COMPRAS", "CONCLUIDA", "CANCELADA"):
            with self.subTest(status=status):
                row = self.request()
                if status == "CONCLUIDA":
                    self.order([row["id"]])
                else:
                    self.db.execute(pr.requests.update().where(pr.requests.c.id == row["id"])
                                    .values(status=status))
                self.db.commit()
                before = pr.get(self.db, row["id"])
                self.act(before, "EDITAR_QUANTIDADE_ADMIN", quantity="3,250",
                         roles=["PCP"], purchase_order_id=None, status="SOLICITADA")
                self.db.commit()
                after = pr.get(self.db, row["id"])
                self.assertEqual("3.250", str(after["quantity"]))
                for key in before:
                    if key not in {"quantity", "version", "updated_at"}:
                        self.assertEqual(before[key], after[key], key)
                history = pr.history(self.db, row["id"])["events"]
                event = next(e for e in history if e["action"] == "EDITAR_QUANTIDADE_ADMIN")
                self.assertEqual(self.user.username, event["actor"])
                self.assertEqual("3.250", event["after_data"]["quantity"])
                self.assertEqual("2.000", event["before_data"]["quantity"])
                # Avoid duplicate pending references in the next case.
                self.act(after, "EXCLUIR_DEFINITIVO", confirmation=row["id"])
                self.db.commit()

    def test_quantity_rejects_invalid_values_without_changes(self):
        row = self.request()
        for value in ("0", "-1", "NaN", "Infinity", "1.0001", "100000000000", "abc", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.act(row, "EDITAR_QUANTIDADE_ADMIN", quantity=value)
            self.db.rollback()
        self.assertEqual(Decimal(row["quantity"]), pr.get(self.db, row["id"])["quantity"])
        self.assertEqual(1, len(pr.history(self.db, row["id"])["events"]))

    def test_only_active_admin_can_use_admin_actions_even_with_forged_payload(self):
        row = self.request()
        for actor in (self.pcp, self.operator, self.buyer):
            for action in ("EXCLUIR_DEFINITIVO", "EDITAR_QUANTIDADE_ADMIN"):
                with self.subTest(role=actor.role, action=action), self.assertRaises(PermissionError):
                    pr.transition(self.db, row["id"], dict(action=action, version=1,
                        quantity=3, confirmation=row["id"], roles=["ADMIN"]), actor)
                self.db.rollback()
        self.user.active = False
        with self.assertRaises(PermissionError):
            self.act(row, "EXCLUIR_DEFINITIVO", confirmation=row["id"])

    def test_delete_needs_current_version_and_exact_confirmation(self):
        row = self.request()
        for values in (dict(confirmation="other"), dict(confirmation=True), {},
                       dict(confirmation=row["id"], version=0)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                pr.transition(self.db, row["id"],
                    dict(action="EXCLUIR_DEFINITIVO", version=1) | values, self.user)
            self.db.rollback()
        self.assertEqual(1, len(pr.history(self.db, row["id"])["events"]))

    def test_delete_removes_row_events_references_backfill_and_excel_history_not_documents(self):
        os_id = self.work_order(3999)
        row = self.request(work_order_ids=[os_id])
        order = self.order([row["id"]])
        self.db.commit()
        completed = pr.get(self.db, row["id"])
        order_before = dict(self.db.execute(select(pr.orders)).mappings().first())
        lines_before = [dict(r) for r in self.db.execute(select(pr.lines)).mappings()]
        other = self.request(sector="ADMINISTRATIVO")
        pr.reference_backfill.create(self.engine, checkfirst=True)
        self.db.execute(pr.reference_backfill.insert().values(
            request_id=row["id"], original_reference="3999", result="VINCULADA"))
        self.db.commit()
        result = self.act(completed, "EXCLUIR_DEFINITIVO", confirmation=row["id"])
        self.db.commit()
        self.assertTrue(result["permanent"])
        for table, column in ((pr.requests, pr.requests.c.id),
                (pr.events, pr.events.c.request_id),
                (pr.request_work_orders, pr.request_work_orders.c.request_id),
                (pr.reference_backfill, pr.reference_backfill.c.request_id)):
            self.assertEqual(0, self.db.execute(select(func.count()).select_from(table)
                                              .where(column == row["id"])).scalar_one())
        self.assertEqual(order_before, dict(self.db.execute(select(pr.orders)).mappings().first()))
        self.assertEqual(lines_before, [dict(r) for r in self.db.execute(select(pr.lines)).mappings()])
        self.assertEqual(os_id, self.db.execute(select(pr.work_orders.c.id)).scalar_one())
        self.assertEqual(other["id"], pr.get(self.db, other["id"])["id"])
        with self.assertRaises(ValueError):
            pr.history(self.db, row["id"])
        book = load_workbook(build_workbook(pr.export_data(self.db, {}, self.user), {}, "ADMIN"))
        for sheet in book:
            self.assertNotIn(row["id"], str(list(sheet.values)))

    def test_postgres_admin_purge_sets_transaction_local_request_scope(self):
        class PostgreSQLSession:
            class Bind:
                class Dialect:
                    name = "postgresql"
                dialect = Dialect()
            bind = Bind()
            def __init__(self):
                self.statements = []
            def execute(self, statement, params=None):
                self.statements.append((str(statement), params))
        row = {"id": "request-admin-purge-test"}
        session = PostgreSQLSession()
        pr._authorize_admin_request_purge(session, row)
        self.assertIn("set_config('erp.purchase_request_purge_id'", session.statements[0][0])
        self.assertEqual({"request_id": row["id"]}, session.statements[0][1])

    def test_quantity_requires_reason_and_fresh_version(self):
        row = self.request()
        for values in (dict(reason=""), dict(version=0)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                pr.transition(self.db, row["id"], dict(action="EDITAR_QUANTIDADE_ADMIN",
                    version=1, quantity=3, reason="Correção") | values, self.user)
            self.db.rollback()


class PurchaseRequestAdminRouteTests(unittest.TestCase):
    setUpBase = fixtures.PurchaseRequestTests.setUpBase
    setUp = fixtures.PurchaseRequestRouteTests.setUp
    tearDown = fixtures.PurchaseRequestTests.tearDown
    request = fixtures.PurchaseRequestTests.request

    def test_local_admin_actions_require_csrf_and_actual_role(self):
        row = self.request()
        path = "/api/erp/purchase-requests/"+row["id"]+"/action"
        payload = dict(action="EXCLUIR_DEFINITIVO", version=1, confirmation=row["id"])
        self.actor = self.user
        self.assertEqual(403, self.client.post(path, json=payload).status_code)
        self.actor = self.buyer
        self.assertEqual(403, self.client.post(path, json=payload,
                         headers={"X-CSRF-Token":"csrf"}).status_code)
        self.actor = self.user
        self.assertEqual(200, self.client.post(path, json=payload,
                         headers={"X-CSRF-Token":"csrf"}).status_code)
        self.assertEqual(0, self.db.execute(select(func.count()).select_from(pr.requests)).scalar_one())

    def test_failed_delete_rolls_back_child_deletions(self):
        row = self.request()
        self.actor = self.user
        original = self.db.execute
        def fail_parent(statement, *args, **kwargs):
            if str(statement).startswith("DELETE FROM erp_purchase_requests "):
                raise RuntimeError("Falha simulada")
            return original(statement, *args, **kwargs)
        with patch.object(self.db, "execute", side_effect=fail_parent):
            response = self.client.post("/api/erp/internal/purchase-requests/"+row["id"]+"/action",
                json=dict(action="EXCLUIR_DEFINITIVO", version=1, confirmation=row["id"]))
        self.assertEqual(503, response.status_code)
        self.assertEqual(1, len(pr.history(self.db, row["id"])["events"]))
