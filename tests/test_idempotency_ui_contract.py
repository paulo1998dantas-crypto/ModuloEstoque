import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IdempotencyUiContractTest(unittest.TestCase):
    def test_stock_forms_carry_stable_command_keys(self):
        movement_form = (
            ROOT / "estoque_app" / "templates" / "movement_form.html"
        ).read_text(encoding="utf-8")
        consumption_form = (
            ROOT / "estoque_app" / "templates" / "consumption_import.html"
        ).read_text(encoding="utf-8")
        movements = (
            ROOT / "estoque_app" / "templates" / "movements.html"
        ).read_text(encoding="utf-8")
        self.assertGreaterEqual(
            movement_form.count('name="idempotency_key"'),
            4,
        )
        self.assertIn('name="idempotency_key"', consumption_form)
        self.assertIn("data-modal-idempotency-key", movements)
        self.assertIn("data-cancel-composite-operation", movements)
        self.assertIn("Cancelar conjunto", movements)
        self.assertIn("cancele pelo movimento pai", movements)

    def test_receipt_key_is_reused_until_success(self):
        template = (
            ROOT / "estoque_app" / "templates" / "erp_recebimentos.html"
        ).read_text(encoding="utf-8")
        self.assertIn("receiptKeys:new Map()", template)
        self.assertIn("state.receiptKeys.get(activeId)", template)
        self.assertIn("state.receiptKeys.delete(activeId)", template)
        self.assertNotIn("idempotency_key:crypto.randomUUID()", template)

    def test_commitment_form_always_receives_a_draft(self):
        """The shared movement template reads entry_draft in both modes."""
        source = (ROOT / "estoque_app" / "app.py").read_text(encoding="utf-8")
        start = source.index("def saida():")
        end = source.index('@app.route("/empenhos/importar"', start)
        route = source[start:end]

        self.assertIn("entry_draft = {}", route)
        self.assertIn("entry_draft=entry_draft", route)


if __name__ == "__main__":
    unittest.main()
