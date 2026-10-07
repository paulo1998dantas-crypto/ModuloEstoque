"""Canonical requests. No stock movement; completion is part of the O.C. transaction."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import (MetaData, Table, Column, String, Integer, Numeric, Date,
                        DateTime, JSON, Uuid, select, func, or_, inspect,
                        CheckConstraint, UniqueConstraint, case)
from models import SKU, User
from auth import effective_roles, can

metadata = MetaData()
_UNSET = object()
requests = Table("erp_purchase_requests", metadata,
    Column("id", String(36), primary_key=True),
    Column("origin", String(20), nullable=False),
    Column("sku_id", Integer, nullable=False),
    Column("sku_codigo", String(80), nullable=False),
    Column("descricao", String(255), nullable=False),
    Column("unidade", String(20), nullable=False),
    Column("quantity", Numeric(14, 3), nullable=False),
    Column("needed_at", Date, nullable=False),
    Column("request_type", String(20), nullable=False, server_default="COMPRA_NOVA"),
    Column("reference", String(255), nullable=False),
    Column("notes", String(4000), nullable=False),
    Column("status", String(20), nullable=False),
    Column("requested_by_id", Integer, nullable=False),
    Column("requested_by", String(80), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("buyer_id", Integer), Column("buyer", String(80)),
    Column("purchase_order_id", Uuid(as_uuid=False)),
    Column("anticipation_order_id", Uuid(as_uuid=False)),
    Column("anticipation_confirmed_delivery_date", Date),
    Column("completed_at", DateTime(timezone=True)),
    Column("completed_by", String(80)),
    Column("version", Integer, nullable=False),
    Column("idempotency_key", String(80), nullable=False),
    UniqueConstraint("requested_by_id", "idempotency_key"),
    CheckConstraint("quantity > 0"),
    CheckConstraint("origin in ('ESTOQUE', 'PCP')"),
    CheckConstraint("request_type in ('COMPRA_NOVA', 'ANTECIPACAO')"),
    CheckConstraint("status in ('SOLICITADA', 'EM_COMPRAS', 'CONCLUIDA', 'CANCELADA')"),
    CheckConstraint("(status = 'CONCLUIDA') = (purchase_order_id is not null and completed_at is not null and completed_by is not null)"),
)
events = Table("erp_purchase_request_events", metadata,
    Column("id", String(36), primary_key=True),
    Column("request_id", String(36), nullable=False),
    Column("action", String(40), nullable=False),
    Column("actor_id", Integer, nullable=False),
    Column("actor", String(80), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("before_data", JSON), Column("after_data", JSON),
    Column("reason", String(4000), nullable=False),
)
# Minimal mappings of existing canonical tables; never created by this module.
orders = Table("erp_purchase_orders", MetaData(),
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("numero_oc", String), Column("status", String), Column("fornecedor_nome", String),
    Column("data_necessidade", Date))
lines = Table("erp_purchase_order_lines", MetaData(),
    Column("purchase_order_id", Uuid(as_uuid=False)),
    Column("sku_codigo", String), Column("quantidade_pedida", Numeric(14, 3)),
    Column("quantidade_recebida", Numeric(14, 3)), Column("status", String),
    Column("data_necessidade", Date))

def now():
    return datetime.now(timezone.utc)

def ready(db):
    return inspect(db.connection()).has_table(requests.name)

def require_ready(db):
    if not ready(db):
        raise ValueError("Workflow indisponível: aplique a migração 20261006120000_purchase_requests.sql no Estoque.")

def encode(value):
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (datetime, date)):
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value

def uid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Identificador de solicitação inválido.")

def buyer_allowed(db, user):
    return bool(user and user.active and effective_roles(user, db) & {"ADMIN", "COMPRADOR"}
                and can(user, "suprimentos.purchase.create", db))

def require_buyer(db, user):
    if not buyer_allowed(db, user):
        raise PermissionError("Somente o comprador (ou administrador) pode tratar solicitações.")

def require_request_editor(db, row, user):
    if buyer_allowed(db, user):
        if row["status"] not in {"SOLICITADA", "EM_COMPRAS"} or row.get("purchase_order_id"):
            raise ValueError("Só é possível editar/excluir solicitações ainda pendentes.")
        return
    if row["status"] != "SOLICITADA" or row.get("purchase_order_id"):
        raise ValueError("Usuários não compradores só podem editar/excluir antes de o comprador assumir a solicitação.")
    if not user or not user.active:
        raise PermissionError("Usuário ativo obrigatório para editar/excluir a solicitação.")
    require_origin(db, user, row["origin"])

def editable_request_fields(db, payload):
    sku_code = str(payload.get("sku_codigo") or "").strip()
    sku = db.query(SKU).filter(SKU.sku == sku_code, SKU.active.is_(True)).one_or_none()
    if not sku:
        raise ValueError("Selecione um SKU ativo do cadastro.")
    try:
        quantity = Decimal(str(payload.get("quantity") or "").replace(",", "."))
        if (not quantity.is_finite() or quantity <= 0 or
                quantity > Decimal("99999999999.999") or quantity.as_tuple().exponent < -3):
            raise InvalidOperation()
        needed = date.fromisoformat(str(payload.get("needed_at") or ""))
    except (ValueError, InvalidOperation):
        raise ValueError("Informe quantidade positiva (até três decimais) e data de necessidade válida.")
    reference = str(payload.get("reference") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    if len(reference) > 255 or len(notes) > 4000:
        raise ValueError("Referência: até 255 caracteres. Observações: até 4.000.")
    return {"sku_id": sku.id, "sku_codigo": sku.sku,
            "descricao": sku.descricao, "unidade": sku.unidade or "UN",
            "quantity": quantity, "needed_at": needed,
            "reference": reference, "notes": notes}

def require_origin(db, user, origin):
    roles = effective_roles(user, db) if user and user.active else set()
    required = {"ADMIN", "PCP"} if origin == "PCP" else {"ADMIN", "OPERADOR", "COMPRADOR", "PCP", "PRODUCAO"}
    permission = "suprimentos.work_order.manage" if origin == "PCP" else "estoque.stock.view"
    if origin not in {"ESTOQUE", "PCP"} or not roles & required or not can(user, permission, db):
        raise PermissionError("Seu perfil não pode solicitar nesta origem.")

def audit(db, row, user, action, before=None, reason=""):
    db.execute(events.insert().values(id=str(uuid4()), request_id=row["id"],
        action=action, actor_id=user.id, actor=user.username, created_at=now(),
        before_data=encode(before), after_data=encode(dict(row)), reason=reason))

def order_condition(db, column, order_id):
    # Legacy SQLite stores UUID strings with hyphens; PostgreSQL uses UUID.
    if db.bind.dialect.name == "sqlite":
        from sqlalchemy import cast
        if hasattr(order_id, "_compiler_dispatch"):
            return func.replace(cast(column, String), "-", "") == func.replace(cast(order_id, String), "-", "")
        return func.replace(cast(column, String), "-", "") == UUID(str(order_id)).hex
    return column == order_id

def get(db, request_id, lock=False):
    statement = select(requests).where(requests.c.id == uid(request_id))
    if lock:
        statement = statement.with_for_update()
    row = db.execute(statement).mappings().first()
    if not row:
        raise ValueError("Solicitação não encontrada.")
    return dict(row)

def create(db, payload, user, origin):
    require_ready(db)
    require_origin(db, user, origin)
    if not isinstance(payload, dict):
        raise ValueError("Dados da solicitação inválidos.")
    key = uid(payload.get("idempotency_key"))
    # Transaction advisory lock serializes retries, including different origins.
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy import text
        db.execute(text("select pg_advisory_xact_lock(hashtextextended(:key,0))"),
                   {"key": f"purchase-request:{user.id}:{key}"})
    found = db.execute(select(requests).where(
        requests.c.requested_by_id == user.id, requests.c.idempotency_key == key)).mappings().first()
    if found:
        return {"request": encode(dict(found)), "replayed": True}
    sku = db.query(SKU).filter(SKU.sku == str(payload.get("sku_codigo") or "").strip(),
                               SKU.active.is_(True)).one_or_none()
    if not sku:
        raise ValueError("Selecione um SKU ativo do cadastro.")
    try:
        quantity = Decimal(str(payload.get("quantity") or "").replace(",", "."))
        if not quantity.is_finite() or quantity <= 0 or quantity > Decimal("99999999999.999") or quantity.as_tuple().exponent < -3:
            raise InvalidOperation()
        needed = date.fromisoformat(str(payload.get("needed_at") or ""))
    except (ValueError, InvalidOperation):
        raise ValueError("Informe quantidade positiva (até três decimais) e data de necessidade válida.")
    reference = str(payload.get("reference") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    if len(reference) > 255 or len(notes) > 4000:
        raise ValueError("Referência: até 255 caracteres. Observações: até 4.000.")
    anticipation = closest_active_order(db, sku.sku, needed)
    row = dict(id=str(uuid4()), origin=origin, request_type=("ANTECIPACAO" if anticipation else "COMPRA_NOVA"),
        anticipation_order_id=(str(anticipation["id"]) if anticipation else None),
        anticipation_confirmed_delivery_date=None,
        sku_id=sku.id, sku_codigo=sku.sku,
        descricao=sku.descricao, unidade=sku.unidade or "UN", quantity=quantity,
        needed_at=needed, reference=reference, notes=notes, status="SOLICITADA",
        requested_by_id=user.id, requested_by=user.username, created_at=now(),
        updated_at=now(), buyer_id=None, buyer=None, purchase_order_id=None,
        completed_at=None, completed_by=None, version=1, idempotency_key=key)
    db.execute(requests.insert().values(**row))
    audit(db, row, user, "ANTECIPACAO_IDENTIFICADA" if anticipation else "SOLICITADA",
          reason=(f"Vinculada automaticamente ao pedido vigente {anticipation['numero_oc']} "
                  f"(saldo pendente {encode(anticipation['pending_quantity'])})." if anticipation else ""))
    return {"request": encode(row), "replayed": False,
            "anticipation_order": encode(anticipation) if anticipation else None}

def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None

def _bom_leaf_factors(db, sku, cache, ancestry=()):
    """Return leaf SKU quantities per one purchased SKU, using receipt semantics."""
    from services.estoque_service import bom_components_for_sku, is_bom_manufacturing_sku
    if sku.id in ancestry:
        raise ValueError(f"B.O.M. cíclica detectada para {sku.sku}.")
    if sku.id in cache:
        return cache[sku.id]
    components = bom_components_for_sku(db, sku) if is_bom_manufacturing_sku(sku) else []
    if not components:
        result = {sku.sku: Decimal("1")}
        cache[sku.id] = result
        return result
    result = {}
    next_ancestry = (*ancestry, sku.id)
    for component in components:
        child = component.component_sku
        if not child or not child.active:
            raise ValueError(f"B.O.M. possui componente inexistente ou inativo para {sku.sku}.")
        try:
            quantity = Decimal(str(component.quantidade))
        except (InvalidOperation, TypeError):
            raise ValueError(f"B.O.M. possui quantidade inválida para {child.sku}.")
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError(f"B.O.M. possui quantidade inválida para {child.sku}.")
        for leaf_code, factor in _bom_leaf_factors(db, child, cache, next_ancestry).items():
            result[leaf_code] = result.get(leaf_code, Decimal("0")) + quantity * factor
    cache[sku.id] = result
    return result

def _active_order_lines(db, order_id=None, exclude_order_id=None):
    statement = select(
        lines.c.purchase_order_id, lines.c.sku_codigo,
        lines.c.quantidade_pedida, lines.c.quantidade_recebida,
        func.coalesce(lines.c.data_necessidade, orders.c.data_necessidade).label("delivery_date"),
        orders.c.numero_oc, orders.c.status, orders.c.fornecedor_nome,
    ).select_from(lines.join(orders, order_condition(db, lines.c.purchase_order_id, orders.c.id)))\
      .where(orders.c.status.in_(["EMITIDA", "PARCIALMENTE_RECEBIDA"]),
             lines.c.status.notin_(["CANCELADA", "RECEBIDA"]),
             lines.c.quantidade_pedida > lines.c.quantidade_recebida)
    if order_id:
        statement = statement.where(order_condition(db, lines.c.purchase_order_id, order_id))
    if exclude_order_id:
        statement = statement.where(~order_condition(db, lines.c.purchase_order_id, exclude_order_id))
    return db.execute(statement).mappings().all()

def _order_component_balances(db, sku_code, needed_at=None, order_id=None,
                              rows=None, factor_cache=None, sku_cache=None):
    """Aggregate open PO coverage for a leaf SKU, including recursively exploded kits."""
    rows = _active_order_lines(db, order_id) if rows is None else rows
    balances = {}
    factor_cache = factor_cache if factor_cache is not None else {}
    sku_cache = sku_cache if sku_cache is not None else {}
    for line in rows:
        if order_id and str(line["purchase_order_id"]) != str(order_id):
            continue
        parent_code = str(line["sku_codigo"] or "").strip()
        if not parent_code:
            continue
        parent = sku_cache.get(parent_code)
        if parent is None:
            parent = db.query(SKU).filter(SKU.sku == parent_code).one_or_none()
            sku_cache[parent_code] = parent
        if not parent:
            if parent_code == sku_code:
                factor = Decimal("1")
            else:
                continue
        else:
            try:
                # A purchased PP/CJ with a BOM is not itself a stockable receipt:
                # only its recursively exploded leaf components cover the request.
                factor = _bom_leaf_factors(db, parent, factor_cache).get(sku_code, Decimal("0"))
            except ValueError:
                # An invalid/cyclic BOM cannot safely promise component coverage.
                continue
        if factor <= 0:
            continue
        remaining_parent = Decimal(str(line["quantidade_pedida"] or 0)) - Decimal(str(line["quantidade_recebida"] or 0))
        pending = remaining_parent * factor
        if pending <= 0:
            continue
        key = str(line["purchase_order_id"])
        balance = balances.setdefault(key, {
            "id": key, "numero_oc": line["numero_oc"], "status": line["status"],
            "fornecedor_nome": line["fornecedor_nome"],
            "pending_quantity": Decimal("0"), "delivery_date": None,
        })
        balance["pending_quantity"] += pending
        due = _as_date(line["delivery_date"])
        current = balance["delivery_date"]
        if due and (current is None or (needed_at is None and due < current) or
                    (needed_at is not None and abs((due-needed_at).days) < abs((current-needed_at).days))):
            balance["delivery_date"] = due
    return balances

def order_component_pending(db, order_id, sku_code, rows=None,
                            factor_cache=None, sku_cache=None):
    balance = _order_component_balances(
        db, sku_code, order_id=order_id, rows=rows,
        factor_cache=factor_cache, sku_cache=sku_cache,
    ).get(str(order_id))
    return balance or {"pending_quantity": Decimal("0"), "delivery_date": None}

def _order_component_unallocated(db, order_id, sku_code, exclude_request_id=None):
    pending = order_component_pending(db, order_id, sku_code)["pending_quantity"]
    conditions = [order_condition(db, requests.c.purchase_order_id, order_id),
                  requests.c.status == "CONCLUIDA", requests.c.sku_codigo == sku_code]
    if exclude_request_id:
        conditions.append(requests.c.id != exclude_request_id)
    reserved = db.execute(select(func.coalesce(func.sum(requests.c.quantity), 0)).where(*conditions)).scalar_one()
    return max(Decimal("0"), pending - Decimal(str(reserved or 0)))

def closest_active_order(db, sku_code, needed_at, rows=None,
                         factor_cache=None, sku_cache=None):
    """Choose the nearest active order covering the item directly or via a kit B.O.M."""
    balances = list(_order_component_balances(
        db, sku_code, needed_at, rows=rows,
        factor_cache=factor_cache, sku_cache=sku_cache,
    ).values())
    if not balances:
        return None
    def key(row):
        due = row["delivery_date"]
        distance = abs((due - needed_at).days) if due else 10**9
        return (distance, due or date.max, str(row["numero_oc"] or ""))
    return min(balances, key=key)

def _anticipation_state(db, row, coverage_lines=None, factor_cache=None, sku_cache=None):
    """Return the linked order and whether it can still receive an anticipation."""
    order_id = row.get("anticipation_order_id")
    if not order_id:
        return None, Decimal(0), "O pedido vinculado não existe mais."
    order = db.execute(select(orders.c.id, orders.c.numero_oc, orders.c.status,
                              orders.c.fornecedor_nome).where(
        order_condition(db, orders.c.id, order_id)
    )).mappings().first()
    if not order:
        return None, Decimal(0), "O pedido vinculado não foi encontrado."
    if order["status"] not in {"EMITIDA", "PARCIALMENTE_RECEBIDA"}:
        return dict(order), Decimal(0), (
            f"O.C. {order['numero_oc']} deixou de estar vigente (status {order['status']})."
        )
    pending = order_component_pending(
        db, order_id, row["sku_codigo"], rows=coverage_lines,
        factor_cache=factor_cache, sku_cache=sku_cache,
    )["pending_quantity"]
    if pending <= 0:
        return dict(order), pending, (
            f"A O.C. {order['numero_oc']} não possui mais saldo pendente do SKU {row['sku_codigo']}."
        )
    return dict(order), pending, ""

def _is_pending_request(row):
    return row.get("status") in {"SOLICITADA", "EM_COMPRAS"}

def _reclassify_stale_anticipation(db, row, user, reason_prefix="", coverage_lines=None,
                                   factor_cache=None, sku_cache=None,
                                   latest_decision=_UNSET):
    """Keep pending request classification synchronized with open O.C.s and kit B.O.M.s."""
    if not _is_pending_request(row):
        return row
    if row.get("request_type") == "ANTECIPACAO":
        order, pending, reason = _anticipation_state(
            db, row, coverage_lines, factor_cache, sku_cache
        )
        if not reason:
            return row
        before = dict(row)
        if order:
            before["anticipation_order"] = encode(order)
        row.update(request_type="COMPRA_NOVA", anticipation_order_id=None,
                   anticipation_confirmed_delivery_date=None, updated_at=now(),
                   version=row["version"] + 1)
        db.execute(requests.update().where(requests.c.id == row["id"]).values(**row))
        audit(db, row, user, "ANTECIPACAO_RECLASSIFICADA", before,
              f"{reason_prefix} {reason}".strip())
        return row
    if row.get("request_type") != "COMPRA_NOVA" or row.get("purchase_order_id"):
        return row
    if latest_decision is _UNSET:
        latest_decision = db.execute(select(events.c.action).where(
            events.c.request_id == row["id"],
            events.c.action.in_({"CONVERTER_NOVA_COMPRA", "EDITAR", "ANTECIPACAO_IDENTIFICADA", "ANTECIPACAO_RECLASSIFICADA"}),
        ).order_by(events.c.created_at.desc(), events.c.id.desc()).limit(1)).scalar_one_or_none()
    if latest_decision == "CONVERTER_NOVA_COMPRA":
        return row
    order = closest_active_order(
        db, row["sku_codigo"], row["needed_at"], rows=coverage_lines,
        factor_cache=factor_cache, sku_cache=sku_cache,
    )
    if not order:
        return row
    before = dict(row)
    row.update(request_type="ANTECIPACAO", anticipation_order_id=order["id"],
               anticipation_confirmed_delivery_date=None, updated_at=now(),
               version=row["version"] + 1)
    db.execute(requests.update().where(requests.c.id == row["id"]).values(**row))
    audit(db, row, user, "ANTECIPACAO_RECLASSIFICADA", before,
          f"{reason_prefix} Solicitação vinculada à O.C. {order['numero_oc']}: o item está coberto diretamente ou pela explosão da B.O.M. do conjunto; saldo equivalente {encode(order['pending_quantity'])}.".strip())
    return row

def _present_current_request(row):
    """Show stale pending anticipations as new purchases, including legacy rows."""
    row = dict(row)
    if (row.get("request_type") == "ANTECIPACAO" and _is_pending_request(row) and
            (row.get("anticipation_purchase_status") not in {"EMITIDA", "PARCIALMENTE_RECEBIDA"} or
             Decimal(str(row.get("anticipation_pending_quantity") or 0)) <= 0)):
        row.update(request_type="COMPRA_NOVA", anticipation_order_id=None,
                   anticipation_numero_oc=None, anticipation_fornecedor_nome=None,
                   anticipation_purchase_status=None, anticipation_pending_quantity=None,
                   anticipation_delivery_date=None,
                   anticipation_confirmed_delivery_date=None)
    return row

def reclassify_closed_order_anticipations(db, order_id, user, reason=""):
    """Reclassify pending requests in the same transaction that closes/receives an O.C."""
    if not ready(db) or not user:
        return 0
    rows = db.execute(select(requests).where(
        order_condition(db, requests.c.anticipation_order_id, order_id),
        requests.c.request_type == "ANTECIPACAO",
        requests.c.status.in_(["SOLICITADA", "EM_COMPRAS"]),
    ).order_by(requests.c.id).with_for_update()).mappings().all()
    changed = 0
    for item in rows:
        row = _reclassify_stale_anticipation(db, dict(item), user, reason)
        if row["request_type"] == "COMPRA_NOVA":
            changed += 1
    return changed

def synchronize_stale_anticipations(db, user):
    """Reconcile pending requests with current open orders and recursively exploded kits."""
    if not ready(db) or not user:
        return 0
    rows = db.execute(select(requests).where(
        requests.c.status.in_(["SOLICITADA", "EM_COMPRAS"]),
    ).order_by(requests.c.id).with_for_update()).mappings().all()
    changed = 0
    open_lines = _active_order_lines(db)
    factor_cache, sku_cache = {}, {}
    request_ids = [row["id"] for row in rows]
    decisions = db.execute(select(events.c.request_id, events.c.action).where(
        events.c.request_id.in_(request_ids),
        events.c.action.in_({"CONVERTER_NOVA_COMPRA", "EDITAR", "ANTECIPACAO_IDENTIFICADA", "ANTECIPACAO_RECLASSIFICADA"}),
    ).order_by(events.c.created_at.desc(), events.c.id.desc())).all() if request_ids else []
    latest_decision = {}
    for request_id, action in decisions:
        latest_decision.setdefault(request_id, action)
    for item in rows:
        row = dict(item)
        original_type = row["request_type"]
        row = _reclassify_stale_anticipation(
            db, row, user, "Reconciliação automática ao atualizar a fila de solicitações.",
            coverage_lines=open_lines, factor_cache=factor_cache,
            sku_cache=sku_cache, latest_decision=latest_decision.get(row["id"], ""),
        )
        if row["request_type"] != original_type:
            changed += 1
    return changed

def listing(db, filters, user=None):
    require_ready(db)
    synchronize_stale_anticipations(db, user)
    from sqlalchemy import cast
    join_condition = requests.c.purchase_order_id == orders.c.id
    if db.bind.dialect.name == "sqlite":
        join_condition = func.replace(cast(requests.c.purchase_order_id, String), "-", "") == func.replace(cast(orders.c.id, String), "-", "")
    anticipated_order = orders.alias("anticipated_order")
    anticipation_join = order_condition(db, requests.c.anticipation_order_id, anticipated_order.c.id)
    statement = select(requests, orders.c.numero_oc, orders.c.fornecedor_nome,
                       orders.c.status.label("purchase_status"),
                       anticipated_order.c.numero_oc.label("anticipation_numero_oc"),
                       anticipated_order.c.fornecedor_nome.label("anticipation_fornecedor_nome"),
                       anticipated_order.c.status.label("anticipation_purchase_status"),
                       requests.c.anticipation_confirmed_delivery_date).select_from(
        requests.outerjoin(orders, join_condition).outerjoin(anticipated_order, anticipation_join))
    for field in ("status", "origin"):
        if filters.get(field):
            statement = statement.where(requests.c[field] == filters[field])
    if filters.get("q"):
        term = "%" + str(filters["q"]).strip()[:150] + "%"
        statement = statement.where(or_(*[c.ilike(term) for c in
            (requests.c.sku_codigo, requests.c.descricao, requests.c.requested_by,
             requests.c.reference, requests.c.buyer, orders.c.numero_oc,
             anticipated_order.c.numero_oc)]))
    for key, comparison in (("from", True), ("to", False)):
        if filters.get(key):
            try: value = date.fromisoformat(filters[key])
            except ValueError: raise ValueError("Filtro de data inválido.")
            statement = statement.where(requests.c.needed_at >= value if comparison else requests.c.needed_at <= value)
    try: page = max(1, int(filters.get("page", 1)))
    except (ValueError, TypeError): raise ValueError("Página inválida.")
    count = db.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
    rows = db.execute(statement.order_by(requests.c.created_at.desc(), requests.c.id)
                      .offset((page-1)*100).limit(100)).mappings().all()
    pending = requests.c.status.in_(["SOLICITADA", "EM_COMPRAS"])
    counts = db.execute(select(requests.c.status, func.count()).group_by(requests.c.status)).all()
    overdue = db.execute(select(func.count()).select_from(requests).where(
        pending, requests.c.needed_at < datetime.now(ZoneInfo("America/Sao_Paulo")).date())).scalar_one()
    has_anticipations = any(r["anticipation_order_id"] for r in rows)
    coverage_lines = _active_order_lines(db) if has_anticipations else []
    factor_cache, sku_cache = {}, {}
    items = []
    for raw in rows:
        item = dict(raw)
        if item.get("anticipation_order_id"):
            component_balance = order_component_pending(
                db, item["anticipation_order_id"], item["sku_codigo"],
                rows=coverage_lines, factor_cache=factor_cache, sku_cache=sku_cache,
            )
            item["anticipation_pending_quantity"] = component_balance["pending_quantity"]
            item["anticipation_delivery_date"] = component_balance["delivery_date"]
        else:
            item["anticipation_pending_quantity"] = None
            item["anticipation_delivery_date"] = None
        items.append(encode(_present_current_request(item)))
    return {"items": items, "total": count, "page": page,
            "counts": dict(counts), "overdue": overdue}

def history(db, request_id):
    require_ready(db)
    row = get(db, request_id)
    rows = db.execute(select(events).where(events.c.request_id == row["id"])
                      .order_by(events.c.created_at, events.c.id)).mappings()
    return {"request": encode(row), "events": [encode(dict(r)) for r in rows]}

def existing_order_options(db, request_id, user):
    """List active O.C.s that can cover the request, including component coverage via B.O.M."""
    require_ready(db)
    require_buyer(db, user)
    row = get(db, request_id)
    if not _is_pending_request(row) or row.get("purchase_order_id"):
        raise ValueError("Só é possível alocar O.C. em solicitação ainda pendente.")
    balances = _order_component_balances(db, row["sku_codigo"], row["needed_at"])
    options = []
    for order_id, balance in balances.items():
        available = _order_component_unallocated(db, order_id, row["sku_codigo"])
        if available < Decimal(str(row["quantity"])):
            continue
        options.append({**balance, "available_quantity": available})
    options.sort(key=lambda item: (
        abs((item["delivery_date"] - row["needed_at"]).days) if item["delivery_date"] else 10**9,
        item["delivery_date"] or date.max,
        str(item["numero_oc"] or ""),
    ))
    return {"items": [encode(item) for item in options]}

def notifications(db):
    require_ready(db)
    counts = dict(db.execute(select(requests.c.status, func.count()).group_by(requests.c.status)).all())
    return {"new": counts.get("SOLICITADA", 0), "in_progress": counts.get("EM_COMPRAS", 0)}

def prepare(db, ids, user):
    require_ready(db)
    require_buyer(db, user)
    if not isinstance(ids, list) or not ids or len(ids) > 100:
        raise ValueError("Selecione entre uma e 100 solicitações.")
    rows = [get(db, i) for i in dict.fromkeys(uid(i) for i in ids)]
    rows = [_reclassify_stale_anticipation(db, row, user) for row in rows]
    if any(r["status"] not in {"SOLICITADA", "EM_COMPRAS"} for r in rows):
        raise ValueError("Há solicitações concluídas/canceladas. Atualize a tabela.")
    if any(r["request_type"] == "ANTECIPACAO" for r in rows):
        raise ValueError("Solicitações de antecipação devem ser tratadas no pedido vigente; para emitir nova O.C., converta a solicitação com justificativa primeiro.")
    return {"items": [encode(r) for r in rows]}

def transition(db, request_id, payload, user):
    require_ready(db)
    if not isinstance(payload, dict):
        raise ValueError("Dados do tratamento inválidos.")
    row = get(db, request_id, lock=True)
    action = payload.get("action")
    if action in {"EDITAR", "EXCLUIR"}:
        require_request_editor(db, row, user)
    else:
        require_buyer(db, user)
    try: version = int(payload.get("version"))
    except (ValueError, TypeError): raise ValueError("Atualize a tabela antes de tratar a solicitação.")
    if row["version"] != version:
        raise ValueError("A solicitação foi alterada por outro usuário. Atualize a tabela.")
    row = _reclassify_stale_anticipation(db, row, user)
    reason = str(payload.get("reason") or "").strip()
    if not reason or len(reason) > 4000:
        raise ValueError("Informe o motivo/observação (até 4.000 caracteres).")
    edit_fields = editable_request_fields(db, payload) if action == "EDITAR" else None
    anticipated = None
    if edit_fields:
        changed = (
            row["sku_id"] != edit_fields["sku_id"] or
            Decimal(str(row["quantity"])) != edit_fields["quantity"] or
            row["needed_at"] != edit_fields["needed_at"] or
            (row["reference"] or "") != edit_fields["reference"] or
            (row["notes"] or "") != edit_fields["notes"]
        )
        if not changed:
            raise ValueError("Nenhum dado da solicitação foi alterado.")
        anticipated = closest_active_order(db, edit_fields["sku_codigo"], edit_fields["needed_at"])
    if action == "ASSUMIR" and row["status"] == "SOLICITADA":
        status = "EM_COMPRAS"
    elif action == "ALOCAR_PEDIDO" and row["status"] in {"SOLICITADA", "EM_COMPRAS"} and not row.get("purchase_order_id"):
        try:
            selected_order_id = uid(payload.get("purchase_order_id"))
        except ValueError:
            raise ValueError("Selecione um pedido de compra válido.")
        order = db.execute(select(orders).where(
            order_condition(db, orders.c.id, selected_order_id)
        ).with_for_update()).mappings().first()
        if not order or order["status"] not in {"EMITIDA", "PARCIALMENTE_RECEBIDA"} or not str(order["numero_oc"] or "").strip():
            raise ValueError("Selecione uma O.C. emitida e ainda vigente.")
        available = _order_component_unallocated(db, selected_order_id, row["sku_codigo"], row["id"])
        if available < Decimal(str(row["quantity"])):
            raise ValueError("A O.C. não tem saldo pendente suficiente deste SKU, considerando alocações já confirmadas.")
        status = "CONCLUIDA"
    elif action == "SOLICITAR_ANTECIPACAO" and row["status"] == "SOLICITADA" and row["request_type"] == "ANTECIPACAO":
        status = "EM_COMPRAS"
    elif action == "CONFIRMAR_ANTECIPACAO" and row["status"] == "EM_COMPRAS" and row["request_type"] == "ANTECIPACAO":
        try:
            confirmed_date = date.fromisoformat(str(payload.get("confirmed_delivery_date") or ""))
        except ValueError:
            raise ValueError("Informe a nova data de entrega negociada.")
        order = db.execute(select(orders).where(
            order_condition(db, orders.c.id, row["anticipation_order_id"])
        ).with_for_update()).mappings().first() if row["anticipation_order_id"] else None
        if not order or order["status"] not in {"EMITIDA", "PARCIALMENTE_RECEBIDA"} or not str(order["numero_oc"] or "").strip():
            raise ValueError("A antecipação exige uma O.C. válida e ainda vigente.")
        available_quantity = _order_component_unallocated(
            db, row["anticipation_order_id"], row["sku_codigo"], row["id"]
        )
        if available_quantity < Decimal(str(row["quantity"])):
            raise ValueError("A O.C. vigente não tem saldo pendente suficiente deste SKU para atender a solicitação.")
        status = "CONCLUIDA"
    elif action == "CONVERTER_NOVA_COMPRA" and row["status"] in {"SOLICITADA", "EM_COMPRAS"} and row["request_type"] == "ANTECIPACAO":
        status = row["status"]
    elif action == "EDITAR" and row["status"] in {"SOLICITADA", "EM_COMPRAS"} and not row.get("purchase_order_id"):
        status = row["status"]
    elif action == "EXCLUIR" and row["status"] in {"SOLICITADA", "EM_COMPRAS"} and not row.get("purchase_order_id"):
        status = "CANCELADA"
    elif action == "CANCELAR" and row["status"] in {"SOLICITADA", "EM_COMPRAS"}:
        status = "CANCELADA"
    elif action == "REABRIR" and row["status"] == "CANCELADA":
        status = "SOLICITADA"
    elif action == "OBSERVACAO":
        status = row["status"]
    else:
        raise ValueError("Transição inválida. A conclusão ocorre somente na emissão do pedido.")
    before = dict(row)
    row.update(status=status, updated_at=now(), version=row["version"]+1)
    if action in {"ASSUMIR", "SOLICITAR_ANTECIPACAO"}:
        row.update(buyer_id=user.id, buyer=user.username)
    elif action == "EDITAR":
        row.update(**edit_fields,
                   request_type="ANTECIPACAO" if anticipated else "COMPRA_NOVA",
                   anticipation_order_id=str(anticipated["id"]) if anticipated else None,
                   anticipation_confirmed_delivery_date=None)
    elif action == "CONFIRMAR_ANTECIPACAO":
        row.update(status="CONCLUIDA", purchase_order_id=row["anticipation_order_id"],
                   anticipation_confirmed_delivery_date=confirmed_date,
                   completed_at=now(), completed_by=user.username,
                   buyer_id=user.id, buyer=user.username)
    elif action == "ALOCAR_PEDIDO":
        row.update(status="CONCLUIDA", request_type="ANTECIPACAO",
                   purchase_order_id=selected_order_id, anticipation_order_id=selected_order_id,
                   anticipation_confirmed_delivery_date=None,
                   completed_at=now(), completed_by=user.username,
                   buyer_id=user.id, buyer=user.username)
    elif action == "CONVERTER_NOVA_COMPRA":
        row.update(request_type="COMPRA_NOVA", anticipation_order_id=None,
                   buyer_id=user.id, buyer=user.username)
    elif action == "REABRIR":
        row.update(buyer_id=None, buyer=None)
    db.execute(requests.update().where(requests.c.id == row["id"]).values(**row))
    audit(db, dict(row, purchase_order=encode(dict(order))) if action in {"CONFIRMAR_ANTECIPACAO", "ALOCAR_PEDIDO"} else row,
          user, action, before, reason)
    return {"request": encode(row)}

def actor_user(db, actor):
    user = db.query(User).filter(User.username == actor, User.active.is_(True)).one_or_none()
    require_buyer(db, user)
    return user

def confirm_order(db, order_id, request_ids, actor, updated=False):
    """Called BEFORE the canonical order commit; edits cannot remove requested materials."""
    if not isinstance(request_ids, list) or len(request_ids) > 100:
        raise ValueError("Lista de solicitações inválida.")
    if not ready(db):
        if request_ids:
            require_ready(db)
        return
    # Serialize order edits/cancellations with newly attached requests.
    order = db.execute(select(orders).where(order_condition(db, orders.c.id, order_id))
                       .with_for_update()).mappings().first()
    existing = db.execute(select(requests.c.id).where(requests.c.purchase_order_id == order_id)).scalars().all()
    ids = sorted(set(uid(i) for i in (request_ids or [])) | set(existing))
    if not ids:
        return
    user = actor_user(db, actor)
    if not order or order["status"] in {"CANCELADA", "RASCUNHO"}:
        raise ValueError("A solicitação exige pedido confirmado.")
    rows = [get(db, i, lock=True) for i in ids]
    coverage_lines = _active_order_lines(db, exclude_order_id=order_id)
    factor_cache, sku_cache = {}, {}
    rows = [_reclassify_stale_anticipation(
        db, row, user, coverage_lines=coverage_lines,
        factor_cache=factor_cache, sku_cache=sku_cache,
    ) for row in rows]
    purchased = dict(db.execute(select(lines.c.sku_codigo, func.sum(lines.c.quantidade_pedida))
         .where(order_condition(db, lines.c.purchase_order_id, order_id)).group_by(lines.c.sku_codigo)).all())
    required = {}
    for row in rows:
        if row["purchase_order_id"] and row["purchase_order_id"] != str(order_id):
            raise ValueError("Solicitação já concluída em outro pedido.")
        if row["status"] == "CANCELADA":
            raise ValueError("Solicitação cancelada não pode ser convertida.")
        if row["request_type"] == "ANTECIPACAO":
            raise ValueError("Solicitação vinculada a antecipação precisa ser convertida em nova compra com justificativa antes de entrar em outra O.C.")
        required[row["sku_codigo"]] = required.get(row["sku_codigo"], Decimal(0)) + row["quantity"]
    if any(Decimal(str(purchased.get(sku, 0))) < qty for sku, qty in required.items()):
        raise ValueError("O pedido não contempla o SKU e a quantidade total das solicitações vinculadas.")
    for row in rows:
        before = dict(row)
        replay = row["purchase_order_id"] == str(order_id)
        if replay:
            if updated:
                snapshot = dict(row, purchase_order=encode(dict(order)), purchased_quantities=encode(purchased))
                audit(db, snapshot, user, "PEDIDO_ATUALIZADO", before,
                      "Pedido alterado; vínculo e quantidades das solicitações preservados.")
            continue
        row.update(status="CONCLUIDA", purchase_order_id=str(order_id), completed_at=now(),
                   completed_by=user.username, buyer_id=user.id, buyer=user.username,
                   updated_at=now(), version=row["version"]+1)
        db.execute(requests.update().where(requests.c.id == row["id"]).values(**row))
        audit(db, dict(row, purchase_order=encode(dict(order)), purchased_quantities=encode(purchased)), user, "PEDIDO_CONFIRMADO", before,
              f"Pedido {order['numero_oc']} confirmado; conclusão de compras, não recebimento.")

def reopen_cancelled_order(db, order_id, actor, reason):
    if not ready(db): return
    rows = db.execute(select(requests).where(requests.c.purchase_order_id == order_id)
                      .order_by(requests.c.id).with_for_update()).mappings().all()
    if not rows: return
    user = actor_user(db, actor)
    for item in rows:
        row, before = dict(item), dict(item)
        row.update(status="SOLICITADA", purchase_order_id=None, completed_at=None,
                   completed_by=None, buyer_id=None, buyer=None, updated_at=now(),
                   version=row["version"]+1)
        if row["request_type"] == "ANTECIPACAO" and row["anticipation_order_id"] == str(order_id):
            row.update(request_type="COMPRA_NOVA", anticipation_order_id=None,
                       anticipation_confirmed_delivery_date=None)
        db.execute(requests.update().where(requests.c.id == row["id"]).values(**row))
        audit(db, row, user, "PEDIDO_CANCELADO_REABERTURA", before, reason)

