import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReceiptReversalRoutesTest(unittest.TestCase):
    def test_admin_preview_confirmation_replay_and_operator_denial(self):
        script = r'''
import sys
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import event, text
sys.path.insert(0, str(__import__('pathlib').Path.cwd().parent / 'tests'))
import test_erp_sku_resolution as fixtures
import app as app_module
from database import SessionLocal, engine
from models import Movement, SKU, User
from services.erp_service import create_purchase_order, confirm_receipt

@event.listens_for(engine, 'connect')
def functions(conn, record):
    conn.create_function('now', 0, lambda: '2026-10-16 10:00:00')
    conn.create_function('greatest', -1, lambda *values: max(values))
    conn.create_function('jsonb_build_object', -1, lambda *args: '{}')

engine.dispose()
@event.listens_for(engine, 'before_cursor_execute', retval=True)
def strip_locks(conn, cursor, statement, parameters, context, executemany):
    return __import__('re').sub(r'\s+for\s+update(?:\s+of\s+\w+)?', '', statement, flags=__import__('re').I), parameters

with engine.begin() as conn:
    for statement in fixtures.ERP_TEST_SCHEMA:
        conn.execute(text(statement))
database = SessionLocal()
admin = database.query(User).filter(User.role.in_(('ADMIN','ADM'))).first()
operator = User(username='OP-ESTORNO', password_hash='hash', role='OPERADOR', active=True)
sku = SKU(sku='NF-ROUTE', descricao='Vidro teste', unidade='PC', active=True)
database.add_all([operator, sku]); database.commit()
operator_id, admin_id = operator.id, admin.id
order = create_purchase_order(database, {'idempotency_key':str(uuid4()), 'numero_oc':'2810', 'lines':[{'sku_codigo':sku.sku,'descricao_original':'Vidro teste','quantidade_pedida':2}]},'COMPRADOR')
line = database.execute(text('select id from erp_purchase_order_lines where purchase_order_id=:id'), {'id':order['id']}).scalar_one()
receipt = confirm_receipt(database, {'idempotency_key':str(uuid4()),'purchase_order_id':order['id'],'numero_nf':'235972','lines':[{'purchase_order_line_id':line,'quantidade_fisica':2,'quantidade_aprovada':2,'resultado_inspecao':'A'}]}, 'ALMOXARIFADO', admin_id)
receipt_id = receipt['id']
movement_id = database.query(Movement).filter_by(source_type='GOODS_RECEIPT').one().id
database.close(); SessionLocal.remove()
app_module.app.config.update(TESTING=True)
client = app_module.app.test_client()
path = f'/erp/recebimentos/{receipt_id}/estorno'

def login(user_id):
    with client.session_transaction() as session:
        session.clear(); session['user_id'] = user_id

login(operator_id)
for response in [client.get(path),client.post(path,data={'motivo':'Teste'}),client.post(f'/api/erp/receipts/{receipt_id}/reverse',json={'motivo':'Teste'})]:
    assert response.status_code in (302,403), response.status_code
login(admin_id)
response = client.get(path)
assert response.status_code == 200, response.data[:300]
assert b'235972' in response.data and b'2810' in response.data
assert b'Confirmar estorno integral' in response.data
assert b'Reabrir O.C.' in response.data
response = client.post(path, data={'motivo':' ', 'reabrir_pedido':'1'})
assert b'Informe o motivo' in response.data
response = client.post(f'/movimentacoes/{movement_id}/cancelar',data={'reason':'Individual'})
assert response.status_code == 302
database = SessionLocal(); assert database.get(Movement,movement_id).movement_status == 'ATIVA'
database.close(); SessionLocal.remove()
response = client.post(path,data={'motivo':'Entrada incorreta solicitada por PAULO','reabrir_pedido':'1'},follow_redirects=True)
assert response.status_code == 200, response.data[:500]
assert b'ESTORNADO' in response.data
assert b'Confirmar estorno integral' not in response.data
response = client.post(f'/api/erp/receipts/{receipt_id}/reverse',json={'motivo':'Repeticao','reabrir_pedido':True})
assert response.status_code == 200 and response.json['replayed'] is True
with patch.object(app_module,'reverse_receipt',side_effect=RuntimeError('nao expor detalhes')):
    response = client.post(f'/api/erp/receipts/{receipt_id}/reverse',json={'motivo':'Teste'})
    assert response.status_code == 500 and response.is_json and response.json['ok'] is False
    assert 'nao expor detalhes' not in response.get_data(as_text=True)
print('Rotas: previa, permissao, estorno, bloqueio individual e repeticao OK')
'''
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env.update({'PYTHONPATH': str(ROOT / 'estoque_app'),
                        'ESTOQUE_DATABASE_MODE': 'online',
                        'DATABASE_URL': f'sqlite:///{Path(tmp).as_posix()}/routes.sqlite',
                        'ERP_FEATURE_FLAG': 'true', 'ERP_SHARED_RBAC_ENABLED': 'false',
                        'ERP_PORTAL_SSO_ENABLED': '0', 'ERP_MOVEMENT_CONTEXT_ENABLED': 'false',
                        'ERP_AUTH_COOKIE_SECURE': 'false'})
            result = subprocess.run([sys.executable, '-W', 'ignore', '-c', script],
                                    cwd=ROOT/'estoque_app', env=env, capture_output=True,
                                    text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
