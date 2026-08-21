import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from purchase_report import _purchase_status  # noqa: E402


class PurchaseReportStatusTests(unittest.TestCase):
    def test_technical_close_overrides_missing_physical_receipt(self):
        self.assertEqual(
            _purchase_status("EMITIDA", 10, 0, "CONCLUIDA"),
            "CONCLUÍDO",
        )

    def test_technical_close_applies_to_partially_received_order(self):
        self.assertEqual(
            _purchase_status("PARCIALMENTE_RECEBIDA", 10, 4, "CONCLUIDA"),
            "CONCLUÍDO",
        )

    def test_cancellation_keeps_precedence_over_technical_close(self):
        self.assertEqual(
            _purchase_status("CANCELADA", 10, 0, "CONCLUIDA"),
            "CANCELADA",
        )

    def test_open_orders_keep_physical_receipt_statuses(self):
        self.assertEqual(
            _purchase_status("EMITIDA", 10, 0, "ABERTA"),
            "AGUARDANDO",
        )
        self.assertEqual(
            _purchase_status("PARCIALMENTE_RECEBIDA", 10, 4, "ABERTA"),
            "ENTREGUE PARCIAL",
        )
        self.assertEqual(
            _purchase_status("RECEBIDA", 10, 10, "ABERTA"),
            "ENTREGUE",
        )


if __name__ == "__main__":
    unittest.main()
