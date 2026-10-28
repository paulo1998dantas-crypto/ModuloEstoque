import unittest
from decimal import Decimal
from sqlalchemy import select, func
import test_purchase_requests as fixtures
from services import purchase_requests as pr
from models import Movement, StockBalance


class ManualCompletionTests(unittest.TestCase):
    setUpBase=fixtures.PurchaseRequestTests.setUpBase
    setUp=fixtures.PurchaseRequestTests.setUp
    tearDown=fixtures.PurchaseRequestTests.tearDown
    request=fixtures.PurchaseRequestTests.request
    order=fixtures.PurchaseRequestTests.order

    def complete(self,row,**extra):
        payload=dict(action='CONCLUIR_MANUAL',version=row['version'],confirmation=row['id'],
            reason='Necessidade atendida por estoque existente',
            evidence='Conferido saldo e demanda em 28/10/2026; nenhuma compra adicional necessária.')
        payload.update(extra)
        result=pr.transition(self.db,row['id'],payload,self.buyer)['request']
        self.db.commit()
        return result

    def test_manual_completion_without_order_is_audited_and_does_not_move_stock(self):
        row=self.request(quantity='3')
        result=self.complete(row)
        self.assertEqual('CONCLUIDA',result['status'])
        self.assertEqual('MANUAL',result['completion_mode'])
        self.assertIsNone(result['purchase_order_id'])
        self.assertEqual([],result['purchase_orders'])
        self.assertEqual(Decimal(0),Decimal(result['remaining_quantity']))
        self.assertEqual(Decimal(3),Decimal(result['fulfilled_without_order_quantity']))
        self.assertEqual(self.buyer.username,result['completed_by'])
        event=pr.history(self.db,row['id'])['events'][-1]
        self.assertEqual('CONCLUIR_MANUAL',event['action'])
        self.assertEqual(result['completion_evidence'],event['after_data']['completion_evidence'])
        self.assertEqual('SOLICITADA',event['before_data']['status'])
        self.assertEqual(0,self.db.query(Movement).count())
        self.assertEqual(0,self.db.query(StockBalance).count())
        self.assertEqual(0,self.db.execute(select(func.count()).select_from(pr.orders)).scalar_one())

    def test_requires_confirmation_reason_evidence_buyer_and_current_version(self):
        row=self.request()
        for changes in (dict(confirmation='other'),dict(reason='curto'),dict(evidence=''),dict(evidence='x'*4001),dict(version=0)):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.complete(row,**changes)
            self.db.rollback()
        with self.assertRaises(PermissionError):
            pr.transition(self.db,row['id'],dict(action='CONCLUIR_MANUAL',version=1,
                confirmation=row['id'],reason='Atendimento confirmado',evidence='Conferência de estoque'),self.pcp)
        self.db.rollback()
        self.assertEqual('SOLICITADA',pr.get(self.db,row['id'])['status'])
        self.assertEqual(1,len(pr.history(self.db,row['id'])['events']))

    def test_partial_allocation_is_preserved_and_only_remainder_is_manual(self):
        order=self.order([],qty='2')
        row=self.request(quantity='3')
        row=pr.transition(self.db,row['id'],dict(action='ALOCAR_PEDIDO',version=row['version'],
            reason='Alocar duas peças já compradas',allocations=[dict(purchase_order_id=order['id'],quantity='2')]),self.buyer)['request']
        self.db.commit()
        links=pr.active_allocations(self.db,row['id'])
        result=self.complete(row)
        self.assertEqual(Decimal(2),Decimal(result['allocated_quantity']))
        self.assertEqual(Decimal(1),Decimal(result['fulfilled_without_order_quantity']))
        self.assertEqual(Decimal(0),Decimal(result['remaining_quantity']))
        self.assertEqual(links,pr.active_allocations(self.db,row['id']))
        with self.assertRaisesRegex(ValueError,'Reabra'):
            pr.transition(self.db,row['id'],dict(action='ALOCAR_PEDIDO',version=result['version'],
                reason='Alterar distribuição',replace_allocations=True,allocations=[dict(purchase_order_id=order['id'],quantity='2')]),self.buyer)
        self.db.rollback()

    def test_reopen_manual_preserves_audit_and_restores_pending_quantity(self):
        row=self.complete(self.request(quantity='3'))
        reopened=pr.transition(self.db,row['id'],dict(action='REABRIR',version=row['version'],
            reason='Atendimento precisa ser revisto'),self.buyer)['request']
        self.db.commit()
        self.assertEqual('SOLICITADA',reopened['status'])
        self.assertEqual('PEDIDO',reopened['completion_mode'])
        self.assertIsNone(reopened['completed_at'])
        self.assertIsNone(reopened['completion_evidence'])
        self.assertEqual(Decimal(3),Decimal(reopened['remaining_quantity']))
        events=pr.history(self.db,row['id'])['events']
        self.assertEqual('CONCLUIR_MANUAL',events[-2]['action'])
        self.assertTrue(events[-2]['after_data']['completion_evidence'])
        self.assertEqual('REABRIR',events[-1]['action'])

    def test_order_confirmation_cannot_bypass_manual_completion(self):
        row=self.complete(self.request(quantity='3'))
        with self.assertRaisesRegex(ValueError,'Reabra'):
            self.order([row['id']],qty='3')
        self.db.rollback()
        saved=pr.get(self.db,row['id'])
        self.assertEqual('MANUAL',saved['completion_mode'])
        self.assertEqual('CONCLUIDA',saved['status'])
        self.assertIsNone(saved['purchase_order_id'])
        self.assertEqual([],pr.active_allocations(self.db,row['id']))

    def test_completed_or_cancelled_request_cannot_be_manually_completed_again(self):
        row=self.complete(self.request())
        with self.assertRaises(ValueError):self.complete(row)
        self.db.rollback()
        row=self.request(sector='PRODUÇÃO')
        cancelled=pr.transition(self.db,row['id'],dict(action='CANCELAR',version=row['version'],reason='Demanda cancelada'),self.buyer)['request']
        self.db.commit()
        with self.assertRaises(ValueError):self.complete(cancelled)
        self.db.rollback()


class ManualCompletionRouteTests(unittest.TestCase):
    setUpBase=fixtures.PurchaseRequestTests.setUpBase
    setUp=fixtures.PurchaseRequestRouteTests.setUp
    tearDown=fixtures.PurchaseRequestTests.tearDown
    request=fixtures.PurchaseRequestTests.request

    def test_internal_api_authorizes_manual_completion_and_commits_audit(self):
        row=self.request()
        payload=dict(action='CONCLUIR_MANUAL',version=1,confirmation=row['id'],
            reason='Demanda atendida pelo estoque',evidence='Saldo conferido com necessidade atual por O.S.')
        path='/api/erp/internal/purchase-requests/'+row['id']+'/action'
        self.actor=self.pcp
        self.assertEqual(403,self.client.post(path,json=payload).status_code)
        self.actor=self.buyer
        result=self.client.post(path,json=payload)
        self.assertEqual(200,result.status_code)
        self.assertEqual('MANUAL',result.json['request']['completion_mode'])
        self.assertEqual('CONCLUIDA',pr.get(self.db,row['id'])['status'])
        self.assertEqual('CONCLUIR_MANUAL',pr.history(self.db,row['id'])['events'][-1]['action'])

if __name__=='__main__':unittest.main()
