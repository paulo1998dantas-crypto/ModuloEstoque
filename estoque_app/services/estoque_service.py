from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.orm import aliased

from models import (
    AppSetting,
    BomComponent,
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
COMMITMENT_TYPES = ("EMPENHO", "SAIDA")


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


def create_or_update_sku(db, data, user=None, commit=True):
    sku_code = normalize_sku(data.get("sku"))
    descricao = str(data.get("descricao") or "").strip()
    if not sku_code:
        raise ValueError("COD e obrigatorio.")
    if not descricao:
        raise ValueError("Descricao e obrigatoria.")

    sku = get_sku_by_code(db, sku_code)
    created = False
    if sku is None:
        sku = SKU(sku=sku_code, descricao=descricao)
        db.add(sku)
        created = True

    sku.descricao = descricao
    if "unidade" in data:
        sku.unidade = str(data.get("unidade") or "").strip() or None
    if "grupo" in data:
        sku.grupo = str(data.get("grupo") or "").strip() or None
    if "categoria" in data:
        sku.categoria = str(data.get("categoria") or "").strip() or None
    if "localizacao" in data:
        sku.localizacao = str(data.get("localizacao") or "").strip() or None
    if "estoque_minimo" in data:
        sku.estoque_minimo = to_optional_decimal(data.get("estoque_minimo"))
    sku.active = bool(data.get("active", True))
    db.flush()
    balance = ensure_balance(db, sku)
    if "saldo_atual" in data:
        balance.saldo_atual = to_decimal(data.get("saldo_atual"))
    if commit:
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


def clear_dashboard_movement_cache(db):
    db.query(DashboardMovementCache).delete(synchronize_session=False)
    db.flush()


def register_movement(
    db,
    sku,
    tipo,
    quantidade,
    usuario_id,
    documento="",
    observacao="",
    allow_negative=False,
    commit=True,
    related_movement_id=None,
):
    if sku is None:
        raise ValueError("COD nao encontrado.")
    if tipo == "SAIDA":
        tipo = "EMPENHO"
    if not sku.active and tipo in {"ENTRADA", "EMPENHO", "BAIXA"}:
        raise ValueError("COD inativo. Movimentacao bloqueada.")

    quantidade = to_decimal(quantidade)
    if quantidade <= 0 and tipo in {"ENTRADA", "EMPENHO", "BAIXA"}:
        raise ValueError("Quantidade deve ser maior que zero.")

    balance = ensure_balance(db, sku)
    saldo_anterior = to_decimal(balance.saldo_atual)

    if tipo == "ENTRADA":
        saldo_posterior = saldo_anterior + quantidade
    elif tipo == "EMPENHO":
        saldo_posterior = saldo_anterior
    elif tipo == "BAIXA":
        saldo_posterior = saldo_anterior - quantidade
        if saldo_posterior < 0 and not allow_negative:
            raise ValueError("Baixa bloqueada: saldo insuficiente.")
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
        related_movement_id=related_movement_id,
        documento=documento or None,
        observacao=observacao or None,
    )
    db.add(movement)
    db.flush()
    cache_dashboard_movement(db, movement)
    if commit:
        db.commit()
    return movement


def pending_commitments_by_sku(db, sku_ids=None):
    commitment_query = db.query(Movement.sku_id, func.coalesce(func.sum(Movement.quantidade), 0)).filter(
        Movement.tipo.in_(COMMITMENT_TYPES)
    )
    if sku_ids is not None:
        commitment_query = commitment_query.filter(Movement.sku_id.in_(sku_ids))
    commitments = {
        sku_id: to_decimal(total)
        for sku_id, total in commitment_query.group_by(Movement.sku_id).all()
    }
    if not commitments:
        return {}

    parent = aliased(Movement)
    baixas_query = (
        db.query(parent.sku_id, func.coalesce(func.sum(Movement.quantidade), 0))
        .join(parent, Movement.related_movement_id == parent.id)
        .filter(Movement.tipo == "BAIXA", parent.tipo.in_(COMMITMENT_TYPES))
    )
    if sku_ids is not None:
        baixas_query = baixas_query.filter(parent.sku_id.in_(sku_ids))
    baixas = {
        sku_id: to_decimal(total)
        for sku_id, total in baixas_query.group_by(parent.sku_id).all()
    }
    return {
        sku_id: max(total - baixas.get(sku_id, Decimal("0.000")), Decimal("0.000"))
        for sku_id, total in commitments.items()
    }


def pending_commitment_for_movement(db, movement):
    if movement is None or movement.tipo not in COMMITMENT_TYPES:
        return Decimal("0.000")
    baixado = (
        db.query(func.coalesce(func.sum(Movement.quantidade), 0))
        .filter(Movement.tipo == "BAIXA", Movement.related_movement_id == movement.id)
        .scalar()
    )
    return max(to_decimal(movement.quantidade) - to_decimal(baixado), Decimal("0.000"))


def register_consumption_from_commitment(
    db,
    commitment,
    quantidade,
    usuario_id,
    documento="",
    observacao="",
    allow_negative=False,
    commit=True,
):
    if commitment is None or commitment.tipo not in COMMITMENT_TYPES:
        raise ValueError("Empenho nao encontrado.")
    pending = pending_commitment_for_movement(db, commitment)
    quantidade = pending if quantidade in (None, "") else to_decimal(quantidade)
    if quantidade <= 0:
        raise ValueError("Quantidade para baixa deve ser maior que zero.")
    if quantidade > pending:
        raise ValueError(
            f"Baixa bloqueada: empenho possui somente {decimal_to_str(pending)} pendente."
        )
    document = documento or commitment.documento or f"BAIXA-EMPENHO-{commitment.id}"
    note = f"Baixa vinculada ao empenho {commitment.id}."
    if observacao:
        note = f"{note} {observacao}"
    return register_movement(
        db,
        commitment.sku,
        "BAIXA",
        quantidade,
        usuario_id,
        documento=document,
        observacao=note,
        allow_negative=allow_negative,
        commit=commit,
        related_movement_id=commitment.id,
    )


def bom_components_for_sku(db, sku):
    if sku is None:
        return []
    return (
        db.query(BomComponent)
        .filter(BomComponent.item_sku_id == sku.id)
        .order_by(BomComponent.id)
        .all()
    )


def build_backflush_preview(db, sku, entry_qty):
    entry_qty = to_decimal(entry_qty)
    if entry_qty <= 0:
        raise ValueError("Quantidade deve ser maior que zero.")

    rows = []
    for component in bom_components_for_sku(db, sku):
        component_sku = component.component_sku
        required_qty = to_decimal(component.quantidade) * entry_qty
        saldo_atual = component_sku.balance.saldo_atual if component_sku and component_sku.balance else 0
        rows.append(
            {
                "sku": component_sku.sku if component_sku else "",
                "descricao": component.descricao or (component_sku.descricao if component_sku else ""),
                "unidade": component.unidade or (component_sku.unidade if component_sku else ""),
                "quantidade": decimal_to_str(required_qty),
                "saldo_atual": decimal_to_str(saldo_atual),
                "bom_quantidade": decimal_to_str(component.quantidade),
            }
        )
    return rows


def parse_backflush_rows(db, component_codes, component_quantities):
    rows_by_sku = {}
    max_len = max(len(component_codes), len(component_quantities))
    for index in range(max_len):
        raw_code = component_codes[index] if index < len(component_codes) else ""
        raw_qty = component_quantities[index] if index < len(component_quantities) else ""
        if not str(raw_code or "").strip() and not str(raw_qty or "").strip():
            continue

        sku = get_sku_by_code(db, raw_code, active_only=True)
        if not sku:
            raise ValueError(f"Backflush linha {index + 1}: componente nao cadastrado ou inativo.")
        qty = to_decimal(raw_qty)
        if qty <= 0:
            raise ValueError(f"Backflush linha {index + 1}: quantidade deve ser maior que zero.")

        key = normalize_sku(raw_code)
        if key in rows_by_sku:
            rows_by_sku[key]["quantidade"] += qty
        else:
            rows_by_sku[key] = {"sku": sku, "quantidade": qty}
    return list(rows_by_sku.values())


def register_entry_with_backflush(
    db,
    sku,
    quantidade,
    usuario_id,
    component_rows,
    documento="",
    observacao="",
    allow_negative=False,
):
    document = documento or f"ENTRADA-BACKFLUSH-{now_utc().strftime('%Y%m%d-%H%M%S')}"
    entry = register_movement(
        db,
        sku,
        "ENTRADA",
        quantidade,
        usuario_id,
        documento=document,
        observacao=observacao,
        commit=False,
    )
    for row in component_rows:
        component_sku = row["sku"]
        consumed = row["quantidade"]
        note = (
            f"Backflush da entrada {entry.id} do item {sku.sku}. "
            f"Quantidade entrada: {decimal_to_str(quantidade)}."
        )
        if observacao:
            note = f"{note} {observacao}"
        register_movement(
            db,
            component_sku,
            "BAIXA",
            consumed,
            usuario_id,
            documento=document,
            observacao=note,
            allow_negative=allow_negative,
            commit=False,
        )
    db.commit()
    return entry


def delete_movement(db, movement, allow_negative=False):
    if movement is None:
        raise ValueError("Movimentacao nao encontrada.")
    if movement.tipo in COMMITMENT_TYPES:
        has_related_baixa = (
            db.query(Movement.id)
            .filter(Movement.related_movement_id == movement.id, Movement.tipo == "BAIXA")
            .first()
            is not None
        )
        if has_related_baixa:
            raise ValueError("Exclusao bloqueada: existe baixa vinculada a este empenho.")

    balance = ensure_balance(db, movement.sku)
    saldo_atual = to_decimal(balance.saldo_atual)
    impacto = to_decimal(movement.saldo_posterior) - to_decimal(movement.saldo_anterior)
    saldo_corrigido = saldo_atual - impacto
    if saldo_corrigido < 0 and not allow_negative:
        raise ValueError("Exclusao bloqueada: o saldo ficaria negativo.")

    balance.saldo_atual = saldo_corrigido
    db.delete(movement)
    clear_dashboard_movement_cache(db)
    db.commit()
    return saldo_corrigido


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


def save_inventory_count(db, session_id, sku, quantidade_contada, user_id, commit=True):
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
    if commit:
        db.commit()
    else:
        db.flush()
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


def reset_sku_base(db):
    deleted = {}
    deleted["label_print_jobs"] = db.query(LabelPrintJob).delete(synchronize_session=False)
    deleted["inventory_counts"] = db.query(InventoryCount).delete(synchronize_session=False)
    deleted["inventory_sessions"] = db.query(InventorySession).delete(synchronize_session=False)
    deleted["movements"] = db.query(Movement).delete(synchronize_session=False)
    deleted["dashboard_movement_cache"] = db.query(DashboardMovementCache).delete(synchronize_session=False)
    deleted["bom_components"] = db.query(BomComponent).delete(synchronize_session=False)
    deleted["stock_balances"] = db.query(StockBalance).delete(synchronize_session=False)
    deleted["skus"] = db.query(SKU).delete(synchronize_session=False)
    db.flush()
    db.expunge_all()
    return deleted
