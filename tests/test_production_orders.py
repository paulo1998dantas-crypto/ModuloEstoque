import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from database import Base  # noqa: E402
from models import ErpProductionOrder, Movement, SKU, StockBalance, User  # noqa: E402
from services.estoque_service import register_movement  # noqa: E402
from services.production_order_service import (  # noqa: E402
    cancel_production_order,
    commit_production_order,
    complete_production_order,
    create_production_order,
)


class ProductionOrderTest(unittest.TestCase):
    def setUp(self):
        self.old_flag = os.environ.get("ERP_MOVEMENT_CONTEXT_ENABLED")
        os.environ["ERP_MOVEMENT_CONTEXT_ENABLED"] = "true"
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        self.user = User(username="pcp-op", password_hash="hash", role="PCP", active=True)
        self.source = SKU(sku="BCO-NORMAL", descricao="Banco normal", unidade="UN", active=True)
        self.target = SKU(sku="BCO-TRILHO", descricao="Banco trilho", unidade="UN", active=True)
        self.db.add_all([self.user, self.source, self.target])
        self.db.commit()
        register_movement(self.db, self.source, "ENTRADA", "5", self.user.id, documento="SALDO-INICIAL")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if self.old_flag is None:
            os.environ.pop("ERP_MOVEMENT_CONTEXT_ENABLED", None)
        else:
            os.environ["ERP_MOVEMENT_CONTEXT_ENABLED"] = self.old_flag

    def _create(self):
        return create_production_order(
            self.db,
            {
                "idempotency_key": "op-test:normal-trilho:1",
                "target_sku": "BCO-TRILHO",
                "quantidade_planejada": "1",
                "setor": "SERRALHERIA",
                "target_snapshot": {"field_values": {"altura_pe": "240 MM"}},
                "selected_parameters": [{"key": "altura_pe", "label": "Altura do pé", "value": "240 MM"}],
                "inputs": [{"sku": "BCO-NORMAL", "quantidade": "1"}],
            },
            self.user.id,
        )["order"]

    def test_op_converts_one_reserved_source_into_one_target_exactly_once(self):
        order = self._create()
        replay = create_production_order(
            self.db,
            {
                "idempotency_key": "op-test:normal-trilho:1",
                "target_sku": "BCO-TRILHO",
                "quantidade_planejada": "1",
                "inputs": [{"sku": "BCO-NORMAL", "quantidade": "1"}],
            },
            self.user.id,
        )
        self.assertTrue(replay["replayed"])
        committed = commit_production_order(self.db, order["id"], self.user.id)
        self.assertFalse(committed.get("replayed", False))
        self.assertEqual(1, len(committed["movement_ids"]))
        commitment = self.db.get(Movement, committed["movement_ids"][0])
        self.assertEqual("EMPENHO", commitment.tipo)
        self.assertEqual("PRODUCTION_ORDER", commitment.source_type)
        self.assertEqual(order["id"], str(commitment.source_id))
        self.assertEqual(Decimal("5.000"), self.db.query(StockBalance).filter_by(sku_id=self.source.id).one().saldo_atual)

        completed = complete_production_order(self.db, order["id"], self.user.id)
        self.assertFalse(completed.get("replayed", False))
        self.assertEqual("CONCLUIDA", completed["order"]["status"])
        self.assertEqual(Decimal("4.000"), self.db.query(StockBalance).filter_by(sku_id=self.source.id).one().saldo_atual)
        self.assertEqual(Decimal("1.000"), self.db.query(StockBalance).filter_by(sku_id=self.target.id).one().saldo_atual)
        self.assertEqual(3, self.db.query(Movement).filter(Movement.source_type == "PRODUCTION_ORDER").count())
        replay_complete = complete_production_order(self.db, order["id"], self.user.id)
        self.assertTrue(replay_complete["replayed"])
        self.assertEqual(Decimal("1.000"), self.db.query(StockBalance).filter_by(sku_id=self.target.id).one().saldo_atual)

    def test_cancelling_completed_op_reverses_the_entire_conversion(self):
        order = self._create()
        commit_production_order(self.db, order["id"], self.user.id)
        complete_production_order(self.db, order["id"], self.user.id)
        cancelled = cancel_production_order(self.db, order["id"], self.user.id, "Transformação registrada em duplicidade.")
        self.assertEqual("CANCELADA", cancelled["order"]["status"])
        self.assertEqual(Decimal("5.000"), self.db.query(StockBalance).filter_by(sku_id=self.source.id).one().saldo_atual)
        self.assertEqual(Decimal("0.000"), self.db.query(StockBalance).filter_by(sku_id=self.target.id).one().saldo_atual)
        self.assertEqual("CANCELADA", self.db.get(ErpProductionOrder, order["id"]).status)

    def test_second_op_cannot_reserve_material_already_committed_by_another_op(self):
        first = self._create()
        self.assertEqual(1, len(commit_production_order(self.db, first["id"], self.user.id)["movement_ids"]))
        second = create_production_order(
            self.db,
            {
                "idempotency_key": "op-test:normal-trilho:second",
                "target_sku": "BCO-TRILHO",
                "quantidade_planejada": "5",
                "inputs": [{"sku": "BCO-NORMAL", "quantidade": "5"}],
            },
            self.user.id,
        )["order"]
        with self.assertRaisesRegex(ValueError, "Empenho bloqueado"):
            commit_production_order(self.db, second["id"], self.user.id)


if __name__ == "__main__":
    unittest.main()
