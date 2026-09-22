import sys
from pathlib import Path
import unittest


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services.cadastro_supabase_service import _row_to_sku_data  # noqa: E402


class CadastroSupabaseMappingTests(unittest.TestCase):
    def test_detailed_group_from_cadastro_wins_over_sku_prefix(self):
        data = _row_to_sku_data(
            {
                "sku": "10180192",
                "descricao_primaria": "PP ARO JANELA",
                "category_label": "18 - REVESTIMENTO",
                "field_values": {"prefixo": "PP"},
                "form_values": {"grupo_codigo": ["10"]},
            }
        )

        self.assertEqual(data["descricao"], "PP ARO JANELA")
        self.assertEqual(data["grupo"], "10 - INSUMO")
        self.assertEqual(data["grupo_informado"], "10 - INSUMO")

    def test_missing_description_is_not_replaced_by_sku(self):
        data = _row_to_sku_data(
            {
                "sku": "10180192",
                "descricao_primaria": "",
                "descricao_secundaria": "",
                "category_label": "18 - REVESTIMENTO",
                "form_values": {"grupo_codigo": ["10"]},
                "field_values": {},
            }
        )

        self.assertEqual(data["descricao"], "")
        self.assertEqual(data["grupo"], "10 - INSUMO")
        self.assertEqual(data["grupo_informado"], "10 - INSUMO")


if __name__ == "__main__":
    unittest.main()
