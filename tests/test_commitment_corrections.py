import sys
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from database import Base  # noqa: E402
from models import ErpMovementReferenceHistory, Movement, SKU, User  # noqa: E402
from services.commitment_correction_service import (  # noqa: E402
    apply_commitment_corrections,
    preview_commitment_corrections,
    resolve_any_work_order_reference,
)
from services.estoque_service import (  # noqa: E402
    register_consumption_from_commitment,
    register_movement,
)


ERP_SCHEMA = (
    "create table erp_vehicles (id text primary key,chassi text not null)",
    "create table erp_vehicle_entries (id text primary key,vehicle_id text not null,item_number integer not null)",
    "create table erp_work_orders (id text primary key,vehicle_entry_id text not null,numero_os text not null,status text not null,technical_status text default 'ABERTA')",
)


class CommitmentCorrectionsTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for statement in ERP_SCHEMA:
                connection.execute(text(statement))
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        self.user = User(username="admin", password_hash="hash", role="ADM", active=True)
        self.sku = SKU(sku="MAT-001", descricao="Material", unidade="PC", active=True)
        self.other_sku = SKU(sku="MAT-002", descricao="Material corrigido", unidade="PC", active=True)
        self.db.add_all([self.user, self.sku, self.other_sku])
        self.db.commit()
        register_movement(self.db, self.sku, "ENTRADA", 30, self.user.id)

        self.chassis = "9BRTESTE000001234"
        self.old_work_order = self._add_work_order(3000, self.chassis, "FINALIZADA")
        self.new_work_order = self._add_work_order(3100, self.chassis, "ENTREGUE")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_work_order(self, number, chassis, status):
        vehicle_id, entry_id, work_order_id = uuid4().hex, uuid4().hex, uuid4().hex
        with self.engine.begin() as connection:
            connection.execute(
                text("insert into erp_vehicles values (:id,:chassi)"),
                {"id": vehicle_id, "chassi": chassis},
            )
            connection.execute(
                text("insert into erp_vehicle_entries values (:id,:vehicle_id,:item)"),
                {"id": entry_id, "vehicle_id": vehicle_id, "item": number},
            )
            connection.execute(
                text("insert into erp_work_orders values (:id,:entry_id,:numero,:status,'CONCLUIDA')"),
                {
                    "id": work_order_id,
                    "entry_id": entry_id,
                    "numero": str(number),
                    "status": status,
                },
            )
        return work_order_id

    @staticmethod
    def _row(movement, **overrides):
        row = {
            "linha": 7,
            "movement_id": movement.id,
            "codigo": movement.sku.sku,
            "quantidade_empenhada": movement.quantidade,
            "documento_empenho": movement.documento or "",
            "observacao_empenho": movement.observacao or "",
            "acao_correcao": "CORRIGIR",
            "motivo_correcao": "Reconciliacao historica",
        }
        row.update(overrides)
        return row

    def test_historical_chassis_links_to_latest_work_order_and_propagates_to_baixa(self):
        commitment = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            5,
            self.user.id,
            documento="referencia antiga",
        )
        consumption = register_consumption_from_commitment(
            self.db, commitment, 2, self.user.id
        )
        balance_before = self.sku.balance.saldo_atual
        row = self._row(
            commitment,
            documento_empenho=self.chassis.lower(),
            observacao_empenho="Vinculo corrigido pela planilha",
        )

        preview = preview_commitment_corrections(self.db, [row])
        self.assertEqual([], preview["errors"])
        self.assertIn("O.S. 3100", str(preview["operations"][0]["changes"]))
        result = apply_commitment_corrections(self.db, [row], self.user.id)

        self.db.refresh(commitment)
        self.db.refresh(consumption)
        self.assertEqual(self.new_work_order, str(commitment.work_order_id).replace("-", ""))
        self.assertEqual(commitment.work_order_id, consumption.work_order_id)
        self.assertEqual("WORK_ORDER", commitment.context_kind)
        self.assertEqual(balance_before, self.sku.balance.saldo_atual)
        self.assertEqual(1, result["linked"])
        self.assertGreaterEqual(self.db.query(ErpMovementReferenceHistory).count(), 2)

    def test_sku_and_quantity_correction_changes_reservation_not_physical_balance(self):
        commitment = register_movement(
            self.db, self.sku, "EMPENHO", 5, self.user.id, documento="SETOR"
        )
        old_balance = self.sku.balance.saldo_atual
        new_balance = register_movement(
            self.db, self.other_sku, "ENTRADA", 7, self.user.id
        ).saldo_posterior
        row = self._row(
            commitment,
            codigo="mat-002",
            quantidade_empenhada=3,
        )

        result = apply_commitment_corrections(self.db, [row], self.user.id)

        self.db.refresh(commitment)
        self.assertEqual(self.other_sku.id, commitment.sku_id)
        self.assertEqual(3, commitment.quantidade)
        self.assertEqual(old_balance, self.sku.balance.saldo_atual)
        self.assertEqual(new_balance, self.other_sku.balance.saldo_atual)
        self.assertEqual(1, result["processed"])

    def test_legacy_document_with_free_os_number_is_linked_but_purchase_order_is_not(self):
        commitment = register_movement(self.db, self.sku, "EMPENHO", 2, self.user.id)
        linked = preview_commitment_corrections(
            self.db,
            [self._row(commitment, documento_empenho="material reservado para 3100")],
        )
        purchase_order = preview_commitment_corrections(
            self.db,
            [self._row(commitment, documento_empenho="pedido O.C. 3100")],
        )

        self.assertIn("O.S. 3100", str(linked["operations"][0]["changes"]))
        self.assertNotIn("O.S. 3100", str(purchase_order["operations"][0]["changes"]))

    def test_reference_accepts_full_chassis_last_eight_and_last_four_case_insensitive(self):
        catalog = [
            {
                "id": self.old_work_order,
                "numero_os": "3000",
                "item_number": 3000,
                "numero_norm": "3000",
                "item_norm": "3000",
                "chassi_norm": self.chassis,
                "chassi_reduzido": self.chassis[-8:],
                "chassi_final": self.chassis[-4:],
            },
            {
                "id": self.new_work_order,
                "numero_os": "3100",
                "item_number": 3100,
                "numero_norm": "3100",
                "item_norm": "3100",
                "chassi_norm": self.chassis,
                "chassi_reduzido": self.chassis[-8:],
                "chassi_final": self.chassis[-4:],
            },
        ]

        references = (
            self.chassis.lower(),
            f"chassi {self.chassis[-8:].lower()}",
            f"final {self.chassis[-4:].lower()}",
        )
        for reference in references:
            with self.subTest(reference=reference):
                resolved = resolve_any_work_order_reference(catalog, reference)
                self.assertEqual(self.new_work_order, resolved["id"])

    def test_explicit_os_or_item_number_has_priority_over_duplicate_chassis(self):
        catalog = [
            {
                "id": self.old_work_order,
                "numero_os": "3000",
                "item_number": 3000,
                "numero_norm": "3000",
                "item_norm": "3000",
                "chassi_norm": self.chassis,
                "chassi_reduzido": self.chassis[-8:],
                "chassi_final": self.chassis[-4:],
            },
            {
                "id": self.new_work_order,
                "numero_os": "3100",
                "item_number": 3100,
                "numero_norm": "3100",
                "item_norm": "3100",
                "chassi_norm": self.chassis,
                "chassi_reduzido": self.chassis[-8:],
                "chassi_final": self.chassis[-4:],
            },
        ]

        resolved = resolve_any_work_order_reference(
            catalog, f"O.S. 3000 / chassi {self.chassis[-8:]}"
        )

        self.assertEqual(self.old_work_order, resolved["id"])

    def test_unmatched_reference_is_not_linked_and_does_not_change_balance_or_quantity(self):
        commitment = register_movement(
            self.db, self.sku, "EMPENHO", 4, self.user.id, documento="SEM VINCULO"
        )
        balance_before = self.sku.balance.saldo_atual
        quantity_before = commitment.quantidade

        preview = preview_commitment_corrections(
            self.db,
            [self._row(commitment, documento_empenho="referencia desconhecida ZX90")],
        )
        result = apply_commitment_corrections(
            self.db,
            [self._row(commitment, documento_empenho="referencia desconhecida ZX90")],
            self.user.id,
        )

        self.db.refresh(commitment)
        self.assertIsNone(commitment.work_order_id)
        self.assertEqual(quantity_before, commitment.quantidade)
        self.assertEqual(balance_before, self.sku.balance.saldo_atual)
        self.assertEqual(0, result["linked"])
        self.assertTrue(preview["operations"][0]["warnings"])

    def test_quantity_below_consumed_and_cancel_with_consumption_are_blocked(self):
        commitment = register_movement(self.db, self.sku, "EMPENHO", 5, self.user.id)
        register_consumption_from_commitment(self.db, commitment, 2, self.user.id)

        quantity_preview = preview_commitment_corrections(
            self.db, [self._row(commitment, quantidade_empenhada=1)]
        )
        cancel_preview = preview_commitment_corrections(
            self.db, [self._row(commitment, acao_correcao="CANCELAR")]
        )

        self.assertIn("menor que o total ja baixado", quantity_preview["errors"][0])
        self.assertIn("possui baixa ativa", cancel_preview["errors"][0])

    def test_explicit_cancel_removes_commitment_without_changing_balance(self):
        commitment = register_movement(self.db, self.sku, "EMPENHO", 5, self.user.id)
        balance_before = self.sku.balance.saldo_atual
        row = self._row(commitment, acao_correcao="CANCELAR")

        result = apply_commitment_corrections(self.db, [row], self.user.id)

        self.db.refresh(commitment)
        self.assertEqual("CANCELADA", commitment.movement_status)
        self.assertEqual(balance_before, self.sku.balance.saldo_atual)
        self.assertEqual(1, result["canceled"])


if __name__ == "__main__":
    unittest.main()
