import unittest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

import test_erp_sku_resolution as fixtures
from models import Movement, SKU, StockBalance
from services.erp_service import (confirm_receipt, create_purchase_order,
                                  pending_receipt_orders, receipt_reversal_detail, reverse_receipt)
from services.estoque_service import cancel_movement, register_movement


class ReceiptReversalTest(unittest.TestCase):
    setUp = fixtures.ErpSkuResolutionTest.setUp
    tearDown = fixtures.ErpSkuResolutionTest.tearDown
    _payload = staticmethod(fixtures.ErpSkuResolutionTest._payload)
    _line = fixtures.ErpSkuResolutionTest._line

    def receive(self, order=None, quantity=2, sku=None):
        sku = sku or self.active_sku
        order = order or create_purchase_order(self.db, self._payload(str(uuid4()), sku.sku, quantity), 'COMPRADOR')
        line = self._line(order['id'])
        receipt = confirm_receipt(self.db, {
            'idempotency_key': str(uuid4()), 'purchase_order_id': order['id'], 'numero_nf': 'NF-TESTE',
            'lines': [{'purchase_order_line_id': line['id'], 'quantidade_fisica': quantity,
                       'quantidade_aprovada': quantity, 'resultado_inspecao': 'A'}],
        }, 'ALMOXARIFADO', self.user_id)
        return order, receipt

    def order_state(self, order):
        return self.db.execute(text('select * from erp_purchase_orders where id=:id'),
                               {'id': order['id']}).mappings().one()

    def test_integral_reversal_restores_pending_receipt_and_is_idempotent(self):
        order, receipt = self.receive()
        original = self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT').one()
        result = reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Entrada incorreta', reopen_technical=True)
        self.assertFalse(result['replayed'])
        self.assertEqual('EMITIDA', result['purchase_order_status'])
        self.assertEqual('EMITIDA', self.order_state(order)['status'])
        self.assertEqual(0, self._line(order['id'])['quantidade_recebida'])
        self.assertEqual(1, len(pending_receipt_orders(self.db)))
        self.assertEqual(Decimal('0'), self.db.query(StockBalance).one().saldo_atual)
        self.assertEqual('CANCELADA', original.movement_status)
        self.assertEqual(self.user_id, original.canceled_by)
        self.assertIsNotNone(original.reversal_movement_id)
        again = reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Repeticao', reopen_technical=True)
        self.assertTrue(again['replayed'])
        self.assertEqual(1, self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT_REVERSAL').count())
        self.assertEqual(1, self.db.execute(text("select count(*) from erp_audit_events where action='ESTORNADO'")).scalar())

    def test_technical_closure_reopens_only_with_explicit_choice(self):
        for reopen in (False, True):
            with self.subTest(reopen=reopen):
                order, receipt = self.receive()
                self.db.execute(text("update erp_purchase_orders set status='CONCLUIDA',technical_status='CONCLUIDA',technical_closed_by='COMPRADOR' where id=:id"), {'id': order['id']})
                self.db.commit()
                reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Estorno', reopen_technical=reopen)
                state = self.order_state(order)
                self.assertEqual('ABERTA' if reopen else 'CONCLUIDA', state['technical_status'])
                self.assertEqual('EMITIDA' if reopen else 'CONCLUIDA', state['status'])
                if reopen:
                    self.assertIsNone(state['technical_closed_by'])
                    event = self.db.execute(text("select actor from erp_audit_events where entity_id=:id and action='REABERTURA_TECNICA_POR_ESTORNO'"), {'id': order['id']}).scalar_one()
                    self.assertEqual('PAULO', event)

    def test_other_valid_receipt_is_preserved_and_order_stays_partial(self):
        order = create_purchase_order(self.db, self._payload(str(uuid4()), self.active_sku.sku, 5), 'COMPRADOR')
        _, first = self.receive(order, 2)
        _, second = self.receive(order, 3)
        reverse_receipt(self.db, second['id'], 'PAULO', self.user_id, 'Estorno parcial')
        self.assertEqual('PARCIALMENTE_RECEBIDA', self.order_state(order)['status'])
        self.assertEqual(2, self._line(order['id'])['quantidade_recebida'])
        self.assertEqual(Decimal('2'), self.db.query(StockBalance).one().saldo_atual)
        self.assertEqual('CONFIRMADO', self.db.execute(text('select status from erp_goods_receipts where id=:id'), {'id': first['id']}).scalar_one())

    def test_financial_entry_blocks_without_any_stock_change(self):
        order, receipt = self.receive()
        self.db.execute(text('insert into erp_purchase_order_financial_entries(id,purchase_order_id) values(:id,:order)'), {'id': str(uuid4()), 'order': order['id']})
        self.db.commit()
        with self.assertRaisesRegex(ValueError, 'baixa financeira'):
            reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Estorno', reopen_technical=True)
        self.assertEqual(Decimal('2'), self.db.query(StockBalance).one().saldo_atual)
        self.assertEqual(0, self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT_REVERSAL').count())
        self.assertEqual('CONFIRMADO', receipt_reversal_detail(self.db, receipt['id'])['receipt']['status'])

    def test_102_movements_26_skus_reverse_once_and_preserve_originals(self):
        skus = [SKU(sku=f'102-{n}', descricao=f'Vidro {n}', unidade='PC', active=True) for n in range(26)]
        self.db.add_all(skus)
        self.db.commit()
        payload = self._payload(str(uuid4()), skus[0].sku)
        payload['lines'] = [{'sku_codigo': skus[n % 26].sku, 'descricao_original': 'Componente', 'unidade': 'PC', 'quantidade_pedida': 1} for n in range(102)]
        order = create_purchase_order(self.db, payload, 'COMPRADOR')
        lines = self.db.execute(text('select id from erp_purchase_order_lines where purchase_order_id=:id order by numero_linha'), {'id': order['id']}).scalars().all()
        receipt = confirm_receipt(self.db, {
            'idempotency_key': str(uuid4()), 'purchase_order_id': order['id'], 'numero_nf': '235972',
            'lines': [{'purchase_order_line_id': line, 'quantidade_fisica': 1, 'quantidade_aprovada': 1, 'resultado_inspecao': 'A'} for line in lines],
        }, 'ALMOXARIFADO', self.user_id)
        preview = receipt_reversal_detail(self.db, receipt['id'])
        self.assertEqual((102, 26, 102), (len(preview['lines']), preview['sku_count'], preview['quantity']))
        self.assertFalse(preview['shortages'])
        reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Estorno da nota', reopen_technical=True)
        self.assertEqual(102, self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT', movement_status='CANCELADA').count())
        self.assertEqual(102, self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT_REVERSAL').count())
        self.assertTrue(all(b.saldo_atual == 0 for b in self.db.query(StockBalance).all()))
        self.assertEqual(102, len(pending_receipt_orders(self.db)))

    def test_insufficient_stock_rolls_back_all_items(self):
        other = SKU(sku='ZZ-LAST', descricao='Outro material', unidade='PC', active=True)
        self.db.add(other)
        self.db.commit()
        payload = self._payload(str(uuid4()), self.active_sku.sku)
        payload['lines'].append({'sku_codigo': other.sku, 'descricao_original': 'Outro', 'quantidade_pedida': 2})
        order = create_purchase_order(self.db, payload, 'COMPRADOR')
        lines = self.db.execute(text('select id from erp_purchase_order_lines where purchase_order_id=:id'), {'id': order['id']}).scalars().all()
        receipt = confirm_receipt(self.db, {'idempotency_key': str(uuid4()), 'purchase_order_id': order['id'],
            'lines': [{'purchase_order_line_id': line, 'quantidade_fisica': 2, 'quantidade_aprovada': 2, 'resultado_inspecao': 'A'} for line in lines]}, 'ALMOXARIFADO', self.user_id)
        register_movement(self.db, other, 'BAIXA', 1, self.user_id)
        with self.assertRaisesRegex(ValueError, 'negativo'):
            reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Estorno')
        self.assertEqual(0, self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT_REVERSAL').count())
        self.assertEqual(2, self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT', movement_status='ATIVA').count())
        self.assertEqual('RECEBIDA', self.order_state(order)['status'])
        self.assertEqual('CONFIRMADO', receipt_reversal_detail(self.db, receipt['id'])['receipt']['status'])

    def test_bom_component_reversal_and_quantity_multiplier(self):
        assembly = SKU(sku='30120033', grupo='30 - CJ', descricao='Conjunto', unidade='CJ', active=True)
        self.db.add(assembly)
        self.db.commit()
        self.db.add(fixtures.BomComponent(item_sku_id=assembly.id, component_sku_id=self.active_sku.id, quantidade=3))
        self.db.commit()
        order, receipt = self.receive(quantity=2, sku=assembly)
        self.assertEqual(Decimal('6'), self.db.query(StockBalance).filter_by(sku_id=self.active_sku.id).one().saldo_atual)
        reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Estorno CJ')
        self.assertEqual(0, self._line(order['id'])['quantidade_recebida'])
        self.assertEqual(Decimal('0'), self.db.query(StockBalance).filter_by(sku_id=self.active_sku.id).one().saldo_atual)

    def test_prior_individual_cancellation_is_not_applied_twice(self):
        order, receipt = self.receive()
        movement = self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT').one()
        adjustment = register_movement(self.db, self.active_sku, 'AJUSTE', -2, self.user_id)
        movement.movement_status = 'CANCELADA'
        movement.reversal_movement_id = adjustment.id
        self.db.commit()
        reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Regularizar recebimento')
        self.assertEqual(Decimal('0'), self.db.query(StockBalance).one().saldo_atual)
        self.assertEqual(0, self._line(order['id'])['quantidade_recebida'])

    def test_direct_cancellation_is_blocked_even_for_admin(self):
        _, receipt = self.receive()
        movement = self.db.query(Movement).filter_by(source_type='GOODS_RECEIPT').one()
        with self.assertRaisesRegex(ValueError, 'recebimento completo'):
            cancel_movement(self.db, movement, self.user_id, 'Individual', allow_any=True)

    def test_reason_is_required(self):
        _, receipt = self.receive()
        with self.assertRaisesRegex(ValueError, 'motivo'):
            reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, ' ')

    def test_missing_stock_link_blocks_integral_reversal(self):
        order, receipt = self.receive()
        self.db.execute(text('delete from erp_stock_receipt_links'))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, 'vinculo completo'):
            reverse_receipt(self.db, receipt['id'], 'PAULO', self.user_id, 'Estorno')
        self.assertEqual(Decimal('2'), self.db.query(StockBalance).one().saldo_atual)
        self.assertEqual('RECEBIDA', self.order_state(order)['status'])


if __name__ == '__main__':
    unittest.main()
