import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import load_workbook


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from purchase_report import (  # noqa: E402
    _purchase_status,
    _rows,
    build_purchase_inspection_report,
)


class PurchaseReportStatusTests(unittest.TestCase):
    def test_each_line_keeps_its_receipt_status(self):
        for status, ordered, received, expected in [
            ("PENDENTE", 10, 0, "AGUARDANDO"),
            ("PARCIALMENTE_RECEBIDA", 10, 4, "ENTREGUE PARCIAL"),
            ("RECEBIDA", 10, 10, "ENTREGUE"),
            ("CANCELADA", 10, 4, "CANCELADA"),
            ("CONCLUIDA", 10, 0, "CONCLUÍDO"),
        ]:
            with self.subTest(status=status):
                self.assertEqual(
                    _purchase_status(status, ordered, received), expected
                )

    def test_component_receipt_can_be_partial_before_a_complete_set_arrives(self):
        self.assertEqual(
            _purchase_status("PARCIALMENTE_RECEBIDA", 10, 0), "ENTREGUE PARCIAL"
        )

    def test_quantities_are_a_fallback_when_line_status_is_missing(self):
        for received, expected in [
            (0, "AGUARDANDO"), (4, "ENTREGUE PARCIAL"), (10, "ENTREGUE")
        ]:
            with self.subTest(received=received):
                self.assertEqual(_purchase_status(None, 10, received), expected)

    def test_query_reads_receipt_status_and_dates_for_the_exact_line(self):
        class Database:
            def __init__(self):
                self.queries = []

            def execute(self, statement):
                self.queries.append(str(statement))
                return SimpleNamespace(all=lambda: [])

        db = Database()
        self.assertEqual(_rows(db), ([], []))
        query = " ".join(db.queries[0].split())
        self.assertIn("l.status as line_status", query)
        self.assertIn("l.quantidade_pedida,l.quantidade_recebida", query)
        self.assertIn("where rl.purchase_order_line_id=l.id", query)
        self.assertIn("r.status='CONFIRMADO'", query)
        self.assertIn(
            "coalesce(rl.quantidade_aprovada,0) + coalesce(rl.quantidade_condicional,0) > 0",
            query,
        )
        self.assertNotIn("partition by", query)
        self.assertNotIn("o.technical_status", query)
        self.assertNotIn("o.status", query)

    def test_export_keeps_different_lines_of_a_closed_order_independent(self):
        common = {
            "numero_oc": "PC-TESTE",
            "order_status": "CONCLUIDA",
            "technical_status": "CONCLUIDA",
            "data_emissao": date(2026, 11, 1),
            "necessidade_linha": date(2026, 11, 4),
            "quantidade_pedida": 2,
        }
        purchases = [
            dict(common, sku_codigo="ITEM-1", line_status="RECEBIDA",
                 quantidade_recebida=2, data_entregue=date(2026, 11, 5)),
            dict(common, sku_codigo="ITEM-2", line_status="PENDENTE",
                 quantidade_recebida=0, data_entregue=None),
            dict(common, sku_codigo="ITEM-3", line_status="PARCIALMENTE_RECEBIDA",
                 quantidade_recebida=1, data_entregue=date(2026, 11, 6)),
            dict(common, sku_codigo="ITEM-4", line_status="CANCELADA",
                 quantidade_recebida=0, data_entregue=None),
        ]
        with patch("purchase_report._rows", return_value=(purchases, [])):
            output, purchase_count, inspection_count = build_purchase_inspection_report(None)
        sheet = load_workbook(output)["CONTROLE DE COMPRAS"]
        self.assertEqual((purchase_count, inspection_count), (4, 0))
        self.assertEqual(
            [sheet.cell(row, 1).value for row in range(4, 8)],
            ["ENTREGUE", "AGUARDANDO", "ENTREGUE PARCIAL", "CANCELADA"],
        )
        self.assertEqual(sheet["G3"].value, "QTD.")
        self.assertEqual(sheet["H3"].value, "FALTA RECEBER")
        self.assertEqual([sheet.cell(row, 8).value for row in range(4, 8)], [0, 2, 1, 0])
        self.assertEqual(sheet["I4"].value, "ITEM-1")
        self.assertEqual(sheet["O4"].value.date(), date(2026, 11, 5))
        self.assertEqual(sheet["O6"].value.date(), date(2026, 11, 6))
        self.assertEqual((sheet["P4"].value, sheet["Q4"].value), (4, 1))
        self.assertEqual((sheet["P6"].value, sheet["Q6"].value), (5, 2))
        for row in (5, 7):
            self.assertEqual(
                tuple(sheet.cell(row, column).value for column in (15, 16, 17)),
                (None, None, None),
            )

    def test_export_updates_remaining_quantity_as_receipts_arrive(self):
        for received, expected in [(0, 0.3), (0.1, 0.2), (0.3, 0), (0.4, 0)]:
            with self.subTest(received=received):
                row = {
                    "numero_oc": "PC-TESTE",
                    "sku_codigo": "ITEM-1",
                    "quantidade_pedida": "0.300",
                    "quantidade_recebida": received,
                }
                with patch("purchase_report._rows", return_value=([row], [])):
                    output, _, _ = build_purchase_inspection_report(None)
                sheet = load_workbook(output)["CONTROLE DE COMPRAS"]
                self.assertEqual(sheet["G4"].value, 0.3)
                self.assertEqual(sheet["H4"].value, expected)
                self.assertEqual(sheet["H4"].number_format, "#,##0.000")
                self.assertEqual(sheet.auto_filter.ref, "A3:R4")


if __name__ == "__main__":
    unittest.main()
