from decimal import Decimal, InvalidOperation

from sqlalchemy import func

from models import (
    AppSetting,
    DashboardMovementCache,
    InventoryCount,
    InventorySession,
    LabelPrintJob,
    Movement,
    SKU,
    StockBalance,
    now_utc,
)


QTY_SCALE = Decimal("0.001")


def to_decimal(value, default="0"):
    if value is None or value == "":
        value = default
    try:
        return Decimal(str(value).replace(",", ".")).quantize(QTY_SCALE)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Quantidade invalida.") from exc


def to_optional_decimal(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return to_decimal(value)


def decimal_to_str(value):
    value = to_decimal(value)
    text = f"{value:.3f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def optional_decimal_to_str(value):
    return "" if value is None else decimal_to_str(value)


def get_setting(db, key, default=""):
    setting = db.query(AppSetting).filter_by(key=key).one_or_none()
    return setting.value if setting else default


def get_setting_bool(db, key, default=False):
    value = get_setting(db, key, "true" if default else "false")
    return str(value).strip().lower() in {"1", "true", "sim", "yes", "on"}


def set_setting(db, key, value):
    setting = db.query(AppSetting).filter_by(key=key).one_or_none()
    if setting is None:
        setting = AppSetting(key=key, value=str(value))
        db.add(setting)
    else:
        setting.value = str(value)
    db.commit()
    return setting


def normalize_sku(code):
    return str(code or "").strip().upper()


def get_sku_by_code(db, code, active_only=False):
    sku_code = normalize_sku(code)
    if not sku_code:
        return None
    query = db.query(SKU).filter(func.upper(SKU.sku) == sku_code)
    if active_only:
        query = query.filter(SKU.active.is_(True))
    return query.one_or_none()


def ensure_balance(db, sku):
    if sku.balance:
        return sku.balance
    balance = StockBalance(sku_id=sku.id, saldo_atual=Decimal("0.000"))
    db.add(balance)
    db.flush()
    return balance


def create_or_update_sku(db, data, user=None):
    sku_code = normalize_sku(data.get("sku"))
    descricao = str(data.get("descricao") or "").strip()
    if not sku_code:
        raise ValueError("SKU e obrigatorio.")
    if not descricao:
        raise ValueError("Descricao e obrigatoria.")

    sku = get_sku_by_code(db, sku_code)
    created = False
    if sku is None:
        sku = SKU(sku=sku_code, descricao=descricao)
        db.add(sku)
        created = True

    sku.descricao = descricao
    sku.unidade = str(data.get("unidade") or "").strip() or None
    sku.categoria = str(data.get("categoria") or "").strip() or None
    sku.localizacao = str(data.get("localizacao") or "").strip() or None
    if "estoque_minimo" in data:
        sku.estoque_minimo = to_optional_decimal(data.get("estoque_minimo"))
    sku.active = bool(data.get("active", True))
    db.flush()
    balance = ensure_balance(db, sku)
    if "saldo_atual" in data:
        balance.saldo_atual = to_decimal(data.get("saldo_atual"))
    db.commit()
    return sku, created


def cache_dashboard_movement(db, movement):
    cache = DashboardMovementCache(
        movement_id=movement.id,
        created_at=movement.created_at,
        usuario_id=movement.usuario_id,
        usuario_nome=movement.usuario.username if movement.usuario else "",
        sku_id=movement.sku_id,
        sku_codigo=movement.sku.sku,
        descricao=movement.sku.descricao,
        tipo=movement.tipo,
        quantidade=movement.quantidade,
        saldo_anterior=movement.saldo_anterior,
        saldo_posterior=movement.saldo_posterior,
        documento=movement.documento,
        observacao=movement.observacao,
    )
    db.add(cache)
    db.flush()

    old_rows = (
        db.query(DashboardMovementCache)
        .order_by(DashboardMovementCache.created_at.desc(), DashboardMovementCache.id.desc())
        .offset(10)
        .all()
    )
    for row in old_rows:
        db.delete(row)


def dashboard_movement_cache(db):
    rows = (
        db.query(DashboardMovementCache)
        .order_by(DashboardMovementCache.created_at.desc(), DashboardMovementCache.id.desc())
        .limit(10)
        .all()
    )
    if rows:
        return rows

    for movement in db.query(Movement).order_by(Movement.created_at.desc()).limit(10).all():
        cache_dashboard_movement(db, movement)
    db.commit()
    return (
        db.query(DashboardMovementCache)
        .order_by(DashboardMovementCache.created_at.desc(), DashboardMovementCache.id.desc())
        .limit(10)
        .all()
    )


def register_movement(db, sku, tipo, quantidade, usuario_id, documento="", observacao="", allow_negative=False):
    if sku is None:
        raise ValueError("SKU nao encontrado.")
    if not sku.active and tipo in {"ENTRADA", "SAIDA"}:
        raise ValueError("SKU inativo. Movimentacao bloqueada.")

    quantidade = to_decimal(quantidade)
    if quantidade <= 0 and tipo in {"ENTRADA", "SAIDA"}:
        raise ValueError("Quantidade deve ser maior que zero.")

    balance = ensure_balance(db, sku)
    saldo_anterior = to_decimal(balance.saldo_atual)

    if tipo == "ENTRADA":
        saldo_posterior = saldo_anterior + quantidade
    elif tipo == "SAIDA":
        saldo_posterior = saldo_anterior - quantidade
        if saldo_posterior < 0 and not allow_negative:
            raise ValueError("Saida bloqueada: saldo insuficiente.")
    elif tipo in {"INVENTARIO", "AJUSTE"}:
        saldo_posterior = saldo_anterior + quantidade
        if saldo_posterior < 0 and not allow_negative:
            raise ValueError("Ajuste bloqueado: saldo final negativo.")
    else:
        raise ValueError("Tipo de movimentacao invalido.")

    balance.saldo_atual = saldo_posterior
    movement = Movement(
        sku_id=sku.id,
        tipo=tipo,
        quantidade=quantidade,
        saldo_anterior=saldo_anterior,
        saldo_posterior=saldo_posterior,
        usuario_id=usuario_id,
        documento=documento or None,
        observacao=observacao or None,
    )
    db.add(movement)
    db.flush()
    cache_dashboard_movement(db, movement)
    db.commit()
    return movement


def adjust_balance_to_count(db, sku, counted_qty, usuario_id, documento="", observacao=""):
    balance = ensure_balance(db, sku)
    saldo_atual = to_decimal(balance.saldo_atual)
    counted_qty = to_decimal(counted_qty)
    if counted_qty < 0:
        raise ValueError("Saldo contado nao pode ser negativo.")
    diff = counted_qty - saldo_atual
    return register_movement(
        db,
        sku,
        "INVENTARIO",
        diff,
        usuario_id,
        documento=documento,
        observacao=observacao or f"Ajuste de inventario para saldo contado {decimal_to_str(counted_qty)}.",
        allow_negative=True,
    )


def get_active_inventory_session(db):
    return (
        db.query(InventorySession)
        .filter(InventorySession.status == "ABERTO")
        .order_by(InventorySession.opened_at.desc())
        .first()
    )


def open_inventory_session(db, user_id, observacao=""):
    active = get_active_inventory_session(db)
    if active:
        return active, False
    session = InventorySession(opened_by=user_id, observacao=observacao or None)
    db.add(session)
    db.commit()
    return session, True


def save_inventory_count(db, session_id, sku, quantidade_contada, user_id):
    quantidade_contada = to_decimal(quantidade_contada)
    if quantidade_contada < 0:
        raise ValueError("Quantidade contada nao pode ser negativa.")
    balance = ensure_balance(db, sku)
    saldo_sistema = to_decimal(balance.saldo_atual)
    diferenca = quantidade_contada - saldo_sistema

    count = (
        db.query(InventoryCount)
        .filter_by(session_id=session_id, sku_id=sku.id)
        .one_or_none()
    )
    if count is None:
        count = InventoryCount(session_id=session_id, sku_id=sku.id, counted_by=user_id)
        db.add(count)

    count.quantidade_contada = quantidade_contada
    count.saldo_sistema = saldo_sistema
    count.diferenca = diferenca
    count.counted_by = user_id
    count.counted_at = now_utc()
    db.commit()
    return count


def inventory_stats(db, session=None):
    session = session or get_active_inventory_session(db)
    total_active = db.query(SKU).filter(SKU.active.is_(True)).count()
    zero_balance = (
        db.query(SKU)
        .outerjoin(StockBalance)
        .filter(SKU.active.is_(True))
        .filter((StockBalance.saldo_atual == 0) | (StockBalance.saldo_atual.is_(None)))
        .count()
    )

    if not session:
        return {
            "session": None,
            "total_skus": total_active,
            "counted": 0,
            "pending": total_active,
            "divergences": 0,
            "zero_balance": zero_balance,
            "positive_diff": 0,
            "negative_diff": 0,
        }

    counted = db.query(InventoryCount).filter_by(session_id=session.id).count()
    divergences = (
        db.query(InventoryCount)
        .filter(InventoryCount.session_id == session.id, InventoryCount.diferenca != 0)
        .count()
    )
    positive_diff = (
        db.query(InventoryCount)
        .filter(InventoryCount.session_id == session.id, InventoryCount.diferenca > 0)
        .count()
    )
    negative_diff = (
        db.query(InventoryCount)
        .filter(InventoryCount.session_id == session.id, InventoryCount.diferenca < 0)
        .count()
    )
    return {
        "session": session,
        "total_skus": total_active,
        "counted": counted,
        "pending": max(total_active - counted, 0),
        "divergences": divergences,
        "zero_balance": zero_balance,
        "positive_diff": positive_diff,
        "negative_diff": negative_diff,
    }


def close_inventory_and_adjust(db, session, user_id):
    if not session or session.status != "ABERTO":
        raise ValueError("Nao ha inventario aberto.")

    counts = db.query(InventoryCount).filter_by(session_id=session.id).all()
    adjusted = 0
    for count in counts:
        movement = adjust_balance_to_count(
            db,
            count.sku,
            count.quantidade_contada,
            user_id,
            documento=f"INVENTARIO-{session.id}",
            observacao=f"Inventario {session.id}: saldo sistema {decimal_to_str(count.saldo_sistema)}, contagem {decimal_to_str(count.quantidade_contada)}.",
        )
        if movement:
            adjusted += 1

    session.status = "FECHADO"
    session.closed_by = user_id
    session.closed_at = now_utc()
    db.commit()
    return adjusted


def reset_operational_data(db):
    deleted = {}
    deleted["label_print_jobs"] = db.query(LabelPrintJob).delete(synchronize_session=False)
    deleted["inventory_counts"] = db.query(InventoryCount).delete(synchronize_session=False)
    deleted["inventory_sessions"] = db.query(InventorySession).delete(synchronize_session=False)
    deleted["movements"] = db.query(Movement).delete(synchronize_session=False)
    db.commit()
    return deleted
