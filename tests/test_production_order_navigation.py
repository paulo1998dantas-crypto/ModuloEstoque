import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "estoque_app" / "templates" / "movement_form.html"
CONFIG = ROOT / "estoque_app" / "config.py"


class ProductionOrderNavigationTest(unittest.TestCase):
    def test_empenho_template_links_to_the_configured_supply_production_order_url(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        config = CONFIG.read_text(encoding="utf-8")
        self.assertIn("ERP_SUPRIMENTOS_URL", config)
        self.assertIn("production_orders_url", template)
        self.assertIn("Ordens de Producao / Serralheria", template)


if __name__ == "__main__":
    unittest.main()
