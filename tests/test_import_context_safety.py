import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from database import Base  # noqa: E402
from models import Movement, SKU, User  # noqa: E402
from services.excel_service import import_mass_material_movements  # noqa: E402


class ImportContextSafetyTest(unittest.TestCase):
    def setUp(self):
        self.previous_flag = os.environ.get("ERP_MOVEMENT_CONTEXT_ENABLED")
        os.environ["ERP_MOVEMENT_CONTEXT_ENABLED"] = "true"
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        self.user = User(
            username="import-context",
            password_hash="hash",
            role="OPERADOR",
            active=True,
        )
        self.sku = SKU(
            sku="IMPORT-CTX",
            descricao="Material importado",
            unidade="UN",
            active=True,
        )
        self.db.add_all([self.user, self.sku])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if self.previous_flag is None:
            os.environ.pop("ERP_MOVEMENT_CONTEXT_ENABLED", None)
        else:
            os.environ["ERP_MOVEMENT_CONTEXT_ENABLED"] = self.previous_flag

    def _row(self):
        return {
            "linha": 2,
            "codigo": self.sku.sku,
            "quantidade": 1,
            "numero_os": "",
            "item_os": "",
            "setor": "",
            "descricao": self.sku.descricao,
            "unidade": "UN",
        }

    def test_generated_document_is_not_accepted_as_operational_context(self):
        rejected = import_mass_material_movements(
            self.db,
            [self._row()],
            "EMPENHO",
            self.user.id,
            documento="",
        )
        self.assertEqual(0, rejected["processed"])
        self.assertTrue(
            any("Informe uma O.S. ativa" in error for error in rejected["errors"])
        )
        self.assertEqual(0, self.db.query(Movement).count())

        accepted = import_mass_material_movements(
            self.db,
            [self._row()],
            "EMPENHO",
            self.user.id,
            documento="REFERENCIA-EXPLICITA",
        )
        self.assertEqual([], accepted["errors"])
        movement = self.db.query(Movement).one()
        self.assertEqual("REFERENCIA", movement.context_kind)
        self.assertEqual("REFERENCIA-EXPLICITA", movement.reference_text)


if __name__ == "__main__":
    unittest.main()
