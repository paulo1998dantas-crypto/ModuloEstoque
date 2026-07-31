import sys
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from services.erp_service import (  # noqa: E402
    pending_purchase_order_lines_by_sku,
    pending_purchase_orders,
    purchase_orders_dashboard,
)


PURCHASE_ORDER_SCHEMA = (
    """
    create table erp_purchase_orders (
        id text primary key,
        numero_oc text not null,
        categoria text not null,
        fornecedor_nome text not null,
        status text not null,
        technical_status text not null default 'ABERTA',
        data_emissao date,
        data_necessidade date,
        destino text not null default '',
        valor_total_pedido numeric not null default 0
    )
    """,
    """
    create table erp_purchase_order_lines (
        id text primary key,
        purchase_order_id text not null,
        numero_linha integer not null,
        sku_id integer,
        sku_codigo text,
        descricao_original text not null,
        unidade text not null default 'UN',
        quantidade_pedida numeric not null,
        quantidade_recebida numeric not null default 0,
        valor_unitario_pedido numeric not null default 0,
        status text not null
    )
    """,
)


class PendingPurchaseOrdersTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        for statement in PURCHASE_ORDER_SCHEMA:
            self.db.execute(text(statement))

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_order(self, order_id, status, technical_status="ABERTA"):
        self.db.execute(
            text(
                """
                insert into erp_purchase_orders (
                    id, numero_oc, categoria, fornecedor_nome, status, technical_status,
                    data_necessidade, destino, valor_total_pedido
                ) values (
                    :id, :numero, 'GERAL', :fornecedor, :status, :technical_status,
                    '2026-08-01', 'ESTOQUE', 100
                )
                """
            ),
            {
                "id": order_id,
                "numero": f"OC-{order_id}",
                "fornecedor": f"Fornecedor {order_id}",
                "status": status,
                "technical_status": technical_status,
            },
        )

    def add_line(self, order_id, line_number, ordered, received, status):
        self.db.execute(
            text(
                """
                insert into erp_purchase_order_lines (
                    id, purchase_order_id, numero_linha, sku_id, sku_codigo,
                    descricao_original, unidade, quantidade_pedida,
                    quantidade_recebida, valor_unitario_pedido, status
                ) values (
                    :id, :order_id, :line_number, 1, :sku, :description,
                    'UN', :ordered, :received, 10, :status
                )
                """
            ),
            {
                "id": f"{order_id}-{line_number}",
                "order_id": order_id,
                "line_number": line_number,
                "sku": f"SKU-{order_id}-{line_number}",
                "description": f"Item {order_id}-{line_number}",
                "ordered": ordered,
                "received": received,
                "status": status,
            },
        )

    def test_lists_only_active_orders_with_positive_pending_balance(self):
        self.add_order("emitted", "EMITIDA")
        self.add_line("emitted", 1, 10, 4, "PENDENTE")
        self.add_line("emitted", 2, 5, 5, "PENDENTE")

        self.add_order("partial", "PARCIALMENTE_RECEBIDA")
        # O saldo quantitativo e a fonte de verdade mesmo se o status da linha
        # estiver atrasado em relacao aos recebimentos.
        self.add_line("partial", 1, 7, 2, "RECEBIDA")

        for order_id, status in (
            ("draft", "RASCUNHO"),
            ("received", "RECEBIDA"),
            ("cancelled", "CANCELADA"),
            ("closed", "ENCERRADA_COM_SALDO"),
        ):
            self.add_order(order_id, status)
            self.add_line(order_id, 1, 3, 0, "PENDENTE")

        self.add_order("over", "EMITIDA")
        self.add_line("over", 1, 3, 4, "PARCIALMENTE_RECEBIDA")
        self.add_order("technical-closed", "CONCLUIDA", "CONCLUIDA")
        self.add_line("technical-closed", 1, 3, 0, "PENDENTE")
        self.db.commit()

        rows = pending_purchase_orders(self.db)

        self.assertEqual(
            {("emitted", "emitted-1"), ("partial", "partial-1")},
            {(row["id"], row["line_id"]) for row in rows},
        )
        self.assertTrue(
            all(
                row["status"] in {"EMITIDA", "PARCIALMENTE_RECEBIDA"}
                and Decimal(str(row["quantidade_pendente"])) > 0
                for row in rows
            )
        )
        self.assertEqual(
            {"emitted": Decimal("6"), "partial": Decimal("5")},
            {
                row["id"]: Decimal(str(row["quantidade_pendente"]))
                for row in rows
            },
        )

    def test_sku_suggestion_only_lists_receivable_matching_lines(self):
        self.add_order("matching", "EMITIDA")
        self.add_line("matching", 1, 10, 4, "PARCIALMENTE_RECEBIDA")
        self.add_order("other-sku", "EMITIDA")
        self.add_line("other-sku", 1, 3, 0, "PENDENTE")
        self.add_order("closed", "RECEBIDA")
        self.add_line("closed", 1, 9, 0, "PENDENTE")
        self.add_order("technical-closed", "CONCLUIDA", "CONCLUIDA")
        self.add_line("technical-closed", 1, 9, 0, "PENDENTE")
        self.db.execute(
            text(
                "update erp_purchase_order_lines "
                "set sku_id=77,sku_codigo='MAT-001' where purchase_order_id='matching'"
            )
        )
        self.db.execute(
            text(
                "update erp_purchase_order_lines "
                "set sku_id=88,sku_codigo='OUTRO' where purchase_order_id='other-sku'"
            )
        )
        self.db.execute(
            text(
                "update erp_purchase_order_lines "
                "set sku_id=77,sku_codigo='MAT-001' where purchase_order_id='closed'"
            )
        )
        self.db.execute(
            text(
                "update erp_purchase_order_lines "
                "set sku_id=77,sku_codigo='MAT-001' where purchase_order_id='technical-closed'"
            )
        )
        self.db.commit()

        rows = pending_purchase_order_lines_by_sku(
            self.db,
            sku_id=77,
            sku_code="MAT-001",
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("matching", rows[0]["purchase_order_id"])
        self.assertEqual(Decimal("6"), Decimal(str(rows[0]["quantidade_pendente"])))


class _FakeRow:
    def __init__(self, values):
        self._mapping = values


class _FakeResult:
    def __init__(self, rows):
        self.rows = [_FakeRow(row) for row in rows]

    def all(self):
        return self.rows


class _DashboardDatabase:
    def __init__(self, orders):
        self.orders = orders
        self.statements = []

    def execute(self, statement, _parameters=None):
        self.statements.append(str(statement))
        if len(self.statements) == 1:
            return _FakeResult(self.orders)
        return _FakeResult([])


class PurchaseOrdersDashboardTest(unittest.TestCase):
    def test_pending_metric_requires_active_status_and_positive_balance(self):
        database = _DashboardDatabase(
            [
                {
                    "status": "CONCLUIDA",
                    "quantidade_pendente": Decimal("0"),
                    "technical_status": "ABERTA",
                    "financial_status": "ABERTA",
                },
                {
                    "status": "PARCIALMENTE_RECEBIDA",
                    "quantidade_pendente": Decimal("2"),
                    "technical_status": "ABERTA",
                    "financial_status": "ABERTA",
                },
                {
                    "status": "RECEBIDA",
                    "quantidade_pendente": Decimal("3"),
                    "technical_status": "ABERTA",
                    "financial_status": "ABERTA",
                },
                {
                    "status": "EMITIDA",
                    "quantidade_pendente": Decimal("8"),
                    "technical_status": "CONCLUIDA",
                    "financial_status": "ABERTA",
                },
            ]
        )

        dashboard = purchase_orders_dashboard(database)

        self.assertEqual(4, dashboard["metrics"]["total"])
        self.assertEqual(1, dashboard["metrics"]["pendentes"])
        normalized_query = " ".join(database.statements[0].split())
        self.assertIn(
            "sum(greatest(l.quantidade_pedida-l.quantidade_recebida,0))",
            normalized_query,
        )


if __name__ == "__main__":
    unittest.main()
