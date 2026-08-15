import json
import re
import sqlite3
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from database import Base  # noqa: E402
from models import BomComponent, Movement, SKU, StockBalance, User  # noqa: E402
from services.erp_service import (  # noqa: E402
    confirm_receipt,
    create_purchase_order,
    pending_receipt_orders,
    reverse_receipt,
    reconcile_pending_purchase_line_skus,
    sync_legacy_purchase_order,
)


sqlite3.register_adapter(Decimal, float)


ERP_TEST_SCHEMA = (
    """
    create table erp_purchase_orders (
        id text primary key,
        numero_oc text not null,
        categoria text not null default 'GERAL',
        fornecedor_id text,
        fornecedor_nome text not null default '',
        data_emissao datetime,
        criado_por text not null default '',
        status text not null default 'RASCUNHO',
        destino text not null default '',
        frete numeric not null default 0,
        data_necessidade date,
        observacoes text not null default '',
        valor_total_pedido numeric not null default 0,
        version integer not null default 1,
        financial_status text not null default 'PENDENTE',
        financial_closed_at datetime,
        financial_closed_by text,
        financial_close_reason text not null default '',
        technical_status text not null default 'ABERTA',
        technical_closed_at datetime,
        technical_closed_by text,
        technical_close_reason text not null default '',
        idempotency_key text unique,
        created_at datetime not null default current_timestamp,
        updated_at datetime not null default current_timestamp
    )
    """,
    """
    create table erp_purchase_order_financial_entries (
        id text primary key,
        purchase_order_id text not null
    )
    """,
    """
    create table erp_purchase_order_lines (
        id text primary key,
        purchase_order_id text not null,
        numero_linha integer not null,
        sku_id integer,
        sku_codigo text,
        descricao_original text not null,
        unidade text not null default 'UN',
        quantidade_pedida numeric not null,
        quantidade_recebida numeric not null default 0,
        valor_unitario_pedido numeric not null default 0,
        destino text not null default '',
        cliente_id text,
        work_order_id text,
        data_necessidade date,
        status text not null default 'PENDENTE',
        unique (purchase_order_id, numero_linha)
    )
    """,
    """
    create table erp_goods_receipts (
        id text primary key,
        purchase_order_id text,
        origem text not null,
        data_recebimento datetime not null,
        fornecedor_nome text not null default '',
        numero_nf text not null default '',
        operador text not null,
        status text not null default 'CONFIRMADO',
        observacoes text not null default '',
        motivo_excecao text not null default '',
        idempotency_key text not null unique,
        confirmed_at datetime not null default current_timestamp,
        reversed_at datetime,
        created_at datetime not null default current_timestamp
    )
    """,
    """
    create table erp_goods_receipt_lines (
        id text primary key,
        goods_receipt_id text not null,
        purchase_order_line_id text,
        sku_id integer,
        sku_codigo text,
        quantidade_esperada numeric not null default 0,
        quantidade_recebida_anterior numeric not null default 0,
        saldo_pendente numeric not null default 0,
        quantidade_fisica numeric not null default 0,
        quantidade_aprovada numeric not null default 0,
        quantidade_condicional numeric not null default 0,
        quantidade_rejeitada numeric not null default 0,
        valor_unitario_pedido numeric not null default 0,
        valor_unitario_real numeric not null default 0,
        certificado_exigido boolean not null default false,
        certificado_apresentado boolean not null default false,
        validade_certificado date,
        resultado_inspecao text not null,
        justificativa_divergencia text not null default ''
    )
    """,
    """
    create table erp_stock_receipt_links (
        id text primary key default (lower(hex(randomblob(16)))),
        goods_receipt_line_id text not null,
        movement_id integer,
        quantidade_disponivel numeric not null default 0,
        quantidade_quarentena numeric not null default 0,
        idempotency_key text not null unique,
        created_at datetime not null default current_timestamp
    )
    """,
    """
    create table erp_audit_events (
        id text primary key default (lower(hex(randomblob(16)))),
        entity_type text not null,
        entity_id text,
        action text not null,
        actor text not null default '',
        origin text not null default 'ERP',
        before_data text not null default '{}',
        after_data text not null default '{}',
        reason text not null default '',
        created_at datetime not null default current_timestamp
    )
    """,
)


class ErpSkuResolutionTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)

        @event.listens_for(self.engine, "connect")
        def register_sqlite_functions(connection, _):
            connection.create_function("now", 0, lambda: "2026-07-30 00:00:00")
            connection.create_function("greatest", -1, lambda *values: max(values))
            connection.create_function(
                "jsonb_build_object",
                -1,
                lambda *args: json.dumps(
                    {
                        str(args[index]): args[index + 1]
                        for index in range(0, len(args), 2)
                    }
                ),
            )

        @event.listens_for(self.engine, "before_cursor_execute", retval=True)
        def remove_postgres_row_locks(_, __, statement, parameters, ___, ____):
            return (
                re.sub(r"\s+for\s+update(?:\s+of\s+\w+)?", "", statement, flags=re.I),
                parameters,
            )

        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for statement in ERP_TEST_SCHEMA:
                connection.execute(text(statement))
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        self.user = User(
            username="erp-sku-tester",
            password_hash="hash",
            role="ADM",
            active=True,
        )
        self.active_sku = SKU(
            sku="MAT-001",
            descricao="Material ativo",
            unidade="UN",
            active=True,
        )
        self.inactive_sku = SKU(
            sku="MAT-002",
            descricao="Material inativo",
            unidade="UN",
            active=False,
        )
        self.db.add_all([self.user, self.active_sku, self.inactive_sku])
        self.db.commit()
        self.user_id = self.user.id
        self.active_sku_id = self.active_sku.id

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _payload(key, sku_code, quantity=2):
        return {
            "idempotency_key": key,
            "numero_oc": f"OC-{key}",
            "fornecedor_nome": "Fornecedor teste",
            "lines": [
                {
                    "sku_codigo": sku_code,
                    "descricao_original": "Material da O.C.",
                    "unidade": "UN",
                    "quantidade_pedida": quantity,
                    "valor_unitario_pedido": 10,
                }
            ],
        }

    def _line(self, order_id):
        return self.db.execute(
            text(
                "select * from erp_purchase_order_lines "
                "where purchase_order_id=:order_id"
            ),
            {"order_id": order_id},
        ).mappings().one()

    def test_create_purchase_order_resolves_active_sku_code(self):
        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "  mat-001  "),
            "comprador-teste",
        )

        line = self._line(order["id"])
        self.assertEqual(self.active_sku_id, line["sku_id"])
        self.assertEqual("MAT-001", line["sku_codigo"])

    def test_create_purchase_order_keeps_unregistered_or_inactive_item_unlinked(self):
        for code in ("NAO-CADASTRADO", "MAT-002"):
            with self.subTest(code=code):
                order = create_purchase_order(
                    self.db,
                    self._payload(str(uuid4()), code),
                    "comprador-teste",
                )
                line = self._line(order["id"])
                self.assertIsNone(line["sku_id"])
                self.assertEqual(code, line["sku_codigo"])

    def test_reconcile_pending_line_links_sku_created_after_purchase_order(self):
        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "FUTURO-001"),
            "comprador-teste",
        )
        self.assertIsNone(self._line(order["id"])["sku_id"])
        late_sku = SKU(
            sku="FUTURO-001",
            descricao="SKU sincronizado depois da O.C.",
            unidade="UN",
            active=True,
        )
        self.db.add(late_sku)
        self.db.commit()

        updated = reconcile_pending_purchase_line_skus(self.db)
        line = self._line(order["id"])
        self.assertEqual([str(line["id"])], updated)
        self.assertEqual(late_sku.id, line["sku_id"])
        self.assertEqual("FUTURO-001", line["sku_codigo"])
        audit_count = self.db.execute(
            text("select count(*) from erp_audit_events where action='SKU_VINCULADO_POR_RECONCILIACAO'")
        ).scalar_one()
        self.assertEqual(1, audit_count)

    def test_legacy_sync_resolves_sku_when_replacing_existing_lines(self):
        key = str(uuid4())
        order = create_purchase_order(
            self.db,
            self._payload(key, "AINDA-SEM-CADASTRO"),
            "comprador-teste",
        )

        result = sync_legacy_purchase_order(
            self.db,
            self._payload(key, "mat-001"),
            "comprador-teste",
        )

        line = self._line(order["id"])
        self.assertTrue(result["updated"])
        self.assertEqual(self.active_sku_id, line["sku_id"])
        self.assertEqual("MAT-001", line["sku_codigo"])

    def test_confirm_receipt_resolves_legacy_line_by_active_sku_code(self):
        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "MAT-001"),
            "comprador-teste",
        )
        line = self._line(order["id"])
        self.db.execute(
            text(
                "update erp_purchase_order_lines set sku_id=null "
                "where id=:line_id"
            ),
            {"line_id": line["id"]},
        )
        self.db.commit()

        result = confirm_receipt(
            self.db,
            {
                "idempotency_key": str(uuid4()),
                "purchase_order_id": order["id"],
                "numero_nf": "NF-TESTE",
                "lines": [
                    {
                        "purchase_order_line_id": line["id"],
                        "quantidade_fisica": 2,
                        "quantidade_aprovada": 2,
                        "quantidade_condicional": 0,
                        "quantidade_rejeitada": 0,
                        "resultado_inspecao": "A",
                        "valor_unitario_real": 10,
                    }
                ],
            },
            "almoxarife-teste",
            self.user_id,
        )

        receipt_line = self.db.execute(
            text(
                "select sku_id,sku_codigo from erp_goods_receipt_lines "
                "where goods_receipt_id=:receipt_id"
            ),
            {"receipt_id": result["id"]},
        ).mappings().one()
        balance = self.db.query(StockBalance).filter_by(
            sku_id=self.active_sku_id
        ).one()
        movements = self.db.query(Movement).filter_by(
            source_type="GOODS_RECEIPT",
            source_id=result["id"],
        ).all()
        self.assertEqual(self.active_sku_id, receipt_line["sku_id"])
        self.assertEqual("MAT-001", receipt_line["sku_codigo"])
        self.assertEqual(2, balance.saldo_atual)
        self.assertEqual(1, len(movements))

    def test_confirm_receipt_still_blocks_approved_unregistered_item(self):
        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "NAO-CADASTRADO"),
            "comprador-teste",
        )
        line = self._line(order["id"])
        receipt_key = str(uuid4())

        with self.assertRaisesRegex(
            ValueError,
            "SKU e obrigatorio para quantidade aprovada em estoque",
        ):
            confirm_receipt(
                self.db,
                {
                    "idempotency_key": receipt_key,
                    "purchase_order_id": order["id"],
                    "lines": [
                        {
                            "purchase_order_line_id": line["id"],
                            "quantidade_fisica": 1,
                            "quantidade_aprovada": 1,
                            "quantidade_condicional": 0,
                            "quantidade_rejeitada": 0,
                            "resultado_inspecao": "A",
                        }
                    ],
                },
                "almoxarife-teste",
                self.user_id,
            )
        self.db.rollback()

        receipt_count = self.db.execute(
            text(
                "select count(*) from erp_goods_receipts "
                "where idempotency_key=:key"
            ),
            {"key": receipt_key},
        ).scalar_one()
        self.assertEqual(0, receipt_count)
        self.assertEqual(0, self.db.query(Movement).count())

    def test_approved_inspection_uses_only_received_quantity(self):
        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "MAT-001", quantity=5),
            "comprador-teste",
        )
        line = self._line(order["id"])

        result = confirm_receipt(
            self.db,
            {
                "idempotency_key": str(uuid4()),
                "purchase_order_id": order["id"],
                "numero_nf": "NF-APROVADA",
                "lines": [{
                    "purchase_order_line_id": line["id"],
                    "quantidade_fisica": 2,
                    "resultado_inspecao": "A",
                }],
            },
            "almoxarife-teste",
            self.user_id,
        )

        receipt_line = self.db.execute(text("""
            select quantidade_fisica,quantidade_aprovada,quantidade_condicional,
                   quantidade_rejeitada
              from erp_goods_receipt_lines where goods_receipt_id=:id
        """), {"id": result["id"]}).mappings().one()
        balance = self.db.query(StockBalance).filter_by(sku_id=self.active_sku_id).one()
        self.assertEqual(Decimal("2"), Decimal(str(receipt_line["quantidade_fisica"])))
        self.assertEqual(Decimal("2"), Decimal(str(receipt_line["quantidade_aprovada"])))
        self.assertEqual(Decimal("0"), Decimal(str(receipt_line["quantidade_condicional"])))
        self.assertEqual(Decimal("0"), Decimal(str(receipt_line["quantidade_rejeitada"])))
        self.assertEqual(Decimal("2"), balance.saldo_atual)

    def test_conditional_inspection_uses_approved_and_quarantine_quantities(self):
        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "MAT-001", quantity=5),
            "comprador-teste",
        )
        line = self._line(order["id"])

        result = confirm_receipt(
            self.db,
            {
                "idempotency_key": str(uuid4()),
                "purchase_order_id": order["id"],
                "numero_nf": "NF-CONDICIONAL",
                "lines": [{
                    "purchase_order_line_id": line["id"],
                    "quantidade_aprovada": 1,
                    "quantidade_condicional": 2,
                    "resultado_inspecao": "AC",
                }],
            },
            "almoxarife-teste",
            self.user_id,
        )

        receipt_line = self.db.execute(text("""
            select quantidade_fisica,quantidade_aprovada,quantidade_condicional,
                   quantidade_rejeitada
              from erp_goods_receipt_lines where goods_receipt_id=:id
        """), {"id": result["id"]}).mappings().one()
        balance = self.db.query(StockBalance).filter_by(sku_id=self.active_sku_id).one()
        self.assertEqual(Decimal("3"), Decimal(str(receipt_line["quantidade_fisica"])))
        self.assertEqual(Decimal("1"), Decimal(str(receipt_line["quantidade_aprovada"])))
        self.assertEqual(Decimal("2"), Decimal(str(receipt_line["quantidade_condicional"])))
        self.assertEqual(Decimal("0"), Decimal(str(receipt_line["quantidade_rejeitada"])))
        self.assertEqual(Decimal("1"), balance.saldo_atual)

    def test_rejected_inspection_creates_trace_movement_without_stock_entry(self):
        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "MAT-001", quantity=5),
            "comprador-teste",
        )
        line = self._line(order["id"])

        result = confirm_receipt(
            self.db,
            {
                "idempotency_key": str(uuid4()),
                "purchase_order_id": order["id"],
                "numero_nf": "NF-REJEITADA",
                "lines": [{
                    "purchase_order_line_id": line["id"],
                    "quantidade_rejeitada": 3,
                    "resultado_inspecao": "D",
                }],
            },
            "almoxarife-teste",
            self.user_id,
        )

        receipt_line = self.db.execute(text("""
            select quantidade_fisica,quantidade_aprovada,quantidade_condicional,
                   quantidade_rejeitada
              from erp_goods_receipt_lines where goods_receipt_id=:id
        """), {"id": result["id"]}).mappings().one()
        rejection = self.db.query(Movement).filter_by(
            source_id=result["id"], tipo="REJEICAO"
        ).one()
        balance = self.db.query(StockBalance).filter_by(sku_id=self.active_sku_id).one()
        self.assertEqual(Decimal("3"), Decimal(str(receipt_line["quantidade_fisica"])))
        self.assertEqual(Decimal("0"), Decimal(str(receipt_line["quantidade_aprovada"])))
        self.assertEqual(Decimal("0"), Decimal(str(receipt_line["quantidade_condicional"])))
        self.assertEqual(Decimal("3"), Decimal(str(receipt_line["quantidade_rejeitada"])))
        self.assertEqual(Decimal("0"), balance.saldo_atual)
        self.assertEqual(Decimal("0"), rejection.saldo_anterior)
        self.assertEqual(Decimal("0"), rejection.saldo_posterior)

        reverse_receipt(
            self.db, result["id"], "almoxarife-teste", self.user_id, "Devolução cancelada"
        )
        self.assertEqual("CANCELADA", self.db.get(Movement, rejection.id).movement_status)

    def test_confirm_receipt_rejects_line_from_another_purchase_order(self):
        first = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "MAT-001"),
            "comprador-teste",
        )
        second = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "MAT-001"),
            "comprador-teste",
        )
        second_line = self._line(second["id"])
        receipt_key = str(uuid4())

        with self.assertRaisesRegex(ValueError, "nao pertence a O.C."):
            confirm_receipt(
                self.db,
                {
                    "idempotency_key": receipt_key,
                    "purchase_order_id": first["id"],
                    "lines": [
                        {
                            "purchase_order_line_id": second_line["id"],
                            "quantidade_fisica": 1,
                            "quantidade_aprovada": 1,
                            "quantidade_condicional": 0,
                            "quantidade_rejeitada": 0,
                            "resultado_inspecao": "A",
                        }
                    ],
                },
                "almoxarife-teste",
                self.user_id,
            )
        self.db.rollback()

        self.assertEqual(
            0,
            self.db.execute(
                text(
                    "select count(*) from erp_goods_receipts "
                    "where idempotency_key=:key"
                ),
                {"key": receipt_key},
            ).scalar_one(),
        )
        self.assertEqual(0, self.db.query(Movement).count())

    def test_confirm_receipt_explodes_nested_bom_and_reversal_preserves_oc_quantity(self):
        assembly = SKU(sku="CJ-001", descricao="Conjunto comprado", unidade="CJ", active=True)
        intermediate = SKU(sku="SUB-001", descricao="Subconjunto", unidade="UN", active=True)
        direct_leaf = SKU(sku="COMP-001", descricao="Componente direto", unidade="UN", active=True)
        nested_leaf = SKU(sku="COMP-002", descricao="Componente interno", unidade="UN", active=True)
        self.db.add_all([assembly, intermediate, direct_leaf, nested_leaf])
        self.db.commit()
        self.db.add_all([
            BomComponent(item_sku_id=assembly.id, component_sku_id=intermediate.id, quantidade=1),
            BomComponent(item_sku_id=assembly.id, component_sku_id=direct_leaf.id, quantidade=2),
            BomComponent(item_sku_id=intermediate.id, component_sku_id=nested_leaf.id, quantidade=3),
        ])
        self.db.commit()

        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "CJ-001", quantity=2),
            "comprador-teste",
        )
        line = self._line(order["id"])
        receipt = confirm_receipt(
            self.db,
            {
                "idempotency_key": str(uuid4()),
                "purchase_order_id": order["id"],
                "numero_nf": "NF-BOM-001",
                "lines": [{
                    "purchase_order_line_id": line["id"],
                    "quantidade_fisica": 2,
                    "quantidade_aprovada": 2,
                    "quantidade_condicional": 0,
                    "quantidade_rejeitada": 0,
                    "resultado_inspecao": "A",
                    "valor_unitario_real": 10,
                }],
            },
            "almoxarife-teste",
            self.user_id,
        )

        movements = self.db.query(Movement).filter_by(source_id=receipt["id"]).all()
        self.assertEqual({direct_leaf.id, nested_leaf.id}, {movement.sku_id for movement in movements})
        self.assertNotIn(assembly.id, {movement.sku_id for movement in movements})
        self.assertNotIn(intermediate.id, {movement.sku_id for movement in movements})
        self.assertEqual(Decimal("4"), self.db.query(StockBalance).filter_by(sku_id=direct_leaf.id).one().saldo_atual)
        self.assertEqual(Decimal("6"), self.db.query(StockBalance).filter_by(sku_id=nested_leaf.id).one().saldo_atual)

        reverse_receipt(
            self.db, receipt["id"], "almoxarife-teste", self.user_id, "teste de estorno B.O.M."
        )
        self.assertEqual(Decimal("0"), self.db.query(StockBalance).filter_by(sku_id=direct_leaf.id).one().saldo_atual)
        self.assertEqual(Decimal("0"), self.db.query(StockBalance).filter_by(sku_id=nested_leaf.id).one().saldo_atual)
        reversed_line = self._line(order["id"])
        self.assertEqual(Decimal("0"), reversed_line["quantidade_recebida"])

    def test_pending_receipt_exposes_leaf_bom_components_without_changing_commercial_line(self):
        assembly = SKU(
            sku="CJ-PREVIEW",
            descricao="Conjunto comercial",
            unidade="CJ",
            active=True,
        )
        component = SKU(
            sku="COMP-PREVIEW",
            descricao="Componente unitario",
            unidade="PC",
            active=True,
        )
        self.db.add_all([assembly, component])
        self.db.commit()
        self.db.add(
            BomComponent(
                item_sku_id=assembly.id,
                component_sku_id=component.id,
                quantidade=Decimal("3"),
            )
        )
        self.db.commit()

        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "CJ-PREVIEW", quantity=2),
            "comprador-teste",
        )
        line = self._line(order["id"])

        pending = pending_receipt_orders(self.db)
        preview = next(row for row in pending if row["line_id"] == line["id"])

        self.assertEqual("BOM_COMPONENTS", preview["receipt_mode"])
        self.assertEqual("CJ-PREVIEW", preview["bom_parent"]["sku_codigo"])
        self.assertEqual(1, len(preview["bom_components"]))
        self.assertEqual("COMP-PREVIEW", preview["bom_components"][0]["sku_codigo"])
        self.assertEqual(
            Decimal("6"),
            Decimal(str(preview["bom_components"][0]["quantidade_pendente"])),
        )
        self.assertEqual(
            Decimal("3"),
            Decimal(str(preview["bom_components"][0]["quantidade_por_unidade_pai"])),
        )
        self.assertEqual(Decimal("2"), Decimal(str(preview["quantidade_pendente"])))

    def test_component_receipt_keeps_commercial_conjunto_partial_until_bom_is_complete(self):
        """A bought kit is received and inspected per leaf, not as parent stock."""
        assembly = SKU(sku="CJ-INSPECAO", descricao="Conjunto comercial", unidade="CJ", active=True)
        first_component = SKU(sku="PC-INSPECAO-1", descricao="Peca um", unidade="PC", active=True)
        second_component = SKU(sku="PC-INSPECAO-2", descricao="Peca dois", unidade="PC", active=True)
        self.db.add_all([assembly, first_component, second_component])
        self.db.commit()
        self.db.add_all([
            BomComponent(item_sku_id=assembly.id, component_sku_id=first_component.id, quantidade=Decimal("2")),
            BomComponent(item_sku_id=assembly.id, component_sku_id=second_component.id, quantidade=Decimal("1")),
        ])
        self.db.commit()
        order = create_purchase_order(
            self.db,
            self._payload(str(uuid4()), "CJ-INSPECAO", quantity=2),
            "comprador-teste",
        )
        line = self._line(order["id"])

        first_receipt = confirm_receipt(
            self.db,
            {
                "idempotency_key": str(uuid4()),
                "purchase_order_id": order["id"],
                "numero_nf": "NF-COMP-1",
                "lines": [{
                    "purchase_order_line_id": line["id"],
                    "component_receipts": [
                        {"sku_id": first_component.id, "quantidade_fisica": 2, "quantidade_aprovada": 2, "quantidade_condicional": 0, "quantidade_rejeitada": 0, "resultado_inspecao": "A", "valor_unitario_real": 4},
                        {"sku_id": second_component.id, "quantidade_fisica": 0, "quantidade_aprovada": 0, "quantidade_condicional": 0, "quantidade_rejeitada": 0, "resultado_inspecao": "A", "valor_unitario_real": 6},
                    ],
                }],
            },
            "almoxarife-teste",
            self.user_id,
        )
        partial_line = self._line(order["id"])
        order_status = self.db.execute(
            text("select status from erp_purchase_orders where id=:id"), {"id": order["id"]}
        ).scalar_one()
        self.assertEqual("PARCIALMENTE_RECEBIDA", partial_line["status"])
        self.assertEqual(Decimal("0"), Decimal(str(partial_line["quantidade_recebida"])))
        self.assertEqual("PARCIALMENTE_RECEBIDA", order_status)
        self.assertEqual(Decimal("2"), self.db.query(StockBalance).filter_by(sku_id=first_component.id).one().saldo_atual)
        self.assertIsNone(self.db.query(StockBalance).filter_by(sku_id=assembly.id).one_or_none())

        pending = next(row for row in pending_receipt_orders(self.db) if row["line_id"] == line["id"])
        pending_by_sku = {row["sku_codigo"]: Decimal(str(row["quantidade_pendente"])) for row in pending["bom_components"]}
        self.assertEqual(Decimal("2"), pending_by_sku["PC-INSPECAO-1"])
        self.assertEqual(Decimal("2"), pending_by_sku["PC-INSPECAO-2"])

        second_receipt = confirm_receipt(
            self.db,
            {
                "idempotency_key": str(uuid4()),
                "purchase_order_id": order["id"],
                "numero_nf": "NF-COMP-2",
                "lines": [{
                    "purchase_order_line_id": line["id"],
                    "component_receipts": [
                        {"sku_id": first_component.id, "quantidade_fisica": 2, "quantidade_aprovada": 2, "quantidade_condicional": 0, "quantidade_rejeitada": 0, "resultado_inspecao": "A", "valor_unitario_real": 4},
                        {"sku_id": second_component.id, "quantidade_fisica": 2, "quantidade_aprovada": 2, "quantidade_condicional": 0, "quantidade_rejeitada": 0, "resultado_inspecao": "A", "valor_unitario_real": 6},
                    ],
                }],
            },
            "almoxarife-teste",
            self.user_id,
        )
        received_line = self._line(order["id"])
        self.assertEqual("RECEBIDA", received_line["status"])
        self.assertEqual(Decimal("2"), Decimal(str(received_line["quantidade_recebida"])))
        self.assertEqual(Decimal("4"), self.db.query(StockBalance).filter_by(sku_id=first_component.id).one().saldo_atual)
        self.assertEqual(Decimal("2"), self.db.query(StockBalance).filter_by(sku_id=second_component.id).one().saldo_atual)

        reverse_receipt(
            self.db, second_receipt["id"], "almoxarife-teste", self.user_id, "Teste de estorno parcial"
        )
        reverted_line = self._line(order["id"])
        self.assertEqual("PARCIALMENTE_RECEBIDA", reverted_line["status"])
        self.assertEqual(Decimal("0"), Decimal(str(reverted_line["quantidade_recebida"])))
        self.assertEqual(Decimal("2"), self.db.query(StockBalance).filter_by(sku_id=first_component.id).one().saldo_atual)
        self.assertEqual(Decimal("0"), self.db.query(StockBalance).filter_by(sku_id=second_component.id).one().saldo_atual)
        self.assertTrue(first_receipt["id"])


if __name__ == "__main__":
    unittest.main()
