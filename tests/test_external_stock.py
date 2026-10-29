import json
import unittest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
import test_erp_sku_resolution as fixtures
from models import SKU, BomComponent, Movement, StockBalance
from services import external_stock as external
from services.erp_service import confirm_receipt, create_purchase_order, reverse_receipt, _explode_receipt_bom
from services.work_order_needs_service import _bom_catalog, _normalize_phantom_requirements
from services.purchase_request_components import explode_requests


class ExternalStockTest(unittest.TestCase):
    tearDown = fixtures.ErpSkuResolutionTest.tearDown
    _payload = staticmethod(fixtures.ErpSkuResolutionTest._payload)
    _line = fixtures.ErpSkuResolutionTest._line

    def setUp(self):
        fixtures.ErpSkuResolutionTest.setUp(self)
        self.db.execute(text('''create table erp_external_stock_items(
            sku text primary key,location text default 'SPANDY',activated_at datetime default current_timestamp)'''))
        self.db.execute(text('''create table erp_external_stock_movements(
            id text primary key,sku text,kind text,delta numeric,receipt_id text,receipt_line_id text,
            bank_sku text,bank_quantity numeric,bom_snapshot text,reason text,actor text,user_id integer,
            request_key text unique,reverses_id text unique,created_at datetime default current_timestamp)'''))
        self.foam = SKU(sku='10220272', descricao='Espuma', unidade='pc', active=True)
        self.bank = SKU(sku='10200001', descricao='BCO 2 L', unidade='pc', grupo='10 - INSUMO', active=True)
        self.kit = SKU(sku='30200001', descricao='CJ BANCOS', unidade='cj', grupo='30 - CONJUNTO / KIT', active=True)
        self.db.add_all([self.foam, self.bank, self.kit]); self.db.flush()
        self.db.add_all([
            BomComponent(item_sku_id=self.bank.id,component_sku_id=self.foam.id,quantidade=2),
            BomComponent(item_sku_id=self.kit.id,component_sku_id=self.bank.id,quantidade=3)])
        self.db.execute(text("insert into erp_external_stock_items(sku) values('10220272')")); self.db.commit()

    def move(self, kind, quantity, **kwargs):
        return external.manual_movement(self.db, {
            'sku':self.foam.sku, 'kind':kind, 'quantity':quantity, 'reason':'Conferência Spandy',
            'idempotency_key':str(uuid4()), **kwargs}, self.user.username, self.user_id)

    def receive(self, sku=None, quantity=2, result='A', key=None, order=None):
        sku = sku or self.bank
        order = order or create_purchase_order(self.db, self._payload(str(uuid4()),sku.sku,quantity), 'PCP')
        line = self._line(order['id'])
        data = {'idempotency_key':key or str(uuid4()), 'purchase_order_id':order['id'], 'numero_nf':'NF-ESPUMAS',
                'lines':[{'purchase_order_line_id':line['id'],'quantidade_fisica':quantity,'resultado_inspecao':result}]}
        receipt = confirm_receipt(self.db,data,self.user.username,self.user_id)
        return receipt, data, order

    def test_bank_insumo_receipt_consumes_bom_externally_without_internal_foam_entry(self):
        self.move('INITIAL',10)
        receipt, _, _ = self.receive(quantity=3)
        self.assertEqual(Decimal(4), external.balance(self.db,self.foam.sku))
        self.assertEqual(0,self.db.query(Movement).filter_by(sku_id=self.foam.id).count())
        self.assertEqual(Decimal(3),self.db.query(StockBalance).filter_by(sku_id=self.bank.id).one().saldo_atual)
        snapshot = self.db.execute(text("select bom_snapshot from erp_external_stock_movements where kind='RECEIPT'")).scalar_one()
        self.assertEqual({'10220272':Decimal(6)}, {k:Decimal(v) for k,v in json.loads(snapshot).items()})
        self.assertEqual(2,self.db.execute(text('select count(*) from erp_external_stock_movements')).scalar_one())

    def test_repeated_receipt_does_not_consume_twice(self):
        first, data, _ = self.receive()
        again = confirm_receipt(self.db,data,self.user.username,self.user_id)
        self.assertTrue(again['replayed']);self.assertEqual(first['id'],again['id'])
        self.assertEqual(Decimal(-4),external.balance(self.db,self.foam.sku))

    def test_recursive_kit_consumption_multiplies_quantities_once(self):
        self.receive(self.kit, quantity=2)
        self.assertEqual(Decimal(-12),external.balance(self.db,self.foam.sku))
        self.assertEqual([(self.bank.id,Decimal(6))],[(sku.id,qty) for sku,qty in _explode_receipt_bom(self.db,self.kit,2)])
        self.assertEqual(0,self.db.query(Movement).filter_by(sku_id=self.foam.id).count())

    def test_reversal_uses_original_snapshot_even_after_bom_changes(self):
        self.move('INITIAL',20)
        receipt, _, _ = self.receive(quantity=2)
        edge=self.db.query(BomComponent).filter_by(item_sku_id=self.bank.id).one()
        edge.quantidade=50;self.db.commit()
        reverse_receipt(self.db,receipt['id'],self.user.username,self.user_id,'Estorno de teste')
        self.assertEqual(Decimal(20),external.balance(self.db,self.foam.sku))
        reverse_receipt(self.db,receipt['id'],self.user.username,self.user_id,'Repetição')
        self.assertEqual(1,self.db.execute(text("select count(*) from erp_external_stock_movements where kind='REVERSAL'")).scalar_one())

    def test_rejected_banks_do_not_consume(self):
        self.receive(quantity=3,result='D')
        self.assertEqual(Decimal(0),external.balance(self.db,self.foam.sku))

    def test_partial_receipts_consume_only_approved_quantities(self):
        order=create_purchase_order(self.db,self._payload(str(uuid4()),self.bank.sku,5),'PCP')
        self.receive(quantity=2,order=order);self.receive(quantity=3,order=order)
        self.assertEqual(Decimal(-10),external.balance(self.db,self.foam.sku))

    def test_partial_component_inspection_consumes_actual_banks_not_whole_kit(self):
        order=create_purchase_order(self.db,self._payload(str(uuid4()),self.kit.sku,2),'PCP')
        line=self._line(order['id'])
        confirm_receipt(self.db,{'idempotency_key':str(uuid4()),'purchase_order_id':order['id'],
            'lines':[{'purchase_order_line_id':line['id'],'component_receipts':[
                {'sku_id':self.bank.id,'resultado_inspecao':'A','quantidade_fisica':2}]}]},
            self.user.username,self.user_id)
        self.assertEqual(Decimal(-4),external.balance(self.db,self.foam.sku))
        self.assertEqual(0,self.db.query(Movement).filter_by(sku_id=self.foam.id).count())

    def test_initial_entry_manual_and_absolute_adjustment_have_audit_and_no_internal_movements(self):
        self.move('INITIAL',10);self.move('ENTRY',5);self.move('MANUAL',3)
        data={'idempotency_key':'count-1'}
        self.move('ADJUSTMENT',7,**data);replay=self.move('ADJUSTMENT',7,**data)
        self.assertTrue(replay['replayed']);self.assertEqual(Decimal(7),external.balance(self.db,self.foam.sku))
        self.assertEqual(0,self.db.query(Movement).count())
        with self.assertRaisesRegex(ValueError,'Saldo inicial já'):self.move('INITIAL',2)
        with self.assertRaisesRegex(ValueError,'Identificador'):self.move('ADJUSTMENT',9,**data)
        self.assertEqual(4,self.db.execute(text('select count(*) from erp_external_stock_movements')).scalar_one())

    def test_manual_requires_valid_quantity_reason_and_external_sku(self):
        for value in ('NaN','Infinity','-1','0'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.move('MANUAL',value)
        with self.assertRaises(ValueError):self.move('ENTRY',2,reason=' ')
        with self.assertRaises(ValueError):self.move('ENTRY',2,sku=self.bank.sku)

    def test_foam_does_not_replace_bank_demand_or_generate_internal_need(self):
        from services.estoque_service import bom_components_for_sku
        self.assertEqual([],bom_components_for_sku(self.db,self.bank))
        _,children=_bom_catalog(self.db)
        need,_=_normalize_phantom_requirements([
            {'codigo':self.kit.sku,'qtd':2}, {'codigo':self.bank.sku,'qtd':6},
            {'codigo':self.foam.sku,'qtd':12}],children)
        self.assertEqual({self.bank.sku:Decimal(6)},dict(need))

    def test_purchase_projection_excludes_external_component_but_keeps_bank(self):
        rows=explode_requests([{'sku_codigo':self.kit.sku,'quantity':2}],
            {self.kit.sku:{'grupo':'30'},self.bank.sku:{'grupo':'10'},self.foam.sku:{}},
            [{'parent_sku':self.kit.sku,'component_sku':self.bank.sku,'quantidade':3},
             {'parent_sku':self.bank.sku,'component_sku':self.foam.sku,'quantidade':2}])
        self.assertEqual([(self.bank.sku,Decimal(6))],[(r['sku_codigo'],r['quantity']) for r in rows])

    def test_fractional_nested_consumption_and_cycle_protection(self):
        graph={'CJ':[('PP',Decimal('0.8'))],'PP':[('10220272',Decimal('2.5'))]}
        self.assertEqual({'10220272':Decimal('6')},external.external_requirements('CJ',3,graph))
        graph['PP']=[('CJ',Decimal(1))]
        with self.assertRaisesRegex(ValueError,'cíclica'):external.external_requirements('CJ',1,graph)

    def test_summary_has_unknown_initial_and_receipt_reference(self):
        self.receive(quantity=1)
        summary=external.summary(self.db)
        self.assertFalse(summary['items'][0]['initialized'])
        self.assertEqual(Decimal(-2),Decimal(summary['items'][0]['balance']))
        self.assertEqual('NF-ESPUMAS',summary['movements'][0]['numero_nf'])

    def test_receipt_rollback_leaves_no_external_or_internal_movements(self):
        order=create_purchase_order(self.db,self._payload(str(uuid4()),self.bank.sku,1),'PCP')
        self.db.execute(text("delete from erp_external_stock_items"));self.db.commit()
        with self.assertRaisesRegex(ValueError,'não habilitado'):self.receive(quantity=1,order=order)
        self.db.rollback()
        self.assertEqual(0,self.db.query(Movement).count())
        self.assertEqual(0,self.db.execute(text('select count(*) from erp_goods_receipts')).scalar_one())

    def test_unrelated_insumo_bom_is_not_reinterpreted_or_blocked(self):
        # Legacy technical-only cycles/quantities do not concern external foam.
        self.db.add(BomComponent(item_sku_id=self.active_sku.id,
                                 component_sku_id=self.active_sku.id,quantidade=0))
        self.db.commit()
        self.receive(self.active_sku,quantity=1)
        self.assertEqual(0,self.db.execute(text('select count(*) from erp_external_stock_movements')).scalar_one())


if __name__=='__main__':unittest.main()
