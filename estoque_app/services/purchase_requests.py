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

def closest_active_order(db, sku_code, needed_at):
    """Choose the active PO line for the same SKU whose due date is nearest the need date."""
    rows = db.execute(select(
        orders.c.id, orders.c.numero_oc, orders.c.status, orders.c.fornecedor_nome,
        func.coalesce(lines.c.data_necessidade, orders.c.data_necessidade).label("delivery_date"),
        (lines.c.quantidade_pedida - lines.c.quantidade_recebida).label("pending_quantity"),
    ).select_from(lines.join(orders, order_condition(db, lines.c.purchase_order_id, orders.c.id)))
      .where(lines.c.sku_codigo == sku_code,
             orders.c.status.in_(["EMITIDA", "PARCIALMENTE_RECEBIDA"]),
             lines.c.status.notin_(["CANCELADA", "RECEBIDA"]),
             lines.c.quantidade_pedida > lines.c.quantidade_recebida)).mappings().all()
    if not rows:
        return None
    def key(row):
        raw = row["delivery_date"]
        if isinstance(raw, datetime): raw = raw.date()
        elif isinstance(raw, str):
            try: raw = date.fromisoformat(raw[:10])
            except ValueError: raw = None
        distance = abs((raw - needed_at).days) if raw else 10**9
        return (distance, raw or date.max, str(row["numero_oc"] or ""))
    return dict(min(rows, key=key))

def _anticipation_state(db, row):
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
    pending = db.execute(select(func.coalesce(func.sum(case(
        (lines.c.quantidade_pedida > lines.c.quantidade_recebida,
         lines.c.quantidade_pedida - lines.c.quantidade_recebida), else_=0)
    ), 0)).where(
        order_condition(db, lines.c.purchase_order_id, order_id),
        lines.c.sku_codigo == row["sku_codigo"],
        lines.c.status.notin_(["CANCELADA", "RECEBIDA"]),
    )).scalar_one()
    pending = Decimal(str(pending or 0))
    if pending <= 0:
        return dict(order), pending, (
            f"A O.C. {order['numero_oc']} não possui mais saldo pendente do SKU {row['sku_codigo']}."
        )
    return dict(order), pending, ""

def _is_pending_request(row):
    return row.get("status") in {"SOLICITADA", "EM_COMPRAS"}

def _reclassify_stale_anticipation(db, row, user, reason_prefix=""):
    """Persist an automatic change once the linked order can no longer be anticipated."""
    if row.get("request_type") != "ANTECIPACAO" or not _is_pending_request(row):
        return row
    order, pending, reason = _anticipation_state(db, row)
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
    """Backfill old pending rows when the buyer's request queue is refreshed."""
    if not ready(db) or not user:
        return 0
    rows = db.execute(select(requests).where(
        requests.c.request_type == "ANTECIPACAO",
        requests.c.status.in_(["SOLICITADA", "EM_COMPRAS"]),
    ).order_by(requests.c.id).with_for_update()).mappings().all()
    changed = 0
    for item in rows:
        row = _reclassify_stale_anticipation(
            db, dict(item), user, "Reconciliação automática ao atualizar a fila de solicitações."
        )
        if row["request_type"] == "COMPRA_NOVA":
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
    pending_by_order_sku = select(
        lines.c.purchase_order_id.label("purchase_order_id"),
        lines.c.sku_codigo.label("sku_codigo"),
        func.sum(case((lines.c.quantidade_pedida > lines.c.quantidade_recebida,
                       lines.c.quantidade_pedida - lines.c.quantidade_recebida), else_=0)).label("pending_quantity"),
        func.min(func.coalesce(lines.c.data_necessidade, orders.c.data_necessidade)).label("delivery_date"),
    ).select_from(lines.join(orders, order_condition(db, lines.c.purchase_order_id, orders.c.id)))\
     .where(lines.c.status.notin_(["CANCELADA", "RECEBIDA"]))\
     .group_by(lines.c.purchase_order_id, lines.c.sku_codigo).subquery("anticipation_order_lines")
    statement = select(requests, orders.c.numero_oc, orders.c.fornecedor_nome,
                       orders.c.status.label("purchase_status"),
                       anticipated_order.c.numero_oc.label("anticipation_numero_oc"),
                       anticipated_order.c.fornecedor_nome.label("anticipation_fornecedor_nome"),
                       anticipated_order.c.status.label("anticipation_purchase_status"),
                       pending_by_order_sku.c.pending_quantity.label("anticipation_pending_quantity"),
                       pending_by_order_sku.c.delivery_date.label("anticipation_delivery_date"),
                       requests.c.anticipation_confirmed_delivery_date).select_from(
        requests.outerjoin(orders, join_condition).outerjoin(anticipated_order, anticipation_join)
        .outerjoin(pending_by_order_sku,
                   order_condition(db, pending_by_order_sku.c.purchase_order_id, anticipated_order.c.id) &
                   (pending_by_order_sku.c.sku_codigo == requests.c.sku_codigo)))
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
    return {"items": [encode(_present_current_request(dict(r))) for r in rows], "total": count, "page": page,
            "counts": dict(counts), "overdue": overdue}

def history(db, request_id):
    require_ready(db)
    row = get(db, request_id)
    rows = db.execute(select(events).where(events.c.request_id == row["id"])
                      .order_by(events.c.created_at, events.c.id)).mappings()
    return {"request": encode(row), "events": [encode(dict(r)) for r in rows]}

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
    require_buyer(db, user)
    if not isinstance(payload, dict):
        raise ValueError("Dados do tratamento inválidos.")
    row = get(db, request_id, lock=True)
    try: version = int(payload.get("version"))
    except (ValueError, TypeError): raise ValueError("Atualize a tabela antes de tratar a solicitação.")
    if row["version"] != version:
        raise ValueError("A solicitação foi alterada por outro usuário. Atualize a tabela.")
    row = _reclassify_stale_anticipation(db, row, user)
    action = payload.get("action")
    reason = str(payload.get("reason") or "").strip()
    if not reason or len(reason) > 4000:
        raise ValueError("Informe o motivo/observação (até 4.000 caracteres).")
    if action == "ASSUMIR" and row["status"] == "SOLICITADA":
        status = "EM_COMPRAS"
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
        pending_statement = select(func.sum(case(
            (lines.c.quantidade_pedida > lines.c.quantidade_recebida,
             lines.c.quantidade_pedida - lines.c.quantidade_recebida), else_=0)
        )).where(
            order_condition(db, lines.c.purchase_order_id, row["anticipation_order_id"]),
            lines.c.sku_codigo == row["sku_codigo"],
            lines.c.status.notin_(["CANCELADA", "RECEBIDA"]))
        pending_quantity = db.execute(pending_statement).scalar() or Decimal(0)
        if Decimal(str(pending_quantity)) < Decimal(str(row["quantity"])):
            raise ValueError("A O.C. vigente não tem saldo pendente suficiente deste SKU para atender a solicitação.")
        status = "CONCLUIDA"
    elif action == "CONVERTER_NOVA_COMPRA" and row["status"] in {"SOLICITADA", "EM_COMPRAS"} and row["request_type"] == "ANTECIPACAO":
        status = row["status"]
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
    elif action == "CONFIRMAR_ANTECIPACAO":
        row.update(status="CONCLUIDA", purchase_order_id=row["anticipation_order_id"],
                   anticipation_confirmed_delivery_date=confirmed_date,
                   completed_at=now(), completed_by=user.username,
                   buyer_id=user.id, buyer=user.username)
    elif action == "CONVERTER_NOVA_COMPRA":
        row.update(request_type="COMPRA_NOVA", anticipation_order_id=None,
                   buyer_id=user.id, buyer=user.username)
    elif action == "REABRIR":
        row.update(buyer_id=None, buyer=None)
    db.execute(requests.update().where(requests.c.id == row["id"]).values(**row))
    audit(db, dict(row, purchase_order=encode(dict(order))) if action == "CONFIRMAR_ANTECIPACAO" else row,
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
    rows = [_reclassify_stale_anticipation(db, row, user) for row in rows]
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

