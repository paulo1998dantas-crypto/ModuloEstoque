"""Independent, append-only external-stock ledger. Caller owns the transaction.

No call in this module writes movements or stock_balances. Receipt snapshots
are immutable: a later BOM edit cannot change a historic consumption/reversal.
"""
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import inspect, text

from external_materials import EXTERNAL_MATERIAL_SKUS, is_external_material


def number(value, *, positive=False):
    try:
        result = Decimal(str(value).strip().replace(',', '.'))
        if not result.is_finite() or (positive and result <= 0):
            raise InvalidOperation()
        if result != result.quantize(Decimal('0.000001')) or abs(result) >= Decimal('1e14'):
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Quantidade inválida; informe até seis casas decimais.')


def schema_ready(db):
    inspector = inspect(db.connection())
    return all(inspector.has_table(table) for table in (
        'erp_external_stock_items', 'erp_external_stock_movements'))


def require_schema(db):
    if not schema_ready(db):
        raise ValueError('Aplique a migração de estoque externo antes de usar Baixa externa.')


def _locked_items(db):
    # A stable lock order also serializes concurrent receipts/manual adjustments.
    suffix = ' for update' if db.get_bind().dialect.name == 'postgresql' else ''
    rows = db.execute(text('select sku from erp_external_stock_items order by sku' + suffix)).all()
    return {row[0] for row in rows}


def balance(db, sku):
    return Decimal(str(db.execute(text('''
        select coalesce(sum(delta),0) from erp_external_stock_movements where sku=:sku
    '''), {'sku': sku}).scalar_one()))


def _insert(db, *, sku, kind, delta, actor, user_id, request_key, reason,
            receipt_id=None, receipt_line_id=None, bank_sku=None, bank_quantity=None,
            snapshot=None, reverses_id=None):
    existing = db.execute(text('''select * from erp_external_stock_movements
                                  where request_key=:key'''), {'key': request_key}).mappings().first()
    if existing:
        if (existing['sku'] != sku or existing['kind'] != kind
                or number(existing['delta']) != number(delta)
                or existing['reason'] != reason or existing['user_id'] != user_id):
            raise ValueError('Identificador já utilizado por outro lançamento externo.')
        return {'id': existing['id'], 'replayed': True}
    movement_id = str(uuid4())
    snapshot_sql = 'cast(:snapshot as jsonb)' if db.get_bind().dialect.name == 'postgresql' else ':snapshot'
    db.execute(text(f'''insert into erp_external_stock_movements(
        id,sku,kind,delta,receipt_id,receipt_line_id,bank_sku,bank_quantity,
        bom_snapshot,reason,actor,user_id,request_key,reverses_id
    ) values(:id,:sku,:kind,:delta,:receipt,:line,:bank,:quantity,
        {snapshot_sql},:reason,:actor,:user,:key,:reverses)'''), {
        'id': movement_id, 'sku': sku, 'kind': kind, 'delta': number(delta),
        'receipt': str(receipt_id) if receipt_id else None,
        'line': str(receipt_line_id) if receipt_line_id else None,
        'bank': bank_sku, 'quantity': bank_quantity,
        'snapshot': json.dumps(snapshot or {}, ensure_ascii=False),
        'reason': reason, 'actor': actor, 'user': user_id,
        'key': request_key, 'reverses': reverses_id,
    })
    return {'id': movement_id, 'replayed': False}


def manual_movement(db, data, actor, user_id):
    require_schema(db)
    sku = str(data.get('sku') or '').strip()
    kind = str(data.get('kind') or '').strip().upper()
    reason = str(data.get('reason') or '').strip()
    key = str(data.get('idempotency_key') or '').strip()
    if not reason or len(reason) > 2000 or not key or len(key) > 150:
        raise ValueError('Informe motivo e identificador válido do lançamento.')
    if kind not in {'INITIAL', 'ENTRY', 'MANUAL', 'ADJUSTMENT'}:
        raise ValueError('Tipo de movimento externo inválido.')
    if sku not in _locked_items(db) or not is_external_material(sku):
        raise ValueError('SKU não habilitado no estoque externo.')
    quantity = number(data.get('quantity'))
    # Resolve replay before absolute adjustment: current balance has changed.
    old = db.execute(text('select * from erp_external_stock_movements where request_key=:key'),
                     {'key': 'manual:' + key}).mappings().first()
    if old:
        snapshot = old['bom_snapshot']
        snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        if (old['sku'] != sku or old['kind'] != kind or snapshot.get('input_quantity') != str(quantity)
                or old['reason'] != reason or old['user_id'] != user_id):
            raise ValueError('Identificador já utilizado por outro lançamento externo.')
        return {'id': old['id'], 'replayed': True}
    if kind == 'INITIAL':
        if quantity < 0:
            raise ValueError('Saldo inicial não pode ser negativo.')
        if db.execute(text("select count(*) from erp_external_stock_movements where sku=:sku and kind='INITIAL'"),
                      {'sku': sku}).scalar_one():
            raise ValueError('Saldo inicial já informado. Use ajuste de saldo com motivo.')
        delta = quantity
    elif kind == 'ADJUSTMENT':
        if quantity < 0:
            raise ValueError('Saldo contado não pode ser negativo.')
        delta = quantity - balance(db, sku)
    else:
        if quantity <= 0:
            raise ValueError('Informe uma quantidade maior que zero.')
        delta = -quantity if kind == 'MANUAL' else quantity
    return _insert(db, sku=sku, kind=kind, delta=delta, actor=actor, user_id=user_id,
                   request_key='manual:' + key, reason=reason,
                   snapshot={'input_quantity': str(quantity)})


def _graph(db, roots):
    """Current authoritative Cadastro BOM in production; local graph in SQLite."""
    graph = defaultdict(list)
    if db.get_bind().dialect.name == 'postgresql':
        rows = db.execute(text('''
            with recursive nodes(code) as (
                select value from jsonb_array_elements_text(cast(:roots as jsonb))
                union
                select b.component_sku from cadastro_bom_componentes b join nodes n on b.parent_sku=n.code
            )
            select b.parent_sku,b.component_sku,b.quantidade
              from cadastro_bom_componentes b join nodes n on n.code=b.parent_sku
        '''), {'roots': json.dumps(sorted(roots))}).mappings()
        for row in rows:
            graph[row['parent_sku']].append((row['component_sku'], number(row['quantidade'])))
    else:
        from models import BomComponent, SKU
        skus = {item.id: item.sku for item in db.query(SKU).all()}
        for edge in db.query(BomComponent).all():
            if edge.item_sku_id in skus and edge.component_sku_id in skus:
                graph[skus[edge.item_sku_id]].append((skus[edge.component_sku_id], number(edge.quantidade)))
    return graph


def external_requirements(code, quantity, graph):
    result = defaultdict(lambda: Decimal(0))

    def walk(current, amount, path):
        if current in path or len(path) >= 64:
            raise ValueError('B.O.M. externa cíclica ou com profundidade excessiva: ' + ' > '.join((*path, current)))
        if is_external_material(current):
            result[current] += amount
            return
        for child, factor in graph.get(current, []):
            if factor <= 0:
                raise ValueError(f'Consumo inválido na B.O.M. {current} / {child}.')
            walk(child, amount * factor, (*path, current))

    walk(code, number(quantity, positive=True), ())
    return {code: number(qty, positive=True) for code, qty in result.items()}


def consume_receipt(db, receipt_id, actor, user_id):
    """Only called from new receipt confirmation, before the single commit.

    Rows inspecting actual bank components consume those banks, not the whole
    ordered kit. Whole-kit rows use the complete recursive BOM once. Rejected
    quantities do not consume stock. No historic scan/backfill takes place.
    """
    lines = [dict(row) for row in db.execute(text('''
        select l.id,coalesce(nullif(l.sku_codigo,''),s.sku) as sku_codigo,l.quantidade_aprovada
          from erp_goods_receipt_lines l left join skus s on s.id=l.sku_id
         where l.goods_receipt_id=:receipt and l.quantidade_aprovada>0 order by l.id
    '''), {'receipt': receipt_id}).mappings()]
    lines = [line for line in lines if not is_external_material(line['sku_codigo'])]
    if not lines:
        return
    graph = _graph(db, {line['sku_codigo'] for line in lines})
    planned = [(line, external_requirements(line['sku_codigo'], line['quantidade_aprovada'], graph))
               for line in lines]
    if not any(parts for _, parts in planned):
        return
    require_schema(db)
    enabled = _locked_items(db)
    for line, parts in planned:
        for sku in sorted(parts):
            if sku not in enabled:
                raise ValueError(f'Estoque externo não habilitado para {sku}.')
            _insert(db, sku=sku, kind='RECEIPT', delta=-parts[sku], actor=actor, user_id=user_id,
                    receipt_id=receipt_id, receipt_line_id=line['id'], bank_sku=line['sku_codigo'],
                    bank_quantity=number(line['quantidade_aprovada']),
                    request_key=f"receipt:{line['id']}:{sku}", reason='Consumo pela entrada aprovada de bancos.',
                    snapshot={code: str(qty) for code, qty in parts.items()})


def reverse_receipt_consumption(db, receipt_id, actor, user_id, reason):
    if not schema_ready(db):
        return
    _locked_items(db)
    rows = list(db.execute(text("""select * from erp_external_stock_movements
                where receipt_id=:receipt and kind='RECEIPT' order by sku,id"""),
                           {'receipt': str(receipt_id)}).mappings())
    for row in rows:
        snapshot = row['bom_snapshot']
        _insert(db, sku=row['sku'], kind='REVERSAL', delta=-number(row['delta']),
                actor=actor, user_id=user_id, request_key='reversal:' + row['id'], reason=reason,
                receipt_id=receipt_id, receipt_line_id=row['receipt_line_id'], bank_sku=row['bank_sku'],
                bank_quantity=row['bank_quantity'], reverses_id=row['id'],
                snapshot=json.loads(snapshot) if isinstance(snapshot, str) else snapshot)


def summary(db, *, sku='', page=1):
    require_schema(db)
    if sku and not is_external_material(sku):
        raise ValueError('SKU externo inválido.')
    page = max(1, min(int(page), 100000))
    items = [dict(row) for row in db.execute(text('''
        select i.sku,i.location,i.activated_at,s.descricao,s.unidade,
               coalesce(sum(m.delta),0) as balance,
               max(case when m.kind='INITIAL' then 1 else 0 end) as initialized
          from erp_external_stock_items i left join skus s on s.sku=i.sku
          left join erp_external_stock_movements m on m.sku=i.sku
         group by i.sku,i.location,i.activated_at,s.descricao,s.unidade order by i.sku
    ''')).mappings()]
    where = ' where m.sku=:sku' if sku else ''
    total = db.execute(text('select count(*) from erp_external_stock_movements m' + where), {'sku': sku}).scalar_one()
    movements = [dict(row) for row in db.execute(text('''
        select m.*,r.numero_nf,o.numero_oc from erp_external_stock_movements m
          left join erp_goods_receipts r on cast(r.id as text)=m.receipt_id
          left join erp_purchase_orders o on o.id=r.purchase_order_id
    ''' + where + ' order by m.created_at desc,m.id desc limit 100 offset :offset'),
        {'sku': sku, 'offset': (page-1)*100}).mappings()]
    for row in items:
        row['balance'] = str(number(row['balance']))
        row['initialized'] = bool(row['initialized'])
    for row in movements:
        row['delta'] = str(number(row['delta']))
        row['bank_quantity'] = str(number(row['bank_quantity'])) if row['bank_quantity'] is not None else None
    return {'items': items, 'movements': movements, 'total': total, 'page': page}
