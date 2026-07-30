import sys
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, insert
from sqlalchemy.dialects.postgresql import psycopg
from sqlalchemy.orm import sessionmaker


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from database import Base  # noqa: E402
from models import Movement, SKU, User  # noqa: E402
from services.estoque_service import register_movement  # noqa: E402


class MovementSourceUuidTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        self.user = User(
            username="movement-source-test",
            password_hash="hash",
            role="ADM",
            active=True,
        )
        self.sku = SKU(
            sku="UUID-001",
            descricao="Material para teste UUID",
            unidade="UN",
            active=True,
        )
        self.db.add_all([self.user, self.sku])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_entrada_with_null_source_fields(self):
        movement = register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            Decimal("1.000"),
            self.user.id,
            documento="teste-entrada",
        )

        self.assertEqual("ENTRADA", movement.tipo)
        self.assertIsNone(movement.source_id)
        self.assertIsNone(movement.source_line_id)
        self.assertEqual(Decimal("1.000"), movement.saldo_posterior)

    def test_empenho_with_null_source_fields(self):
        register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            Decimal("5.000"),
            self.user.id,
        )
        movement = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            Decimal("1.000"),
            self.user.id,
            documento="teste-empenho",
        )

        self.assertEqual("EMPENHO", movement.tipo)
        self.assertIsNone(movement.source_id)
        self.assertIsNone(movement.source_line_id)
        self.assertEqual(Decimal("5.000"), movement.saldo_posterior)

    def test_uuid_strings_round_trip_on_sqlite(self):
        source_id = str(uuid4())
        source_line_id = str(uuid4())
        movement = register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            Decimal("2.000"),
            self.user.id,
            commit=False,
        )
        movement.source_type = "GOODS_RECEIPT"
        movement.source_id = source_id
        movement.source_line_id = source_line_id
        self.db.commit()
        self.db.expire_all()

        persisted = self.db.get(Movement, movement.id)
        self.assertEqual(source_id, persisted.source_id)
        self.assertEqual(source_line_id, persisted.source_line_id)

    def test_postgresql_insert_binds_source_fields_as_uuid(self):
        statement = insert(Movement).values(
            sku_id=1,
            tipo="ENTRADA",
            quantidade=Decimal("1.000"),
            saldo_anterior=Decimal("0.000"),
            saldo_posterior=Decimal("1.000"),
            usuario_id=1,
            source_id=None,
            source_line_id=None,
        )

        compiled = str(statement.compile(dialect=psycopg.dialect()))
        self.assertIn("%(source_id)s::UUID", compiled)
        self.assertIn("%(source_line_id)s::UUID", compiled)
        self.assertNotIn("%(source_id)s::VARCHAR", compiled)
        self.assertNotIn("%(source_line_id)s::VARCHAR", compiled)


if __name__ == "__main__":
    unittest.main()
