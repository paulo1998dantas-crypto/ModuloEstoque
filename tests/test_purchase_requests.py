import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch, Mock
from uuid import uuid4
from sqlalchemy import select, func, text
APP = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP))
from services import purchase_requests as pr
from services.erp_service import (create_purchase_order, sync_legacy_purchase_order,
                                  cancel_purchase_order, close_purchase_order_technical,
                                  confirm_receipt)
from models import BomComponent, User, Movement, SKU, StockBalance
import test_erp_sku_resolution as legacy

class PurchaseRequestTests(unittest.TestCase):
    setUpBase = legacy.ErpSkuResolutionTest.setUp
    tearDown = legacy.ErpSkuResolutionTest.tearDown

    def setUp(self):
        self.setUpBase()
        pr.metadata.create_all(self.engine)
        pr.vehicle_entries.create(self.engine, checkfirst=True)
        pr.work_orders.create(self.engine, checkfirst=True)
        self.operator = User(username="OPERADOR",password_hash="hash",role="OPERADOR",active=True)
        self.buyer = User(username="COMPRADOR",password_hash="hash",role="COMPRADOR",active=True)
        self.pcp = User(username="PCP",password_hash="hash",role="PCP",active=True)
        self.db.add_all([self.operator,self.buyer,self.pcp])
        self.db.commit()
        self.rbac = patch("auth.shared_rbac_enabled",return_value=False)
        self.rbac.start()
        self.addCleanup(self.rbac.stop)

    def request(self, quantity="2", origin="ESTOQUE", user=None, key=None, **values):
        payload=dict(sku_codigo="MAT-001",quantity=quantity,needed_at="2026-10-15",
                     idempotency_key=key or str(uuid4()),reference="",notes="Reposição",
                     sector="GERAL",work_order_ids=[])
        payload.update(values)
        result=pr.create(self.db,payload,user or self.operator,origin)
        self.db.commit()
        return result["request"]

    def work_order(self, item_number, numero_os=None, status="ATIVA", technical_status="ABERTA"):
        entry_id, order_id = str(uuid4()), str(uuid4())
        self.db.execute(pr.vehicle_entries.insert().values(id=entry_id, item_number=item_number))
        self.db.execute(pr.work_orders.insert().values(
            id=order_id, vehicle_entry_id=entry_id,
            numero_os=numero_os or f"TA{item_number:06d}",
            status=status, technical_status=technical_status))
        self.db.commit()
        return order_id

    def order(self, ids, qty="2", actor=None, key=None, sku="MAT-001"):
        data=legacy.ErpSkuResolutionTest._payload(key or str(uuid4()),sku,qty)
        data["purchase_request_ids"]=ids
        return create_purchase_order(self.db,data,actor or self.buyer.username)

    def test_two_origins_and_automatic_requester(self):
        stock=self.request()
        pcp=self.request(origin="PCP",user=self.pcp,sector="PRODUÇÃO")
        self.assertEqual("ESTOQUE",stock["origin"])
        self.assertEqual("PCP",pcp["origin"])
        self.assertEqual("OPERADOR",stock["requested_by"])
        self.assertEqual("SOLICITADA",stock["status"])
        self.assertEqual(1,len(pr.history(self.db,stock["id"])["events"]))

    def test_request_can_link_multiple_open_work_orders_and_sector(self):
        first=self.work_order(3185)
        second=self.work_order(3186)
        row=self.request(sector="PRODUÇÃO",work_order_ids=[first,second])

        self.assertEqual("PRODUÇÃO",row["sector"])
        self.assertEqual({first,second},set(row["work_order_ids"]))
        self.assertEqual(2,len(row["work_orders"]))
        listed=pr.listing(self.db,{"q":"3186"})["items"][0]
        self.assertEqual(row["id"],listed["id"])
        self.assertIn("Setor: PRODUÇÃO",listed["reference_display"])
        self.assertIn("TA003186",listed["reference_display"])
        event=pr.history(self.db,row["id"])["events"][0]
        self.assertEqual("PRODUÇÃO",event["after_data"]["sector"])
        self.assertEqual(2,len(event["after_data"]["work_orders"]))

    def test_only_open_work_orders_can_be_added_to_a_request(self):
        closed=self.work_order(3188,status="FINALIZADA",technical_status="CONCLUIDA")
        with self.assertRaisesRegex(ValueError,"ainda estejam abertas"):
            self.request(work_order_ids=[closed])
        self.assertEqual(0,self.db.execute(select(func.count()).select_from(
            pr.request_work_orders)).scalar_one())

    def test_sector_is_restricted_to_the_three_standard_choices(self):
        with self.assertRaisesRegex(ValueError,"PRODUÇÃO, ADMINISTRATIVO ou GERAL"):
            self.request(sector="COMERCIAL")
        self.assertEqual(0,self.db.execute(select(func.count()).select_from(pr.requests)).scalar_one())

    def test_edit_replaces_multiple_work_order_links_and_audits_changes(self):
        first=self.work_order(3190)
        second=self.work_order(3191)
        third=self.work_order(3192)
        row=self.request(sector="GERAL",work_order_ids=[first,second])
        edited=pr.transition(self.db,row["id"],{
            "action":"EDITAR","version":row["version"],"sku_codigo":"MAT-001",
            "quantity":"3","needed_at":"2026-10-20","sector":"ADMINISTRATIVO",
            "work_order_ids":[second,third],"reason":"Corrigir setor e O.S. vinculadas",
        },self.operator)["request"]
        self.db.commit()

        self.assertEqual("ADMINISTRATIVO",edited["sector"])
        self.assertEqual({second,third},set(edited["work_order_ids"]))
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual({first,second},set(event["before_data"]["work_order_ids"]))
        self.assertEqual({second,third},set(event["after_data"]["work_order_ids"]))

    def test_historical_reference_review_is_traced_and_cleared_by_manual_relink(self):
        order=self.work_order(3193)
        row=self.request(sector="GERAL")
        self.db.execute(pr.requests.update().where(pr.requests.c.id==row["id"])
                        .values(reference="O.S. 9999"))
        pr.reference_backfill.create(self.engine,checkfirst=True)
        self.db.execute(pr.reference_backfill.insert().values(
            request_id=row["id"],original_reference="O.S. 9999",result="SEM_MATCH",
            linked_work_orders=0,unresolved_tokens="{9999}",resolved_by_id=None,
            resolved_by=None,reviewed_at=pr.now()))
        self.db.commit()

        edited=pr.transition(self.db,row["id"],{
            "action":"EDITAR","version":row["version"],"sku_codigo":"MAT-001",
            "quantity":"3","needed_at":"2026-10-20","sector":"PRODUÇÃO",
            "work_order_ids":[order],"reason":"Vínculo histórico conferido com a O.S. correta",
        },self.operator)["request"]
        self.db.commit()

        review=self.db.execute(select(pr.reference_backfill).where(
            pr.reference_backfill.c.request_id==row["id"])).mappings().one()
        self.assertEqual("REVISADA_MANUALMENTE",review["result"])
        self.assertEqual(self.operator.username,review["resolved_by"])
        timeline=pr.history(self.db,row["id"])["events"]
        normalized=next(event for event in timeline if event["action"]=="NORMALIZACAO_REFERENCIAS")
        self.assertEqual(self.operator.username,normalized["actor"])
        self.assertEqual("REVISADA_MANUALMENTE",normalized["after_data"]["reference_backfill_result"])
        self.assertEqual([order],edited["work_order_ids"])

    def test_existing_link_survives_os_closure_but_closed_os_cannot_be_added(self):
        linked=self.work_order(3194)
        newly_closed=self.work_order(3195,status="FINALIZADA",technical_status="CONCLUIDA")
        row=self.request(work_order_ids=[linked])
        self.db.execute(pr.work_orders.update().where(pr.work_orders.c.id==linked)
                        .values(status="FINALIZADA",technical_status="CONCLUIDA"))
        self.db.commit()

        edited=pr.transition(self.db,row["id"],{
            "action":"EDITAR","version":row["version"],"sku_codigo":"MAT-001",
            "quantity":"3","needed_at":"2026-10-20","reason":"Ajustar quantidade",
        },self.operator)["request"]
        self.db.commit()
        self.assertEqual([linked],edited["work_order_ids"])
        with self.assertRaisesRegex(ValueError,"ainda estejam abertas"):
            pr.transition(self.db,row["id"],{
                "action":"EDITAR","version":edited["version"],"sku_codigo":"MAT-001",
                "quantity":"4","needed_at":"2026-10-20",
                "work_order_ids":[linked,newly_closed],"reason":"Tentar incluir O.S. fechada",
            },self.operator)

    def test_retry_is_idempotent(self):
        key=str(uuid4())
        a=self.request(key=key)
        b=self.request(key=key)
        self.assertEqual(a["id"],b["id"])
        self.assertEqual(1,self.db.execute(select(func.count()).select_from(pr.events)).scalar())

    def test_duplicate_sector_blocks_across_origins_users_quantity_and_date(self):
        row=self.request(sector="PRODUÇÃO")
        with self.assertRaisesRegex(ValueError,"Solicitação duplicada.*MAT-001.*PRODUÇÃO"):
            self.request(origin="PCP",user=self.pcp,quantity="9",needed_at="2026-11-01",
                         sector="PRODUCAO")
        self.db.rollback()
        self.assertEqual(1,self.db.execute(select(func.count()).select_from(pr.requests)).scalar_one())
        self.assertEqual(1,len(pr.history(self.db,row["id"])["events"]))

    def test_legacy_free_text_cannot_bypass_structured_duplicate_guard(self):
        order=self.work_order(3300)
        self.request(work_order_ids=[order])
        with self.assertRaisesRegex(ValueError,"texto livre"):
            self.request(reference="O.S. 3300")
        self.db.rollback()
        self.assertEqual(1,self.db.execute(select(func.count()).select_from(pr.requests)).scalar_one())

    def test_old_client_missing_reference_fields_must_refresh_without_writing(self):
        with self.assertRaisesRegex(ValueError,"Recarregue a tela"):
            pr.create(self.db,dict(sku_codigo="MAT-001",quantity="2",needed_at="2026-10-15",
                                 idempotency_key=str(uuid4())),self.operator,"ESTOQUE")
        self.assertEqual(0,self.db.execute(select(func.count()).select_from(pr.requests)).scalar_one())

    def test_edit_cannot_replace_os_links_with_free_text(self):
        row=self.request()
        with self.assertRaisesRegex(ValueError,"lista de O.S."):
            pr.transition(self.db,row["id"],{
                "action":"EDITAR","version":1,"sku_codigo":"MAT-001","quantity":"2",
                "needed_at":"2026-10-15","reference":"O.S. 3185","reason":"Alterar referência",
            },self.operator)

    def test_duplicate_os_blocks_any_overlap_even_if_sector_differs(self):
        first,second,third=[self.work_order(number) for number in (3301,3302,3303)]
        self.request(sector="PRODUÇÃO",work_order_ids=[first,second])
        with self.assertRaisesRegex(ValueError,"Solicitação duplicada.*TA003302"):
            self.request(origin="PCP",user=self.pcp,sector="GERAL",work_order_ids=[third,second])
        self.db.rollback()
        self.assertEqual(2,self.db.execute(select(func.count()).select_from(pr.request_work_orders)).scalar_one())

    def test_different_os_same_sector_or_different_sku_are_not_duplicates(self):
        first,second=self.work_order(3304),self.work_order(3305)
        self.request(sector="PRODUÇÃO",work_order_ids=[first])
        self.request(sector="PRODUÇÃO",work_order_ids=[second])
        self.db.add(SKU(sku="MAT-DUP-TEST",descricao="Outro material",unidade="PC",active=True))
        self.db.commit()
        self.request(sku_codigo="MAT-DUP-TEST",sector="PRODUÇÃO",work_order_ids=[first])
        self.request(sector="PRODUÇÃO",work_order_ids=[])
        self.assertEqual(4,self.db.execute(select(func.count()).select_from(pr.requests)).scalar_one())

    def test_buyer_assuming_request_does_not_release_duplicate_guard(self):
        row=self.request()
        pr.transition(self.db,row["id"],{"action":"ASSUMIR","version":1,"reason":"Comprar"},self.buyer)
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"Solicitação duplicada"):
            self.request()

    def test_cancelled_or_completed_requests_allow_a_new_need(self):
        order=self.work_order(3306)
        row=self.request(work_order_ids=[order])
        pr.transition(self.db,row["id"],{"action":"EXCLUIR","version":1,"reason":"Não necessário"},self.operator)
        self.db.commit()
        next_request=self.request(work_order_ids=[order])
        self.order([next_request["id"]])
        third=self.request(work_order_ids=[order])
        self.assertEqual("SOLICITADA",third["status"])
        self.assertEqual(3,self.db.execute(select(func.count()).select_from(pr.requests)).scalar_one())

    def test_edit_cannot_create_duplicate_os_and_leaves_no_event_or_link_changes(self):
        first,second=self.work_order(3307),self.work_order(3308)
        self.request(work_order_ids=[first])
        row=self.request(work_order_ids=[second])
        with self.assertRaisesRegex(ValueError,"Solicitação duplicada"):
            pr.transition(self.db,row["id"],{
                "action":"EDITAR","version":1,"sku_codigo":"MAT-001","quantity":"3",
                "needed_at":"2026-10-20","work_order_ids":[first,second],"reason":"Mudar vínculo",
            },self.operator)
        self.db.rollback()
        self.assertEqual([second],pr.current_work_order_ids(self.db,row["id"]))
        self.assertEqual(1,len(pr.history(self.db,row["id"])["events"]))
        self.assertEqual(1,pr.get(self.db,row["id"])["version"])

    def test_edit_quantity_of_same_request_is_not_a_self_duplicate(self):
        row=self.request()
        edited=pr.transition(self.db,row["id"],{
            "action":"EDITAR","version":1,"sku_codigo":"MAT-001","quantity":"3",
            "needed_at":"2026-10-20","reason":"Aumentar quantidade",
        },self.operator)["request"]
        self.db.commit()
        self.assertEqual("3",edited["quantity"])

    def test_edit_sku_or_sector_cannot_duplicate_pending_reference(self):
        self.request(sector="GERAL")
        row=self.request(sector="ADMINISTRATIVO")
        with self.assertRaisesRegex(ValueError,"Solicitação duplicada"):
            pr.transition(self.db,row["id"],{
                "action":"EDITAR","version":1,"sku_codigo":"MAT-001","quantity":"2",
                "needed_at":"2026-10-15","sector":"GERAL","reason":"Mudar setor",
            },self.operator)
        self.db.rollback()
        self.db.add(SKU(sku="MAT-DUP-TEST",descricao="Outro material",unidade="PC",active=True))
        self.db.commit()
        other=self.request(sku_codigo="MAT-DUP-TEST",sector="GERAL")
        with self.assertRaisesRegex(ValueError,"Solicitação duplicada"):
            pr.transition(self.db,other["id"],{
                "action":"EDITAR","version":1,"sku_codigo":"MAT-001","quantity":"2",
                "needed_at":"2026-10-15","reason":"Mudar material",
            },self.operator)

    def test_reopening_cancelled_request_cannot_duplicate_a_new_pending_need(self):
        row=self.request()
        cancelled=pr.transition(self.db,row["id"],{
            "action":"CANCELAR","version":1,"reason":"Cancelar",
        },self.buyer)["request"]
        self.db.commit()
        self.request()
        with self.assertRaisesRegex(ValueError,"Solicitação duplicada"):
            pr.transition(self.db,row["id"],{
                "action":"REABRIR","version":cancelled["version"],"reason":"Reabrir",
            },self.buyer)
        self.db.rollback()
        self.assertEqual("CANCELADA",pr.get(self.db,row["id"])["status"])

    def test_order_cancellation_is_atomic_if_reopening_would_duplicate_a_pending_need(self):
        row=self.request()
        order=self.order([row["id"]])
        self.request()
        with self.assertRaisesRegex(ValueError,"Solicitação duplicada"):
            cancel_purchase_order(self.db,order["id"],self.buyer.username,"Cancelar pedido")
        self.db.rollback()
        self.assertEqual("CONCLUIDA",pr.get(self.db,row["id"])["status"])
        self.assertEqual("EMITIDA",self.db.execute(select(pr.orders.c.status).where(
            pr.order_condition(self.db,pr.orders.c.id,order["id"]))).scalar_one())

    def test_postgres_material_locks_are_transactional_deduplicated_and_ordered(self):
        database=Mock()
        database.bind.dialect.name="postgresql"
        pr.lock_request_materials(database,[7,2,7])
        self.assertEqual(2,database.execute.call_count)
        self.assertEqual(["purchase-request-material:2","purchase-request-material:7"],
            [call.args[1]["key"] for call in database.execute.call_args_list])
        self.assertTrue(all("pg_advisory_xact_lock" in str(call.args[0])
                            for call in database.execute.call_args_list))

    def test_open_po_auto_routes_request_to_nearest_order_for_anticipation(self):
        earlier_data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","8")
        earlier_data["data_necessidade"]="2026-10-12"
        earlier=create_purchase_order(self.db,earlier_data,self.buyer.username)
        nearer_data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","5")
        nearer_data["data_necessidade"]="2026-10-14"
        nearer=create_purchase_order(self.db,nearer_data,self.buyer.username)
        row=self.request(quantity="2",needed_at="2026-10-15")
        self.assertEqual("ANTECIPACAO",row["request_type"])
        self.assertEqual(nearer["id"],row["anticipation_order_id"])
        listed=pr.listing(self.db,{})["items"][0]
        nearer_number=self.db.execute(text("select numero_oc from erp_purchase_orders where id=:id"),{"id":nearer["id"]}).scalar_one()
        self.assertEqual(nearer_number,listed["anticipation_numero_oc"])
        self.assertEqual("5.000",listed["anticipation_pending_quantity"])
        history=pr.history(self.db,row["id"])["events"][0]
        self.assertEqual("ANTECIPACAO_IDENTIFICADA",history["action"])
        self.assertIn(nearer_number,history["reason"])
        treated=pr.transition(self.db,row["id"],{"action":"SOLICITAR_ANTECIPACAO","version":1,"reason":"Fornecedor consultado; protocolo 123"},self.buyer)["request"]
        self.db.commit()
        self.assertEqual("EM_COMPRAS",treated["status"])
        self.assertEqual("COMPRADOR",treated["buyer"])
        self.assertEqual("SOLICITAR_ANTECIPACAO",pr.history(self.db,row["id"])["events"][-1]["action"])
        with self.assertRaisesRegex(ValueError,"antecipação"):
            pr.prepare(self.db,[row["id"]],self.buyer)
        converted=pr.transition(self.db,row["id"],{"action":"CONVERTER_NOVA_COMPRA","version":treated["version"],"reason":"Fornecedor não consegue antecipar"},self.buyer)["request"]
        self.db.commit()
        self.assertEqual("COMPRA_NOVA",converted["request_type"])
        self.assertEqual(1,len(pr.prepare(self.db,[row["id"]],self.buyer)["items"]))
        self.assertEqual("COMPRA_NOVA",pr.listing(self.db,{},self.buyer)["items"][0]["request_type"])

    def test_buyer_can_allocate_existing_order_and_reserved_balance_cannot_be_reused(self):
        order=self.order([],qty="5")
        row=self.request(quantity="2")
        options=pr.existing_order_options(self.db,row["id"],self.buyer)["items"]
        self.assertEqual([order["id"]],[item["id"] for item in options])
        self.assertEqual("5.000",options[0]["available_quantity"])

        allocated=pr.transition(self.db,row["id"],{
            "action":"ALOCAR_PEDIDO","version":row["version"],
            "purchase_order_id":order["id"],"reason":"Pedido vigente confirmado com compras",
        },self.buyer)["request"]
        self.db.commit()
        self.assertEqual("CONCLUIDA",allocated["status"])
        self.assertEqual(order["id"],allocated["purchase_order_id"])
        self.assertEqual(order["id"],allocated["anticipation_order_id"])
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual("ALOCAR_PEDIDO",event["action"])
        self.assertEqual("Pedido vigente confirmado com compras",event["reason"])
        self.assertEqual(order["id"],event["after_data"]["purchase_order"]["id"])

        second=self.request(quantity="4")
        self.assertEqual([],pr.existing_order_options(self.db,second["id"],self.buyer)["items"])
        with self.assertRaisesRegex(ValueError,"alocações já confirmadas"):
            pr.transition(self.db,second["id"],{
                "action":"ALOCAR_PEDIDO","version":second["version"],
                "purchase_order_id":order["id"],"reason":"Tentativa de alocação excedente",
            },self.buyer)

    def test_open_kit_order_covers_recursively_exploded_component_request(self):
        kit=SKU(sku="CJ-VIDRO",descricao="CJ VIDRO FIXO",unidade="CJ",
                grupo="30 - CONJUNTO",active=True)
        nested=SKU(sku="PP-VIDRO",descricao="PP VIDRO",unidade="CJ",
                   grupo="20 - PP",active=True)
        self.db.add_all([kit,nested]);self.db.flush()
        self.db.add_all([
            BomComponent(item_sku_id=kit.id,component_sku_id=nested.id,quantidade=2),
            BomComponent(item_sku_id=nested.id,component_sku_id=self.active_sku.id,quantidade=3),
        ])
        self.db.commit()

        order_data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),kit.sku,"4")
        order_data["data_necessidade"]="2026-10-14"
        order=create_purchase_order(self.db,order_data,self.buyer.username)
        self.db.execute(text("""update erp_purchase_order_lines
            set quantidade_recebida=1,status='PENDENTE' where purchase_order_id=:id"""),
            {"id":order["id"]})
        self.db.execute(text("update erp_purchase_orders set status='PARCIALMENTE_RECEBIDA' where id=:id"),
                        {"id":order["id"]})
        self.db.commit()

        row=self.request(quantity="10")
        self.assertEqual("ANTECIPACAO",row["request_type"])
        self.assertEqual(order["id"],row["anticipation_order_id"])
        order_options=pr.existing_order_options(self.db,row["id"],self.buyer)["items"]
        self.assertEqual(order["id"],order_options[0]["id"])
        self.assertEqual(Decimal("18"),Decimal(order_options[0]["available_quantity"]))
        listed=pr.listing(self.db,{})["items"][0]
        self.assertEqual(Decimal("18"),Decimal(listed["anticipation_pending_quantity"]))
        order_number=self.db.execute(text(
            "select numero_oc from erp_purchase_orders where id=:id"
        ),{"id":order["id"]}).scalar_one()
        self.assertEqual(order_number,listed["anticipation_numero_oc"])
        self.assertEqual("2026-10-14",listed["anticipation_delivery_date"])
        self.assertIsNone(pr.closest_active_order(self.db,kit.sku,date.fromisoformat("2026-10-15")))
        working=pr.transition(self.db,row["id"],{
            "action":"SOLICITAR_ANTECIPACAO","version":row["version"],
            "reason":"Fornecedor consultado sobre os vidros do conjunto",
        },self.buyer)["request"]
        closed=pr.transition(self.db,row["id"],{
            "action":"CONFIRMAR_ANTECIPACAO","version":working["version"],
            "confirmed_delivery_date":"2026-10-12","reason":"Fornecedor confirmou antecipação dos vidros",
        },self.buyer)["request"]
        self.assertEqual("CONCLUIDA",closed["status"])

    def test_existing_new_purchase_is_reconciled_to_kit_anticipation(self):
        row=self.request(quantity="4")
        self.assertEqual("COMPRA_NOVA",row["request_type"])
        kit=SKU(sku="CJ-VIDRO-LEGADO",descricao="CJ VIDRO",unidade="CJ",
                grupo="30 - CONJUNTO",active=True)
        self.db.add(kit);self.db.flush()
        self.db.add(BomComponent(item_sku_id=kit.id,component_sku_id=self.active_sku.id,quantidade=2))
        self.db.commit()
        order_data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),kit.sku,"3")
        order_data["data_necessidade"]="2026-10-16"
        order=create_purchase_order(self.db,order_data,self.buyer.username)

        current=pr.listing(self.db,{},self.buyer)["items"][0]
        self.assertEqual("ANTECIPACAO",current["request_type"])
        self.assertEqual(order["id"],current["anticipation_order_id"])
        self.assertEqual(Decimal("6"),Decimal(current["anticipation_pending_quantity"]))
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual("ANTECIPACAO_RECLASSIFICADA",event["action"])
        self.assertIn("B.O.M.",event["reason"])


    def test_anticipation_closes_only_with_valid_order_date_and_supplier_return(self):
        order_data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","8")
        order_data["data_necessidade"]="2026-10-14"
        order=create_purchase_order(self.db,order_data,self.buyer.username)
        row=self.request(quantity="2",needed_at="2026-10-15")
        self.assertEqual("ANTECIPACAO",row["request_type"])
        working=pr.transition(self.db,row["id"],{"action":"SOLICITAR_ANTECIPACAO","version":row["version"],"reason":"Fornecedor consultado; protocolo 456"},self.buyer)["request"]
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"nova data de entrega"):
            pr.transition(self.db,row["id"],{"action":"CONFIRMAR_ANTECIPACAO","version":working["version"],"confirmed_delivery_date":"","reason":"Fornecedor confirmou"},self.buyer)
        closed=pr.transition(self.db,row["id"],{"action":"CONFIRMAR_ANTECIPACAO","version":working["version"],"confirmed_delivery_date":"2026-10-10","reason":"Fornecedor confirmou por telefone; protocolo 789"},self.buyer)["request"]
        self.db.commit()
        self.assertEqual("CONCLUIDA",closed["status"])
        self.assertEqual(order["id"],closed["purchase_order_id"])
        self.assertEqual("2026-10-10",closed["anticipation_confirmed_delivery_date"])
        self.assertEqual("2026-10-10",pr.listing(self.db,{})["items"][0]["anticipation_confirmed_delivery_date"])
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual("CONFIRMAR_ANTECIPACAO",event["action"])
        self.assertIn("protocolo 789",event["reason"])

    def test_closed_or_fully_received_order_does_not_trigger_anticipation(self):
        data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","2")
        order=create_purchase_order(self.db,data,self.buyer.username)
        self.db.execute(text("update erp_purchase_order_lines set quantidade_recebida=quantidade_pedida,status='RECEBIDA' where purchase_order_id=:id"),{"id":order["id"]})
        self.db.commit()
        row=self.request()
        self.assertEqual("COMPRA_NOVA",row["request_type"])
        self.assertIsNone(row["anticipation_order_id"])

    def test_technical_close_reclassifies_existing_anticipation(self):
        data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","2")
        order=create_purchase_order(self.db,data,self.buyer.username)
        row=self.request()
        self.assertEqual("ANTECIPACAO",row["request_type"])

        closed=close_purchase_order_technical(
            self.db,order["id"],self.buyer.username,"Recebimento concluído"
        )

        current=pr.get(self.db,row["id"])
        self.assertEqual("CONCLUIDA",closed["status"])
        self.assertEqual("COMPRA_NOVA",current["request_type"])
        self.assertIsNone(current["anticipation_order_id"])
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual("ANTECIPACAO_RECLASSIFICADA",event["action"])
        self.assertEqual("ANTECIPACAO",event["before_data"]["request_type"])
        self.assertEqual("COMPRA_NOVA",event["after_data"]["request_type"])
        self.assertIn("Conclusão técnica",event["reason"])

    def test_full_receipt_reclassifies_existing_anticipation(self):
        data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","2")
        order=create_purchase_order(self.db,data,self.buyer.username)
        row=self.request()
        line_id=self.db.execute(text(
            "select id from erp_purchase_order_lines where purchase_order_id=:id"
        ),{"id":order["id"]}).scalar_one()

        confirm_receipt(self.db,{
            "idempotency_key":str(uuid4()),"purchase_order_id":order["id"],
            "numero_nf":"NF-REQ-001","lines":[{
                "purchase_order_line_id":line_id,"quantidade_fisica":2,
                "quantidade_aprovada":2,"quantidade_condicional":0,
                "quantidade_rejeitada":0,"resultado_inspecao":"A",
                "valor_unitario_real":10,
            }],
        },self.buyer.username,self.buyer.id)

        current=pr.get(self.db,row["id"])
        self.assertEqual("COMPRA_NOVA",current["request_type"])
        self.assertIsNone(current["anticipation_order_id"])
        self.assertEqual("ANTECIPACAO_RECLASSIFICADA",
                         pr.history(self.db,row["id"])["events"][-1]["action"])

    def test_legacy_stale_anticipation_displays_and_prepares_as_new_purchase(self):
        data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","2")
        order=create_purchase_order(self.db,data,self.buyer.username)
        row=self.request()
        self.db.execute(text("update erp_purchase_orders set status='CONCLUIDA' where id=:id"),
                        {"id":order["id"]})
        self.db.commit()

        listed=pr.listing(self.db,{})["items"][0]
        self.assertEqual("COMPRA_NOVA",listed["request_type"])
        self.assertIsNone(listed["anticipation_numero_oc"])
        self.assertEqual("ANTECIPACAO",pr.get(self.db,row["id"])["request_type"])

        pr.listing(self.db,{},self.buyer)
        self.db.commit()
        self.assertEqual("COMPRA_NOVA",pr.get(self.db,row["id"])["request_type"])
        self.assertEqual("ANTECIPACAO_RECLASSIFICADA",
                         pr.history(self.db,row["id"])["events"][-1]["action"])

        prepared=pr.prepare(self.db,[row["id"]],self.buyer)
        self.db.commit()
        self.assertEqual("COMPRA_NOVA",prepared["items"][0]["request_type"])
        self.assertEqual("COMPRA_NOVA",pr.get(self.db,row["id"])["request_type"])

    def test_legacy_stale_anticipation_can_be_assumed_as_new_purchase(self):
        data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","2")
        order=create_purchase_order(self.db,data,self.buyer.username)
        row=self.request()
        self.db.execute(text("update erp_purchase_orders set status='CONCLUIDA' where id=:id"),
                        {"id":order["id"]})
        self.db.commit()
        listed=pr.listing(self.db,{})["items"][0]

        treated=pr.transition(self.db,row["id"],{
            "action":"ASSUMIR","version":listed["version"],"reason":"Nova compra necessária"
        },self.buyer)["request"]
        self.db.commit()

        self.assertEqual("COMPRA_NOVA",treated["request_type"])
        self.assertEqual("EM_COMPRAS",treated["status"])
        self.assertEqual(3,len(pr.history(self.db,row["id"])["events"]))

    def test_quantity_validation(self):
        for qty in ("0","-1","NaN","Infinity","0.0001","9999999999999999"):
            with self.subTest(qty=qty),self.assertRaises(ValueError):
                self.request(quantity=qty)

    def test_invalid_date_or_inactive_sku(self):
        for vals in ({"needed_at":"bad"},{"sku_codigo":"MAT-002"},{"sku_codigo":"missing"}):
            with self.subTest(vals=vals),self.assertRaises(ValueError):
                self.request(**vals)

    def test_operator_cannot_submit_for_pcp(self):
        with self.assertRaises(PermissionError): self.request(origin="PCP")

    def test_pcp_cannot_treat_workflow_even_with_legacy_broad_permissions(self):
        row=self.request()
        with self.assertRaises(PermissionError):
            pr.transition(self.db,row["id"],{"action":"ASSUMIR","version":1,"reason":"Vou comprar"},self.pcp)

    def test_buyer_assume_note_cancel_and_reopen_are_audited(self):
        row=self.request()
        for action in ("ASSUMIR","OBSERVACAO","CANCELAR","REABRIR"):
            row=pr.transition(self.db,row["id"],{"action":action,"version":row["version"],"reason":"Teste"},self.buyer)["request"]
            self.db.commit()
        self.assertEqual("SOLICITADA",row["status"])
        self.assertEqual(5,len(pr.history(self.db,row["id"])["events"]))

    def test_requester_can_edit_and_soft_delete_own_pending_request(self):
        row=self.request()
        order=self.work_order(3200)
        edited=pr.transition(self.db,row["id"],{
            "action":"EDITAR","version":row["version"],"sku_codigo":"MAT-001",
            "quantity":"3.5","needed_at":"2026-10-20","work_order_ids":[order],
            "notes":"Quantidade revisada","reason":"Corrigir a necessidade da área",
        },self.operator)["request"]
        self.db.commit()
        self.assertEqual("3.5",edited["quantity"])
        self.assertEqual("2026-10-20",edited["needed_at"])
        self.assertEqual([order],edited["work_order_ids"])

        deleted=pr.transition(self.db,row["id"],{
            "action":"EXCLUIR","version":edited["version"],
            "reason":"Solicitação lançada em duplicidade",
        },self.operator)["request"]
        self.db.commit()
        self.assertEqual("CANCELADA",deleted["status"])
        events=pr.history(self.db,row["id"])["events"]
        self.assertEqual(["SOLICITADA","EDITAR","EXCLUIR"],
                         [event["action"] if index else event["after_data"]["status"]
                          for index,event in enumerate(events)])
        self.assertEqual(3,len(events))

    def test_other_authorized_nonbuyer_can_edit_and_exclude_pending_same_origin(self):
        row=self.request()
        other=User(username="OUTRO_OPERADOR",password_hash="hash",role="OPERADOR",active=True)
        self.db.add(other);self.db.commit()
        edited=pr.transition(self.db,row["id"],{
            "action":"EDITAR","version":row["version"],"sku_codigo":"MAT-001",
            "quantity":"4","needed_at":"2026-10-20","reason":"Ajuste autorizado pela equipe",
        },other)["request"]
        deleted=pr.transition(self.db,row["id"],{
            "action":"EXCLUIR","version":edited["version"],
            "reason":"Solicitação duplicada identificada pela equipe",
        },other)["request"]
        self.db.commit()
        self.assertEqual("CANCELADA",deleted["status"])
        self.assertEqual("OUTRO_OPERADOR",pr.history(self.db,row["id"])["events"][-1]["actor"])

    def test_nonbuyer_cannot_edit_after_buyer_assumes_request(self):
        row=self.request()
        row=pr.transition(self.db,row["id"],{
            "action":"ASSUMIR","version":row["version"],"reason":"Compra iniciada",
        },self.buyer)["request"]
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"não compradores"):
            pr.transition(self.db,row["id"],{
                "action":"EDITAR","version":row["version"],"sku_codigo":"MAT-001",
                "quantity":"4","needed_at":"2026-10-20","reason":"Teste",
            },self.operator)

    def test_buyer_can_edit_request_already_in_progress(self):
        row=self.request()
        row=pr.transition(self.db,row["id"],{
            "action":"ASSUMIR","version":row["version"],"reason":"Iniciar compra",
        },self.buyer)["request"]
        self.db.commit()
        edited=pr.transition(self.db,row["id"],{
            "action":"EDITAR","version":row["version"],"sku_codigo":"MAT-001",
            "quantity":"5","needed_at":"2026-10-20",
            "notes":"Quantidade corrigida","reason":"Solicitante confirmou a correção",
        },self.buyer)["request"]
        self.db.commit()
        self.assertEqual("EM_COMPRAS",edited["status"])
        self.assertEqual("5",edited["quantity"])

    def test_stale_version_rejected(self):
        row=self.request()
        pr.transition(self.db,row["id"],{"action":"ASSUMIR","version":1,"reason":"Teste"},self.buyer)
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"outro usuário"):
            pr.transition(self.db,row["id"],{"action":"CANCELAR","version":1,"reason":"Teste"},self.buyer)

    def test_no_manual_conclusion(self):
        row=self.request()
        with self.assertRaisesRegex(ValueError,"somente"):
            pr.transition(self.db,row["id"],{"action":"CONCLUIR","version":1,"reason":"Teste"},self.buyer)

    def test_confirm_order_closes_in_same_transaction_and_links_uuid(self):
        row=self.request()
        order=self.order([row["id"]])
        updated=pr.get(self.db,row["id"])
        self.assertEqual("CONCLUIDA",updated["status"])
        self.assertEqual(order["id"],updated["purchase_order_id"])
        self.assertIsNotNone(updated["completed_at"])
        listing=pr.listing(self.db,{})
        self.assertEqual("Fornecedor teste",listing["items"][0]["fornecedor_nome"])

    def test_failed_conversion_does_not_create_order_or_conclude(self):
        row=self.request(quantity="4")
        with self.assertRaisesRegex(ValueError,"quantidade"):
            self.order([row["id"]],qty="2")
        self.db.rollback()
        self.assertEqual("SOLICITADA",pr.get(self.db,row["id"])["status"])
        self.assertEqual(0,self.db.execute(text("select count(*) from erp_purchase_orders")).scalar())

    def test_wrong_sku_is_rejected(self):
        row=self.request()
        with self.assertRaises(ValueError): self.order([row["id"]],sku="OTHER")
        self.db.rollback()

    def test_sum_multiple_requests_same_sku(self):
        a,b=self.request(quantity="2"),self.request(quantity="3",sector="ADMINISTRATIVO")
        with self.assertRaises(ValueError): self.order([a["id"],b["id"]],qty="4")
        self.db.rollback()
        self.order([a["id"],b["id"]],qty="5")
        self.assertEqual(2,pr.listing(self.db,{})["counts"]["CONCLUIDA"])

    def test_request_cannot_complete_in_two_orders(self):
        row=self.request()
        self.order([row["id"]])
        with self.assertRaisesRegex(ValueError,"outro pedido"): self.order([row["id"]])
        self.db.rollback()
        self.assertEqual(1,self.db.execute(text("select count(*) from erp_purchase_orders")).scalar())

    def test_cancelled_request_cannot_convert(self):
        row=self.request()
        pr.transition(self.db,row["id"],{"action":"CANCELAR","version":1,"reason":"Teste"},self.buyer)
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"cancelada"): self.order([row["id"]])
        self.db.rollback()

    def test_order_retry_does_not_duplicate_completion_event(self):
        row=self.request()
        key=str(uuid4())
        a=self.order([row["id"]],key=key)
        b=self.order([row["id"]],key=key)
        self.assertEqual(a["id"],b["id"])
        self.assertEqual(2,len(pr.history(self.db,row["id"])["events"]))

    def test_non_buyer_cannot_emit_linked_order(self):
        row=self.request()
        with self.assertRaises(PermissionError): self.order([row["id"]],actor=self.pcp.username)
        self.db.rollback()

    def test_edit_cannot_drop_quantity_even_if_client_omits_request_ids(self):
        row=self.request(quantity="3")
        key=str(uuid4())
        self.order([row["id"]],qty="3",key=key)
        data=legacy.ErpSkuResolutionTest._payload(key,"MAT-001",2)
        with self.assertRaisesRegex(ValueError,"quantidade"):
            sync_legacy_purchase_order(self.db,data,self.buyer.username)
        self.db.rollback()
        qty=self.db.execute(text("select quantidade_pedida from erp_purchase_order_lines")).scalar()
        self.assertEqual(3,qty)

    def test_edit_keeps_completion_date_and_audits_order_snapshot(self):
        row=self.request()
        key=str(uuid4())
        self.order([row["id"]],key=key)
        completed=pr.get(self.db,row["id"])["completed_at"]
        sync_legacy_purchase_order(self.db,legacy.ErpSkuResolutionTest._payload(key,"MAT-001",4),self.buyer.username)
        self.assertEqual(completed,pr.get(self.db,row["id"])["completed_at"])
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual("PEDIDO_ATUALIZADO",event["action"])
        self.assertEqual("Fornecedor teste",event["after_data"]["purchase_order"]["fornecedor_nome"])

    def test_order_cancellation_reopens_and_preserves_history(self):
        row=self.request()
        order=self.order([row["id"]])
        cancel_purchase_order(self.db,order["id"],self.buyer.username,"Compra incorreta")
        current=pr.get(self.db,row["id"])
        self.assertEqual("SOLICITADA",current["status"])
        self.assertIsNone(current["purchase_order_id"])
        event=pr.history(self.db,row["id"])["events"][-1]
        self.assertEqual(order["id"],event["before_data"]["purchase_order_id"])

    def test_request_and_confirmation_do_not_change_stock(self):
        row=self.request()
        self.order([row["id"]])
        self.assertEqual(0,self.db.query(Movement).count())
        self.assertEqual(0,self.db.query(StockBalance).count())

    def test_listing_filters_and_notifications(self):
        self.request(origin="PCP",user=self.pcp,needed_at="2020-01-01",sector="PRODUÇÃO")
        self.request()
        data=pr.listing(self.db,{"origin":"PCP","q":"MAT","from":"2019-01-01","to":"2021-01-01"})
        self.assertEqual(1,data["total"])
        self.assertEqual(1,data["overdue"])
        self.assertEqual(2,pr.notifications(self.db)["new"])

    def test_prepare_is_buyer_only_and_rejects_completed(self):
        row=self.request()
        with self.assertRaises(PermissionError): pr.prepare(self.db,[row["id"]],self.operator)
        self.assertEqual(1,len(pr.prepare(self.db,[row["id"]],self.buyer)["items"]))
        self.order([row["id"]])
        with self.assertRaises(ValueError): pr.prepare(self.db,[row["id"]],self.buyer)

class PurchaseRequestRouteTests(unittest.TestCase):
    # Reuse fixtures only; run route tests without inheriting the domain suite.
    setUpBase = legacy.ErpSkuResolutionTest.setUp
    tearDown = legacy.ErpSkuResolutionTest.tearDown
    request = PurchaseRequestTests.request
    order = PurchaseRequestTests.order
    def setUp(self):
        PurchaseRequestTests.setUp(self)
        from flask import Flask
        from purchase_request_routes import register
        self.web=Flask(__name__, template_folder=str(APP/"templates"))
        self.web.secret_key="tests-only"
        self.actor=self.operator
        register(self.web,lambda:self.db,lambda:self.actor,lambda:True,
                 lambda db,actor:self.actor,lambda f:f,lambda f:f)
        self.client=self.web.test_client()
        with self.client.session_transaction() as session:
            session["purchase_requests_csrf"]="csrf"

    def test_local_creation_requires_csrf_and_forces_stock_origin(self):
        payload={"sku_codigo":"MAT-001","quantity":1,"needed_at":"2026-10-15",
                 "idempotency_key":str(uuid4()),"origin":"PCP","requested_by":"FAKE",
                 "sector":"GERAL","work_order_ids":[]}
        denied=self.client.post("/api/erp/purchase-requests",json=payload)
        self.assertEqual(403,denied.status_code)
        response=self.client.post("/api/erp/purchase-requests",json=payload,headers={"X-CSRF-Token":"csrf"})
        self.assertEqual(200,response.status_code)
        self.assertEqual("ESTOQUE",response.json["request"]["origin"])
        self.assertEqual("OPERADOR",response.json["request"]["requested_by"])

    def test_internal_pcp_creation_checks_actual_role(self):
        payload={"sku_codigo":"MAT-001","quantity":1,"needed_at":"2026-10-15",
                 "idempotency_key":str(uuid4()),"sector":"GERAL","work_order_ids":[]}
        self.assertEqual(403,self.client.post("/api/erp/internal/purchase-requests",json=payload).status_code)
        self.actor=self.pcp
        response=self.client.post("/api/erp/internal/purchase-requests",json=payload)
        self.assertEqual(200,response.status_code)
        self.assertEqual("PCP",response.json["request"]["origin"])

    def test_duplicate_rejection_is_shared_between_stock_and_pcp_apis(self):
        self.request(sector="PRODUÇÃO")
        self.actor=self.pcp
        response=self.client.post("/api/erp/internal/purchase-requests",json={
            "sku_codigo":"MAT-001","quantity":4,"needed_at":"2026-10-18",
            "sector":"PRODUÇÃO","work_order_ids":[],"idempotency_key":str(uuid4()),
        })
        self.assertEqual(400,response.status_code)
        self.assertIn("Solicitação duplicada",response.json["error"])
        self.assertEqual(1,self.db.execute(select(func.count()).select_from(pr.requests)).scalar_one())
        self.assertEqual(1,self.db.execute(select(func.count()).select_from(pr.events)).scalar_one())

    def test_internal_action_checks_buyer_in_database(self):
        row=self.request()
        path="/api/erp/internal/purchase-requests/"+row["id"]+"/action"
        payload={"action":"ASSUMIR","version":1,"reason":"Tratamento"}
        self.actor=self.pcp
        self.assertEqual(403,self.client.post(path,json=payload).status_code)
        self.actor=self.buyer
        self.assertEqual(200,self.client.post(path,json=payload).status_code)

    def test_get_history_and_active_sku_search(self):
        row=self.request()
        response=self.client.get("/api/erp/purchase-requests/"+row["id"]+"/history")
        self.assertEqual(200,response.status_code)
        self.assertEqual(1,len(response.json["events"]))
        options=self.client.get("/api/erp/purchase-requests/options?q=MAT")
        self.assertEqual(["MAT-001"],[r["sku_codigo"] for r in options.json["items"]])

    def test_open_work_order_options_are_available_to_requester_and_searchable(self):
        with patch("purchase_request_routes.active_work_orders",return_value=[{
            "work_order_id":str(uuid4()),"numero_os":"TA003163",
            "item_number":3163,"label":"O.S. TA003163 · 3163 · teste",
        }]) as lookup:
            response=self.client.get("/api/erp/purchase-requests/work-orders?q=3163")
        self.assertEqual(200,response.status_code)
        self.assertEqual("TA003163",response.json["items"][0]["numero_os"])
        lookup.assert_called_once_with(self.db,"3163",limit=50)

    def test_existing_order_options_requires_buyer_and_returns_covered_orders(self):
        order=self.order([],qty="5")
        row=self.request(quantity="2")
        path="/api/erp/purchase-requests/"+row["id"]+"/orders"
        self.actor=self.operator
        self.assertEqual(403,self.client.get(path).status_code)
        self.actor=self.buyer
        response=self.client.get(path)
        self.assertEqual(200,response.status_code)
        self.assertEqual(order["id"],response.json["items"][0]["id"])

    def test_queue_refresh_persists_legacy_anticipation_reclassification(self):
        data=legacy.ErpSkuResolutionTest._payload(str(uuid4()),"MAT-001","2")
        order=create_purchase_order(self.db,data,self.buyer.username)
        row=self.request()
        self.db.execute(text("update erp_purchase_orders set status='CONCLUIDA' where id=:id"),
                        {"id":order["id"]})
        self.db.commit()
        self.actor=self.buyer

        response=self.client.get("/api/erp/purchase-requests")

        self.assertEqual(200,response.status_code)
        self.assertEqual("COMPRA_NOVA",response.json["items"][0]["request_type"])
        self.assertEqual("COMPRA_NOVA",pr.get(self.db,row["id"])["request_type"])
        self.assertEqual("COMPRADOR",pr.history(self.db,row["id"])["events"][-1]["actor"])

    def test_invalid_payload_no_write(self):
        response=self.client.post("/api/erp/purchase-requests",json=["bad"],headers={"X-CSRF-Token":"csrf"})
        self.assertEqual(400,response.status_code)
        self.assertEqual(0,self.db.execute(select(func.count()).select_from(pr.requests)).scalar())

    def test_database_not_migrated_returns_actionable_503(self):
        pr.metadata.drop_all(self.engine)
        response=self.client.get("/api/erp/purchase-requests")
        self.assertEqual(503,response.status_code)
        self.assertIn("migração",response.json["error"])

if __name__ == "__main__": unittest.main()

