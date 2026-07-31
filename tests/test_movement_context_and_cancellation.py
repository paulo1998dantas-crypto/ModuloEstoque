import os
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
from models import (  # noqa: E402
    ErpMovementReferenceHistory,
    Movement,
    SKU,
    StockBalance,
    User,
)
from services.estoque_service import (  # noqa: E402
    append_manual_entry_exception,
    cancel_movement,
    pending_commitment_for_movement,
    register_entry_with_backflush,
    register_consumption_from_commitment,
    register_movement,
)
from services.erp_service import work_order_materials  # noqa: E402


WORK_ORDER_SCHEMA = (
    """
    create table erp_vehicles (
        id text primary key,
        chassi text not null,
        marca text,
        modelo text,
        versao text
    )
    """,
    """
    create table erp_vehicle_entries (
        id text primary key,
        vehicle_id text not null,
        item_number integer not null
    )
    """,
    """
    create table erp_work_orders (
        id text primary key,
        vehicle_entry_id text not null,
        numero_os text not null,
        cliente_nome text,
        status text not null,
        technical_status text not null default 'ABERTA'
    )
    """,
)


class MovementContextAndCancellationTest(unittest.TestCase):
    def setUp(self):
        self.previous_flag = os.environ.get("ERP_MOVEMENT_CONTEXT_ENABLED")
        os.environ["ERP_MOVEMENT_CONTEXT_ENABLED"] = "true"
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for statement in WORK_ORDER_SCHEMA:
                connection.execute(text(statement))
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        self.user = User(
            username="operador-contexto",
            password_hash="hash",
            role="OPERADOR",
            active=True,
        )
        self.other_user = User(
            username="outro-operador",
            password_hash="hash",
            role="OPERADOR",
            active=True,
        )
        self.sku = SKU(
            sku="CTX-001",
            descricao="Material contextual",
            unidade="UN",
            active=True,
        )
        self.db.add_all([self.user, self.other_user, self.sku])
        self.db.commit()
        self.work_order_id = uuid4().hex
        vehicle_id = str(uuid4())
        entry_id = str(uuid4())
        self.db.execute(
            text(
                "insert into erp_vehicles(id,chassi,marca,modelo,versao) "
                "values(:id,'9BMTESTECHASSI123','Mercedes-Benz','Sprinter','417')"
            ),
            {"id": vehicle_id},
        )
        self.db.execute(
            text(
                "insert into erp_vehicle_entries(id,vehicle_id,item_number) "
                "values(:id,:vehicle,3113)"
            ),
            {"id": entry_id, "vehicle": vehicle_id},
        )
        self.db.execute(
            text(
                "insert into erp_work_orders("
                "id,vehicle_entry_id,numero_os,cliente_nome,status,technical_status"
                ") values(:id,:entry,'3113','Cliente teste','ATIVA','ABERTA')"
            ),
            {"id": self.work_order_id, "entry": entry_id},
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if self.previous_flag is None:
            os.environ.pop("ERP_MOVEMENT_CONTEXT_ENABLED", None)
        else:
            os.environ["ERP_MOVEMENT_CONTEXT_ENABLED"] = self.previous_flag

    def test_manual_entry_note_is_idempotent_and_backflush_parent_has_lineage(self):
        note = append_manual_entry_exception(
            "Entrada conferida",
            "Compra emergencial fora da O.C.",
        )
        note = append_manual_entry_exception(
            note,
            "Compra emergencial fora da O.C.",
        )
        self.assertEqual(1, note.count("Excecao de entrada manual:"))

        entry = register_entry_with_backflush(
            self.db,
            self.sku,
            "1",
            self.user.id,
            [],
            documento="NF-MANUAL",
            observacao=note,
        )

        self.assertEqual("MANUAL_ENTRY_BACKFLUSH", entry.source_type)
        self.assertEqual(1, entry.observacao.count("Excecao de entrada manual:"))

    def test_backflush_has_atomic_lineage_and_stable_replay_key(self):
        component = SKU(
            sku="CTX-COMP",
            descricao="Componente",
            unidade="UN",
            active=True,
        )
        self.db.add(component)
        self.db.commit()
        register_movement(
            self.db,
            component,
            "ENTRADA",
            5,
            self.user.id,
        )
        command_key = "entry-backflush:test-command"
        entry = register_entry_with_backflush(
            self.db,
            self.sku,
            1,
            self.user.id,
            [{"sku": component, "quantidade": Decimal("2")}],
            idempotency_key=command_key,
        )
        child = (
            self.db.query(Movement)
            .filter(Movement.parent_movement_id == entry.id)
            .one()
        )
        before_count = self.db.query(Movement).count()
        replayed = register_entry_with_backflush(
            self.db,
            self.sku,
            1,
            self.user.id,
            [{"sku": component, "quantidade": Decimal("2")}],
            idempotency_key=command_key,
        )

        self.assertEqual(entry.id, replayed.id)
        self.assertEqual(before_count, self.db.query(Movement).count())
        self.assertEqual(entry.operation_id, child.operation_id)
        self.assertEqual("MANUAL_ENTRY_BACKFLUSH", entry.source_type)
        self.assertEqual("BACKFLUSH_CONSUMPTION", child.source_type)

    def test_backflush_replay_rejects_divergent_parent_or_components(self):
        component = SKU(
            sku="CTX-COMP-DIVERGENT",
            descricao="Componente divergente",
            unidade="UN",
            active=True,
        )
        self.db.add(component)
        self.db.commit()
        register_movement(self.db, component, "ENTRADA", 10, self.user.id)
        key = "entry-backflush:divergent"
        register_entry_with_backflush(
            self.db,
            self.sku,
            1,
            self.user.id,
            [{"sku": component, "quantidade": Decimal("2")}],
            idempotency_key=key,
        )

        with self.assertRaisesRegex(ValueError, "outro backflush"):
            register_entry_with_backflush(
                self.db,
                self.sku,
                2,
                self.user.id,
                [{"sku": component, "quantidade": Decimal("2")}],
                idempotency_key=key,
            )
        with self.assertRaisesRegex(ValueError, "outro backflush"):
            register_entry_with_backflush(
                self.db,
                self.sku,
                1,
                self.user.id,
                [{"sku": component, "quantidade": Decimal("3")}],
                idempotency_key=key,
            )

    def test_composite_backflush_cancels_atomically_and_replays(self):
        component = SKU(
            sku="CTX-COMP-CANCEL",
            descricao="Componente para estorno",
            unidade="UN",
            active=True,
        )
        self.db.add(component)
        self.db.commit()
        register_movement(self.db, component, "ENTRADA", 5, self.user.id)
        entry = register_entry_with_backflush(
            self.db,
            self.sku,
            1,
            self.user.id,
            [{"sku": component, "quantidade": Decimal("2")}],
            idempotency_key="entry-backflush:cancel",
        )
        child = (
            self.db.query(Movement)
            .filter(Movement.parent_movement_id == entry.id)
            .one()
        )

        canceled, parent_reversal, replayed = cancel_movement(
            self.db,
            entry,
            self.user.id,
            "Operacao informada em duplicidade.",
        )

        self.assertFalse(replayed)
        self.assertEqual("CANCELADA", canceled.movement_status)
        self.assertEqual("CANCELADA", child.movement_status)
        self.assertEqual(2, canceled.canceled_operation_size)
        self.assertEqual("MOVEMENT_CANCELLATION", parent_reversal.source_type)
        child_reversal = self.db.get(Movement, child.reversal_movement_id)
        self.assertEqual(parent_reversal.id, child_reversal.parent_movement_id)
        self.assertEqual(entry.id, parent_reversal.related_movement_id)
        self.assertEqual(child.id, child_reversal.related_movement_id)
        self.assertEqual(
            Decimal("0.000"),
            self.db.query(StockBalance).filter_by(sku_id=self.sku.id).one().saldo_atual,
        )
        self.assertEqual(
            Decimal("5.000"),
            self.db.query(StockBalance).filter_by(sku_id=component.id).one().saldo_atual,
        )

        _, replay_reversal, replayed = cancel_movement(
            self.db,
            canceled,
            self.user.id,
            "Repeticao da mesma requisicao.",
        )
        self.assertTrue(replayed)
        self.assertEqual(parent_reversal.id, replay_reversal.id)
        self.assertEqual(
            2,
            self.db.query(Movement)
            .filter(Movement.source_type == "MOVEMENT_CANCELLATION")
            .count(),
        )

    def test_composite_child_partial_state_and_foreign_owner_are_blocked(self):
        component = SKU(
            sku="CTX-COMP-GUARDS",
            descricao="Componente para protecoes",
            unidade="UN",
            active=True,
        )
        self.db.add(component)
        self.db.commit()
        register_movement(self.db, component, "ENTRADA", 5, self.user.id)
        entry = register_entry_with_backflush(
            self.db,
            self.sku,
            1,
            self.user.id,
            [{"sku": component, "quantidade": Decimal("1")}],
            idempotency_key="entry-backflush:guards",
        )
        child = (
            self.db.query(Movement)
            .filter(Movement.parent_movement_id == entry.id)
            .one()
        )

        with self.assertRaisesRegex(ValueError, "movimento pai"):
            cancel_movement(
                self.db,
                child,
                self.user.id,
                "Tentativa pelo filho.",
            )
        self.db.rollback()
        entry = self.db.get(Movement, entry.id)
        child = self.db.get(Movement, child.id)
        with self.assertRaisesRegex(ValueError, "integralmente"):
            cancel_movement(
                self.db,
                entry,
                self.other_user.id,
                "Tentativa por outro operador.",
            )
        self.db.rollback()
        child = self.db.get(Movement, child.id)
        child.movement_status = "CANCELADA"
        self.db.commit()
        entry = self.db.get(Movement, entry.id)
        with self.assertRaisesRegex(ValueError, "parcial bloqueado"):
            cancel_movement(
                self.db,
                entry,
                self.user.id,
                "Estado parcial inconsistente.",
            )
        self.db.rollback()

    def test_movement_replay_rejects_different_context_or_relation(self):
        key = "movement:strict-replay"
        first = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            1,
            self.user.id,
            setor="PRODUCAO",
            require_context=True,
            source_type="MANUAL",
            idempotency_key=key,
        )
        replayed = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            1,
            self.user.id,
            setor="PRODUCAO",
            require_context=True,
            source_type="MANUAL",
            idempotency_key=key,
        )
        self.assertEqual(first.id, replayed.id)
        with self.assertRaisesRegex(ValueError, "outra movimentacao"):
            register_movement(
                self.db,
                self.sku,
                "EMPENHO",
                1,
                self.user.id,
                setor="ENGENHARIA",
                require_context=True,
                source_type="MANUAL",
                idempotency_key=key,
            )
        with self.assertRaisesRegex(ValueError, "outra movimentacao"):
            register_movement(
                self.db,
                self.sku,
                "EMPENHO",
                1,
                self.user.id,
                setor="PRODUCAO",
                require_context=True,
                source_type="IMPORT",
                idempotency_key=key,
            )

    def test_new_commitment_requires_context_and_consumption_inherits_it(self):
        with self.assertRaisesRegex(ValueError, "Informe uma O.S. ativa"):
            register_movement(
                self.db,
                self.sku,
                "EMPENHO",
                2,
                self.user.id,
                require_context=True,
            )
        self.db.rollback()

        register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            2,
            self.user.id,
        )
        commitment = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            2,
            self.user.id,
            work_order_id=self.work_order_id,
            link_updated_by=self.user.id,
            require_context=True,
        )
        consumption = register_consumption_from_commitment(
            self.db,
            commitment,
            1,
            self.user.id,
        )

        self.assertEqual("WORK_ORDER", commitment.context_kind)
        self.assertEqual(
            self.work_order_id,
            str(commitment.work_order_id).replace("-", ""),
        )
        self.assertEqual(
            self.work_order_id,
            str(consumption.work_order_id).replace("-", ""),
        )
        self.assertEqual(commitment.id, consumption.related_movement_id)

    def test_consumption_command_is_idempotent(self):
        register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            3,
            self.user.id,
        )
        commitment = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            3,
            self.user.id,
            work_order_id=self.work_order_id,
            require_context=True,
        )
        key = "commitment-consumption:stable"
        first = register_consumption_from_commitment(
            self.db,
            commitment,
            1,
            self.user.id,
            idempotency_key=key,
        )
        replayed = register_consumption_from_commitment(
            self.db,
            commitment,
            1,
            self.user.id,
            idempotency_key=key,
        )
        self.assertEqual(first.id, replayed.id)
        self.assertEqual(
            Decimal("2.000"),
            pending_commitment_for_movement(self.db, commitment),
        )
        with self.assertRaisesRegex(ValueError, "outra movimentacao"):
            register_consumption_from_commitment(
                self.db,
                commitment,
                2,
                self.user.id,
                idempotency_key=key,
            )

    def test_legacy_context_can_be_corrected_during_consumption_with_history(self):
        register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            3,
            self.user.id,
        )
        os.environ["ERP_MOVEMENT_CONTEXT_ENABLED"] = "false"
        commitment = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            3,
            self.user.id,
        )
        os.environ["ERP_MOVEMENT_CONTEXT_ENABLED"] = "true"

        with self.assertRaisesRegex(ValueError, "motivo"):
            register_consumption_from_commitment(
                self.db,
                commitment,
                1,
                self.user.id,
                work_order_id=self.work_order_id,
                correct_context=True,
            )
        self.db.rollback()

        commitment = self.db.get(Movement, commitment.id)
        consumption = register_consumption_from_commitment(
            self.db,
            commitment,
            1,
            self.user.id,
            work_order_id=self.work_order_id,
            correct_context=True,
            context_reason="Vinculo confirmado durante a baixa.",
        )
        history = self.db.query(ErpMovementReferenceHistory).one()

        self.assertEqual(
            self.work_order_id,
            str(commitment.work_order_id).replace("-", ""),
        )
        self.assertEqual(
            self.work_order_id,
            str(consumption.work_order_id).replace("-", ""),
        )
        self.assertEqual(self.user.id, history.changed_by)
        self.assertEqual("LEGACY", history.previous_context_kind)
        self.assertEqual("WORK_ORDER", history.new_context_kind)

    def test_cancellation_is_reversible_idempotent_and_owner_protected(self):
        movement = register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            5,
            self.user.id,
        )
        with self.assertRaisesRegex(ValueError, "seu usuario"):
            cancel_movement(
                self.db,
                movement,
                self.other_user.id,
                "Tentativa indevida",
            )
        self.db.rollback()

        movement = self.db.get(Movement, movement.id)
        canceled, reversal, replayed = cancel_movement(
            self.db,
            movement,
            self.user.id,
            "Entrada registrada em duplicidade.",
        )
        balance = self.db.query(StockBalance).filter_by(sku_id=self.sku.id).one()

        self.assertFalse(replayed)
        self.assertEqual("CANCELADA", canceled.movement_status)
        self.assertEqual("AJUSTE", reversal.tipo)
        self.assertEqual("MOVEMENT_CANCELLATION", reversal.source_type)
        self.assertEqual(Decimal("0.000"), balance.saldo_atual)

        _, second_reversal, second_replayed = cancel_movement(
            self.db,
            canceled,
            self.user.id,
            "Repeticao da requisicao.",
        )
        self.assertTrue(second_replayed)
        self.assertEqual(reversal.id, second_reversal.id)
        self.assertEqual(
            1,
            self.db.query(Movement)
            .filter(Movement.source_type == "MOVEMENT_CANCELLATION")
            .count(),
        )
        with self.assertRaisesRegex(ValueError, "compensatorio"):
            cancel_movement(
                self.db,
                reversal,
                self.user.id,
                "Nao permitido",
                allow_any=True,
            )
        self.db.rollback()

    def test_work_order_must_be_technically_open(self):
        self.db.execute(
            text(
                "update erp_work_orders set technical_status='CONCLUIDA' "
                "where id=:id"
            ),
            {"id": self.work_order_id},
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "nao esta ativa"):
            register_movement(
                self.db,
                self.sku,
                "EMPENHO",
                1,
                self.user.id,
                work_order_id=self.work_order_id,
                require_context=True,
            )

    def test_terminal_work_orders_cannot_be_committed(self):
        for status in ("FINALIZADA", "ENTREGUE", "RETIRADA", "CANCELADA"):
            with self.subTest(status=status):
                self.db.execute(
                    text("update erp_work_orders set status=:status where id=:id"),
                    {"id": self.work_order_id, "status": status},
                )
                self.db.commit()
                with self.assertRaisesRegex(ValueError, "nao esta ativa"):
                    register_movement(
                        self.db,
                        self.sku,
                        "EMPENHO",
                        1,
                        self.user.id,
                        work_order_id=self.work_order_id,
                        require_context=True,
                    )

    def test_commitment_with_active_consumption_cannot_be_canceled(self):
        register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            2,
            self.user.id,
        )
        commitment = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            2,
            self.user.id,
            setor="ALMOXARIFADO",
            require_context=True,
        )
        register_consumption_from_commitment(
            self.db,
            commitment,
            1,
            self.user.id,
        )

        with self.assertRaisesRegex(ValueError, "baixa ativa"):
            cancel_movement(
                self.db,
                commitment,
                self.user.id,
                "Cancelar empenho.",
            )

    def test_work_order_materials_ignores_canceled_movements(self):
        adjustment_sku = SKU(
            sku="CTX-ADJ",
            descricao="Ajuste sem consumo",
            unidade="UN",
            active=True,
        )
        self.db.add(adjustment_sku)
        self.db.commit()
        register_movement(
            self.db,
            self.sku,
            "ENTRADA",
            5,
            self.user.id,
        )
        commitment = register_movement(
            self.db,
            self.sku,
            "EMPENHO",
            4,
            self.user.id,
            work_order_id=self.work_order_id,
            require_context=True,
        )
        consumption = register_consumption_from_commitment(
            self.db,
            commitment,
            1,
            self.user.id,
        )
        cancel_movement(
            self.db,
            consumption,
            self.user.id,
            "Baixa informada incorretamente.",
        )
        register_movement(
            self.db,
            adjustment_sku,
            "AJUSTE",
            1,
            self.user.id,
            work_order_id=self.work_order_id,
            allow_negative=True,
        )

        materials = work_order_materials(self.db, self.work_order_id)

        self.assertEqual(1, len(materials["lines"]))
        self.assertEqual(
            Decimal("4"),
            Decimal(str(materials["totals"]["quantidade_empenhada"])),
        )
        self.assertEqual(
            Decimal("0"),
            Decimal(str(materials["totals"]["quantidade_baixada"])),
        )
        self.assertEqual(
            Decimal("4"),
            Decimal(str(materials["totals"]["saldo_empenhado"])),
        )


if __name__ == "__main__":
    unittest.main()
