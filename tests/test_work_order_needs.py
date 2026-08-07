import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from database import Base  # noqa: E402
from models import BomComponent, Movement, SKU, User  # noqa: E402
from services.estoque_service import (  # noqa: E402
    allocate_shared_commitment_to_work_order,
    register_consumption_from_commitment,
    register_movement,
)
from services.work_order_needs_service import calculate_work_order_needs  # noqa: E402


ERP_SCHEMA = (
    "create table erp_vehicles (id text primary key,chassi text not null,marca text,modelo text,versao text)",
    "create table erp_vehicle_entries (id text primary key,vehicle_id text not null,item_number integer not null)",
    "create table erp_work_orders (id text primary key,vehicle_entry_id text not null,numero_os text not null,cliente_nome text,status text not null,technical_status text default 'ABERTA',data_comercial_prevista text)",
    "create table suprimentos_documentos (id integer primary key,tipo text,erp_work_order_id text,numero text,status text,composicao text,updated_at text)",
)


class WorkOrderNeedsTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for statement in ERP_SCHEMA:
                connection.execute(text(statement))
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        self.user = User(username="needs", password_hash="hash", role="PCP", active=True)
        self.parent = SKU(sku="CJ-001", descricao="Conjunto", unidade="CJ", active=True)
        self.child = SKU(sku="PP-001", descricao="Semiacabado", unidade="PC", active=True)
        self.leaf = SKU(sku="MP-001", descricao="Materia-prima", unidade="PC", active=True)
        self.db.add_all([self.user, self.parent, self.child, self.leaf])
        self.db.flush()
        self.db.add_all(
            [
                BomComponent(item_sku_id=self.parent.id, component_sku_id=self.child.id, quantidade=2),
                BomComponent(item_sku_id=self.child.id, component_sku_id=self.leaf.id, quantidade=3),
            ]
        )
        self.work_order_id = uuid4().hex
        vehicle_id, entry_id = uuid4().hex, uuid4().hex
        composition = json.dumps(
            [
                {"codigo": "CJ-001", "descricao": "Conjunto", "unidade": "CJ", "qtd": 1, "level": 0},
                {"codigo": "PP-001", "descricao": "Semiacabado", "unidade": "PC", "qtd": 2, "level": 1, "item": "CJ-001"},
                {"codigo": "MP-001", "descricao": "Materia-prima", "unidade": "PC", "qtd": 6, "level": 2, "item": "PP-001"},
            ]
        )
        with self.engine.begin() as connection:
            connection.execute(text("insert into erp_vehicles values (:id,:chassi,'JI','Van','V1')"), {"id": vehicle_id, "chassi": "9BRTESTE000001234"})
            connection.execute(text("insert into erp_vehicle_entries values (:id,:vehicle_id,3100)"), {"id": entry_id, "vehicle_id": vehicle_id})
            connection.execute(
                text(
                    "insert into erp_work_orders("
                    "id,vehicle_entry_id,numero_os,cliente_nome,status,technical_status,data_comercial_prevista"
                    ") values (:id,:entry_id,'3100','CLIENTE','ATIVA','ABERTA','2026-08-15')"
                ),
                {"id": self.work_order_id, "entry_id": entry_id},
            )
            connection.execute(text("insert into suprimentos_documentos values (1,'os',:work_id,'3100','emitido',:composition,'2026-08-03')"), {"work_id": self.work_order_id, "composition": composition})
        self.db.commit()
        register_movement(self.db, self.parent, "ENTRADA", 10, self.user.id)
        register_movement(self.db, self.leaf, "ENTRADA", 10, self.user.id)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _by_code(self, result):
        return {row["codigo"]: row for row in result["lines"]}

    def test_parent_commitment_covers_parent_and_recursive_bom(self):
        register_movement(
            self.db,
            self.parent,
            "EMPENHO",
            1,
            self.user.id,
            work_order_id=self.work_order_id,
        )
        result = calculate_work_order_needs(self.db, self.work_order_id)
        rows = self._by_code(result)

        self.assertEqual(Decimal("0"), rows["CJ-001"]["quantidade_pendente"])
        self.assertEqual(Decimal("0"), rows["PP-001"]["quantidade_pendente"])
        self.assertEqual(Decimal("0"), rows["MP-001"]["quantidade_pendente"])
        self.assertEqual(3, result["summary"]["covered_items"])
        self.assertEqual("2026-08-15", rows["CJ-001"]["data_entrega_os"])

    def test_related_consumption_is_not_counted_twice_and_direct_baixa_covers_need(self):
        commitment = register_movement(
            self.db,
            self.parent,
            "EMPENHO",
            Decimal("0.5"),
            self.user.id,
            work_order_id=self.work_order_id,
        )
        register_consumption_from_commitment(
            self.db, commitment, Decimal("0.5"), self.user.id
        )
        register_movement(
            self.db,
            self.leaf,
            "BAIXA",
            2,
            self.user.id,
            work_order_id=self.work_order_id,
        )
        result = calculate_work_order_needs(self.db, self.work_order_id)
        rows = self._by_code(result)

        self.assertEqual(Decimal("0.5"), rows["CJ-001"]["quantidade_pendente"])
        self.assertEqual(Decimal("1.0"), rows["PP-001"]["quantidade_pendente"])
        self.assertEqual(Decimal("1.0"), rows["MP-001"]["quantidade_pendente"])

    def test_shared_pool_is_visible_but_only_covers_need_after_admin_allocation(self):
        shared = register_movement(
            self.db,
            self.parent,
            "EMPENHO",
            1,
            self.user.id,
            setor="PRODUCAO",
        )
        before = calculate_work_order_needs(self.db, self.work_order_id)
        before_rows = self._by_code(before)

        self.assertEqual(Decimal("1"), before_rows["CJ-001"]["quantidade_pendente"])
        self.assertEqual(Decimal("1"), before_rows["CJ-001"]["saldo_fluxo_compartilhado"])
        self.assertEqual(Decimal("6"), before_rows["MP-001"]["saldo_fluxo_compartilhado"])
        self.assertEqual(shared.id, before_rows["MP-001"]["empenhos_compartilhados"][0]["movement_id"])

        baixa = allocate_shared_commitment_to_work_order(
            self.db,
            shared,
            Decimal("0.5"),
            self.user.id,
            self.work_order_id,
            "Consumo real do material compartilhado.",
            idempotency_key="shared-pool-test",
        )
        self.db.refresh(shared)
        self.assertIsNone(shared.work_order_id)
        self.assertEqual(self.work_order_id, str(baixa.work_order_id).replace("-", ""))
        self.assertEqual(shared.id, baixa.related_movement_id)
        self.assertEqual("SHARED_COMMITMENT_ALLOCATION", baixa.source_type)

        after = calculate_work_order_needs(self.db, self.work_order_id)
        after_rows = self._by_code(after)
        self.assertEqual(Decimal("0.5"), after_rows["CJ-001"]["quantidade_pendente"])
        self.assertEqual(Decimal("1.0"), after_rows["PP-001"]["quantidade_pendente"])
        self.assertEqual(Decimal("3.0"), after_rows["MP-001"]["quantidade_pendente"])
        self.assertEqual(Decimal("0.5"), after_rows["CJ-001"]["saldo_fluxo_compartilhado"])

    def test_shared_allocation_is_idempotent_and_rejects_linked_parent(self):
        shared = register_movement(
            self.db, self.leaf, "EMPENHO", 2, self.user.id, setor="PRODUCAO"
        )
        first = allocate_shared_commitment_to_work_order(
            self.db,
            shared,
            1,
            self.user.id,
            self.work_order_id,
            "Rateio controlado.",
            idempotency_key="shared-pool-retry",
        )
        replayed = allocate_shared_commitment_to_work_order(
            self.db,
            shared,
            1,
            self.user.id,
            self.work_order_id,
            "Rateio controlado.",
            idempotency_key="shared-pool-retry",
        )
        self.assertEqual(first.id, replayed.id)
        self.assertEqual(
            1,
            self.db.query(Movement)
            .filter(Movement.idempotency_key == "shared-pool-retry")
            .count(),
        )

        linked = register_movement(
            self.db,
            self.leaf,
            "EMPENHO",
            1,
            self.user.id,
            work_order_id=self.work_order_id,
        )
        with self.assertRaisesRegex(ValueError, "ja esta vinculado"):
            allocate_shared_commitment_to_work_order(
                self.db,
                linked,
                1,
                self.user.id,
                self.work_order_id,
                "Nao permitido.",
            )

    def test_technically_closed_work_order_is_excluded(self):
        self.db.execute(
            text("update erp_work_orders set technical_status='CONCLUIDA' where id=:id"),
            {"id": self.work_order_id},
        )
        self.db.commit()
        result = calculate_work_order_needs(self.db)
        self.assertEqual([], result["lines"])
        self.assertEqual(0, result["summary"]["work_orders"])


if __name__ == "__main__":
    unittest.main()
