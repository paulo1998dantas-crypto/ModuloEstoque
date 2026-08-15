import os
from uuid import UUID
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import Uuid, bindparam, func, text
from sqlalchemy.orm import aliased

from models import (
    AppSetting,
    BomComponent,
    DashboardMovementCache,
    ErpMovementReferenceHistory,
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
ACTIVE_MOVEMENT_STATUS = "ATIVA"
VALID_CONTEXT_KINDS = {"WORK_ORDER", "SETOR", "REFERENCIA", "LEGACY"}


def movement_context_enabled():
    return os.environ.get("ERP_MOVEMENT_CONTEXT_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "sim",
        "on",
    }


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


def append_manual_entry_exception(observation, reason):
    observation = str(observation or "").strip()
    reason = str(reason or "").strip()
    note = f"Excecao de entrada manual: {reason}"
    if not reason or note in observation:
        return observation
    return " | ".join(value for value in (observation, note) if value)


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


def _normalized_identifier(value):
    value = str(value or "").strip().lower().replace("-", "")
    return value or None


def _normalized_text(value):
    value = str(value or "").strip()
    return value or None


def _requested_movement_context(
    work_order_id=None,
    context_kind=None,
    setor="",
    reference_text="",
):
    work_order_id = _normalized_identifier(work_order_id)
    setor = _normalized_text(setor)
    reference_text = _normalized_text(reference_text)
    if work_order_id:
        resolved_kind = "WORK_ORDER"
    elif setor:
        resolved_kind = "SETOR"
    elif reference_text:
        resolved_kind = "REFERENCIA"
    elif context_kind in VALID_CONTEXT_KINDS:
        resolved_kind = context_kind
    else:
        resolved_kind = "LEGACY"
    return {
        "work_order_id": work_order_id,
        "context_kind": resolved_kind,
        "setor": setor,
        "reference_text": reference_text,
    }


def _movement_matches_command(
    movement,
    *,
    sku_id,
    tipo,
    quantidade,
    related_movement_id=None,
    work_order_id=None,
    context_kind=None,
    setor="",
    reference_text="",
    source_type=None,
    operation_id=None,
    parent_movement_id=None,
):
    requested_context = _requested_movement_context(
        work_order_id=work_order_id,
        context_kind=context_kind,
        setor=setor,
        reference_text=reference_text,
    )
    return (
        movement.sku_id == sku_id
        and movement.tipo == tipo
        and to_decimal(movement.quantidade) == to_decimal(quantidade)
        and movement.related_movement_id == related_movement_id
        and _normalized_identifier(movement.work_order_id)
        == requested_context["work_order_id"]
        and (movement.context_kind or "LEGACY")
        == requested_context["context_kind"]
        and _normalized_text(movement.setor) == requested_context["setor"]
        and _normalized_text(movement.reference_text)
        == requested_context["reference_text"]
        and _normalized_text(movement.source_type)
        == _normalized_text(source_type)
        and _normalized_identifier(movement.operation_id)
        == _normalized_identifier(operation_id)
        and movement.parent_movement_id == parent_movement_id
    )


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


def resolve_movement_context(
    db,
    work_order_id=None,
    setor="",
    reference_text="",
    require_context=False,
    active_work_order_only=True,
):
    work_order_id = str(work_order_id or "").strip() or None
    setor = str(setor or "").strip()
    reference_text = str(reference_text or "").strip()
    work_order = None
    if work_order_id:
        status_filter = (
            """
            and w.status in ('ATIVA','EM_PRODUÇÃO','EM_PRODUCAO')
            and coalesce(w.technical_status,'ABERTA')='ABERTA'
            """
            if active_work_order_only
            else ""
        )
        work_order = db.execute(
            text(
                f"""
                select w.id,w.numero_os,w.status,e.item_number,v.chassi
                  from erp_work_orders w
                  join erp_vehicle_entries e on e.id=w.vehicle_entry_id
                  join erp_vehicles v on v.id=e.vehicle_id
                 where w.id=:id
                   {status_filter}
                 limit 1
                """
            ).bindparams(bindparam("id", type_=Uuid(as_uuid=False))),
            {"id": work_order_id},
        ).mappings().first()
        if not work_order:
            raise ValueError("O.S. nao encontrada ou nao esta ativa.")
        context_kind = "WORK_ORDER"
    elif setor:
        context_kind = "SETOR"
    elif reference_text:
        context_kind = "REFERENCIA"
    else:
        context_kind = "LEGACY"
        if require_context:
            raise ValueError("Informe uma O.S. ativa, setor ou referencia.")
    return {
        "work_order_id": work_order_id,
        "context_kind": context_kind,
        "setor": setor or None,
        "reference_text": reference_text or None,
        "work_order": dict(work_order) if work_order else None,
    }


def resolve_active_work_order_reference(db, reference):
    reference = str(reference or "").strip()
    if not reference:
        return None
    rows = (
        db.execute(
            text(
                """
                select w.id,w.numero_os,w.status,e.item_number,v.chassi
                  from erp_work_orders w
                  join erp_vehicle_entries e on e.id=w.vehicle_entry_id
                  join erp_vehicles v on v.id=e.vehicle_id
                 where w.status in ('ATIVA','EM_PRODUÇÃO','EM_PRODUCAO')
                   and coalesce(w.technical_status,'ABERTA')='ABERTA'
                   and (
                        upper(trim(coalesce(w.numero_os,'')))=upper(:reference)
                        or cast(e.item_number as text)=:reference
                        or upper(trim(coalesce(v.chassi,'')))=upper(:reference)
                        or upper(substr(trim(coalesce(v.chassi,'')),-8))=upper(:reference)
                   )
                 order by e.item_number desc
                 limit 2
                """
            ),
            {"reference": reference},
        )
        .mappings()
        .all()
    )
    if not rows:
        raise ValueError(f"O.S. ativa nao encontrada para a referencia {reference}.")
    if len(rows) > 1:
        raise ValueError(
            f"Referencia {reference} corresponde a mais de uma O.S. ativa; "
            "selecione a O.S. explicitamente."
        )
    return dict(rows[0])


def movement_context_from_movement(movement):
    return {
        "work_order_id": movement.work_order_id,
        "context_kind": movement.context_kind or "LEGACY",
        "setor": movement.setor,
        "reference_text": movement.reference_text,
    }


def update_movement_context(
    db,
    movement,
    context,
    actor_user_id,
    reason="",
    propagate_related=False,
):
    before = movement_context_from_movement(movement)
    after = {
        "work_order_id": context.get("work_order_id"),
        "context_kind": context.get("context_kind") or "LEGACY",
        "setor": context.get("setor"),
        "reference_text": context.get("reference_text"),
    }
    if before == after:
        return False
    history = ErpMovementReferenceHistory(
        id=str(uuid4()),
        movement_id=movement.id,
        previous_work_order_id=before["work_order_id"],
        new_work_order_id=after["work_order_id"],
        previous_context_kind=before["context_kind"],
        new_context_kind=after["context_kind"],
        previous_setor=before["setor"],
        new_setor=after["setor"],
        previous_reference_text=before["reference_text"],
        new_reference_text=after["reference_text"],
        changed_by=actor_user_id,
        reason=str(reason or "").strip(),
    )
    db.add(history)
    movement.work_order_id = after["work_order_id"]
    movement.context_kind = after["context_kind"]
    movement.setor = after["setor"]
    movement.reference_text = after["reference_text"]
    movement.link_updated_at = now_utc()
    movement.link_updated_by = actor_user_id
    if propagate_related:
        children = (
            db.query(Movement)
            .filter(
                Movement.related_movement_id == movement.id,
                Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
            )
            .all()
        )
        for child in children:
            update_movement_context(
                db,
                child,
                context,
                actor_user_id,
                reason=reason,
                propagate_related=False,
            )
    db.flush()
    return True


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
    work_order_id=None,
    context_kind=None,
    setor="",
    reference_text="",
    link_updated_by=None,
    require_context=False,
    source_type=None,
    source_id=None,
    source_line_id=None,
    idempotency_key=None,
    operation_id=None,
    parent_movement_id=None,
    require_available_for_commitment=False,
):
    if sku is None:
        raise ValueError("COD nao encontrado.")
    if tipo == "SAIDA":
        tipo = "EMPENHO"
    if not sku.active and tipo in {"ENTRADA", "EMPENHO", "BAIXA"}:
        raise ValueError("COD inativo. Movimentacao bloqueada.")

    quantidade = to_decimal(quantidade)
    if quantidade <= 0 and tipo in {"ENTRADA", "EMPENHO", "BAIXA", "REJEICAO"}:
        raise ValueError("Quantidade deve ser maior que zero.")

    idempotency_key = str(idempotency_key or "").strip() or None

    def normalize_source_uuid(value, label):
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return str(UUID(raw))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"{label} deve ser um UUID valido.") from exc

    source_id = normalize_source_uuid(source_id, "source_id")
    source_line_id = normalize_source_uuid(source_line_id, "source_line_id")

    def replay_if_present():
        if not idempotency_key:
            return None
        existing = (
            db.query(Movement)
            .filter(Movement.idempotency_key == idempotency_key)
            .one_or_none()
        )
        if existing is None:
            return None
        same_command = _movement_matches_command(
            existing,
            sku_id=sku.id,
            tipo=tipo,
            quantidade=quantidade,
            related_movement_id=related_movement_id,
            work_order_id=work_order_id,
            context_kind=context_kind,
            setor=setor,
            reference_text=reference_text,
            source_type=source_type,
            operation_id=operation_id,
            parent_movement_id=parent_movement_id,
        )
        if not same_command:
            raise ValueError(
                "Chave de idempotencia ja utilizada por outra movimentacao."
            )
        return existing

    replayed = replay_if_present()
    if replayed is not None:
        return replayed

    context = resolve_movement_context(
        db,
        work_order_id=work_order_id,
        setor=setor,
        reference_text=reference_text,
        require_context=(
            require_context
            and tipo in {"EMPENHO", "BAIXA"}
            and movement_context_enabled()
        ),
    )
    if context_kind in VALID_CONTEXT_KINDS and not any(
        (work_order_id, setor, reference_text)
    ):
        context["context_kind"] = context_kind

    balance = ensure_balance(db, sku)
    db.flush()
    balance = (
        db.query(StockBalance)
        .filter(StockBalance.sku_id == sku.id)
        .with_for_update()
        .one()
    )
    # The balance row serializes commands for the same SKU. Recheck after the
    # lock so two retries racing with the same key cannot both insert.
    replayed = replay_if_present()
    if replayed is not None:
        return replayed
    saldo_anterior = to_decimal(balance.saldo_atual)

    # Production orders reserve material without changing physical balance.  Do
    # this check after locking the balance row, otherwise two OP requests for
    # the same SKU can both observe the same available quantity and overbook it.
    if tipo == "EMPENHO" and require_available_for_commitment:
        already_committed = pending_commitments_by_sku(db, [sku.id]).get(
            sku.id, Decimal("0.000")
        )
        available_for_commitment = saldo_anterior - already_committed
        if quantidade > available_for_commitment:
            raise ValueError(
                "Empenho bloqueado: saldo disponivel insuficiente para a Ordem de Producao."
            )

    if tipo == "ENTRADA":
        saldo_posterior = saldo_anterior + quantidade
    elif tipo == "EMPENHO":
        saldo_posterior = saldo_anterior
    elif tipo == "BAIXA":
        saldo_posterior = saldo_anterior - quantidade
        if saldo_posterior < 0 and not allow_negative:
            raise ValueError("Baixa bloqueada: saldo insuficiente.")
    elif tipo == "REJEICAO":
        # A rejected material is recorded for traceability, but it was
        # returned/devolved and therefore must never alter physical stock.
        saldo_posterior = saldo_anterior
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
        work_order_id=context["work_order_id"],
        context_kind=context["context_kind"],
        setor=context["setor"],
        reference_text=context["reference_text"],
        source_type=str(source_type or "").strip() or None,
        source_id=source_id,
        source_line_id=source_line_id,
        idempotency_key=idempotency_key,
        operation_id=str(operation_id or "").strip() or None,
        parent_movement_id=parent_movement_id,
        link_updated_at=now_utc() if context["context_kind"] != "LEGACY" else None,
        link_updated_by=link_updated_by,
        movement_status=ACTIVE_MOVEMENT_STATUS,
    )
    db.add(movement)
    db.flush()
    cache_dashboard_movement(db, movement)
    if commit:
        db.commit()
    return movement


def pending_commitments_by_sku(db, sku_ids=None):
    commitment_query = db.query(Movement.sku_id, func.coalesce(func.sum(Movement.quantidade), 0)).filter(
        Movement.tipo.in_(COMMITMENT_TYPES),
        Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
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
        .filter(
            Movement.tipo == "BAIXA",
            Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
            parent.tipo.in_(COMMITMENT_TYPES),
            parent.movement_status == ACTIVE_MOVEMENT_STATUS,
        )
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
    if (
        movement is None
        or movement.tipo not in COMMITMENT_TYPES
        or movement.movement_status != ACTIVE_MOVEMENT_STATUS
    ):
        return Decimal("0.000")
    baixado = (
        db.query(func.coalesce(func.sum(Movement.quantidade), 0))
        .filter(
            Movement.tipo == "BAIXA",
            Movement.related_movement_id == movement.id,
            Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
        )
        .scalar()
    )
    return max(to_decimal(movement.quantidade) - to_decimal(baixado), Decimal("0.000"))


def movement_available_snapshots(db, movements):
    movement_ids = {movement.id for movement in movements if movement and movement.id}
    if not movement_ids:
        return {}
    sku_ids = {movement.sku_id for movement in movements if movement and movement.sku_id}
    snapshots = {}
    pending_by_sku = {sku_id: Decimal("0.000") for sku_id in sku_ids}
    history = (
        db.query(Movement)
        .filter(Movement.sku_id.in_(sku_ids))
        .order_by(Movement.created_at.asc(), Movement.id.asc())
        .all()
    )
    for movement in history:
        sku_id = movement.sku_id
        pending = pending_by_sku.get(sku_id, Decimal("0.000"))
        if movement.movement_status != ACTIVE_MOVEMENT_STATUS:
            if movement.id in movement_ids:
                snapshots[movement.id] = to_decimal(movement.saldo_posterior) - pending
            continue
        if movement.tipo in COMMITMENT_TYPES:
            pending += to_decimal(movement.quantidade)
        elif movement.tipo == "BAIXA" and movement.related_movement_id:
            pending = max(pending - to_decimal(movement.quantidade), Decimal("0.000"))
        pending_by_sku[sku_id] = pending
        if movement.id in movement_ids:
            snapshots[movement.id] = to_decimal(movement.saldo_posterior) - pending
    return snapshots


def register_consumption_from_commitment(
    db,
    commitment,
    quantidade,
    usuario_id,
    documento="",
    observacao="",
    allow_negative=False,
    commit=True,
    work_order_id=None,
    setor="",
    reference_text="",
    correct_context=False,
    context_reason="",
    source_type=None,
    source_id=None,
    source_line_id=None,
    operation_id=None,
    parent_movement_id=None,
    idempotency_key=None,
    preserve_parent_context=False,
):
    if commitment is None:
        raise ValueError("Empenho nao encontrado.")
    commitment_id = commitment.id
    requested_quantity = (
        None if quantidade in (None, "") else to_decimal(quantidade)
    )
    idempotency_key = str(idempotency_key or "").strip() or None
    if idempotency_key:
        replayed = (
            db.query(Movement)
            .filter(Movement.idempotency_key == idempotency_key)
            .one_or_none()
        )
        if replayed is not None:
            expected_context = movement_context_from_movement(commitment)
            if preserve_parent_context or correct_context or any((work_order_id, setor, reference_text)):
                expected_context = _requested_movement_context(
                    work_order_id=work_order_id,
                    setor=setor,
                    reference_text=reference_text,
                )
            if not _movement_matches_command(
                replayed,
                sku_id=commitment.sku_id,
                tipo="BAIXA",
                quantidade=(
                    replayed.quantidade
                    if requested_quantity is None
                    else requested_quantity
                ),
                related_movement_id=commitment_id,
                work_order_id=expected_context["work_order_id"],
                context_kind=expected_context["context_kind"],
                setor=expected_context["setor"],
                reference_text=expected_context["reference_text"],
                source_type=source_type,
                operation_id=operation_id,
                parent_movement_id=parent_movement_id,
            ):
                raise ValueError(
                    "Chave de idempotencia ja utilizada por outra movimentacao."
                )
            return replayed
    # Lock and reload the parent before calculating the pending quantity. On
    # PostgreSQL this serializes concurrent consumptions for the same empenho.
    commitment = (
        db.query(Movement)
        .filter(Movement.id == commitment_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if commitment is None or commitment.tipo not in COMMITMENT_TYPES:
        raise ValueError("Empenho nao encontrado.")
    if preserve_parent_context and commitment.work_order_id:
        raise ValueError(
            "O empenho informado ja pertence a uma O.S.; use apenas saldo compartilhado sem vinculo."
        )
    pending = pending_commitment_for_movement(db, commitment)
    quantidade = pending if requested_quantity is None else requested_quantity
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
    inherited_context = movement_context_from_movement(commitment)
    if preserve_parent_context:
        inherited_context = resolve_movement_context(
            db,
            work_order_id=work_order_id,
            setor=setor,
            reference_text=reference_text,
            require_context=True,
        )
    elif correct_context or any((work_order_id, setor, reference_text)):
        inherited_context = resolve_movement_context(
            db,
            work_order_id=work_order_id,
            setor=setor,
            reference_text=reference_text,
            require_context=movement_context_enabled(),
        )
        if (
            movement_context_from_movement(commitment) != {
                "work_order_id": inherited_context["work_order_id"],
                "context_kind": inherited_context["context_kind"],
                "setor": inherited_context["setor"],
                "reference_text": inherited_context["reference_text"],
            }
            and not str(context_reason or "").strip()
        ):
            raise ValueError("Informe o motivo da correcao do vinculo.")
        update_movement_context(
            db,
            commitment,
            inherited_context,
            usuario_id,
            reason=context_reason,
            propagate_related=True,
        )
    elif movement_context_enabled() and inherited_context["context_kind"] == "LEGACY":
        raise ValueError(
            "Revise o vinculo do empenho: selecione uma O.S., setor ou referencia."
        )
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
        work_order_id=inherited_context["work_order_id"],
        context_kind=inherited_context["context_kind"],
        setor=inherited_context["setor"],
        reference_text=inherited_context["reference_text"],
        link_updated_by=usuario_id,
        require_context=True,
        source_type=source_type,
        source_id=source_id,
        source_line_id=source_line_id,
        operation_id=operation_id,
        parent_movement_id=parent_movement_id,
        idempotency_key=idempotency_key,
    )


def allocate_shared_commitment_to_work_order(
    db,
    commitment,
    quantidade,
    usuario_id,
    work_order_id,
    reason,
    idempotency_key=None,
):
    """Consume part of an unlinked commitment and assign only that BAIXA to an O.S.

    The parent remains in the shared pool with its remaining quantity.  This is
    deliberately different from correcting the parent context: a loose sector
    commitment may feed several vehicles, but each physical consumption is
    linked to exactly one active O.S. and changes stock exactly once.
    """
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("Informe o motivo da apropriacao do saldo compartilhado.")
    if commitment is None or commitment.tipo not in COMMITMENT_TYPES:
        raise ValueError("Empenho compartilhado nao encontrado.")
    if commitment.work_order_id:
        raise ValueError("O empenho ja esta vinculado a uma O.S. e nao pertence ao fluxo compartilhado.")
    return register_consumption_from_commitment(
        db,
        commitment,
        quantidade,
        usuario_id,
        documento=f"APROPRIACAO-OS-{work_order_id}",
        observacao=f"Apropriacao de saldo compartilhado para O.S.: {reason}",
        allow_negative=False,
        work_order_id=work_order_id,
        preserve_parent_context=True,
        source_type="SHARED_COMMITMENT_ALLOCATION",
        idempotency_key=idempotency_key,
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
    idempotency_key=None,
):
    command_key = str(idempotency_key or "").strip() or None
    quantidade = to_decimal(quantidade)
    requested_components = {}
    for row in component_rows:
        component_sku = row.get("sku")
        if component_sku is None:
            raise ValueError("Componente do backflush nao encontrado.")
        component_qty = to_decimal(row.get("quantidade"))
        if component_qty <= 0:
            raise ValueError("Quantidade do componente deve ser maior que zero.")
        requested_components[component_sku.id] = (
            requested_components.get(component_sku.id, Decimal("0.000"))
            + component_qty
        )
    if command_key:
        existing = (
            db.query(Movement)
            .filter(Movement.idempotency_key == f"{command_key}:entry")
            .one_or_none()
        )
        if existing is not None:
            children = (
                db.query(Movement)
                .filter(
                    Movement.operation_id == existing.operation_id,
                    Movement.parent_movement_id == existing.id,
                    Movement.source_type == "BACKFLUSH_CONSUMPTION",
                )
                .all()
            )
            persisted_components = {}
            for child in children:
                persisted_components[child.sku_id] = (
                    persisted_components.get(child.sku_id, Decimal("0.000"))
                    + to_decimal(child.quantidade)
                )
            same_command = (
                existing.sku_id == sku.id
                and existing.tipo == "ENTRADA"
                and to_decimal(existing.quantidade) == quantidade
                and existing.source_type == "MANUAL_ENTRY_BACKFLUSH"
                and existing.parent_movement_id is None
                and persisted_components == requested_components
            )
            if not same_command:
                raise ValueError(
                    "Chave de idempotencia ja utilizada por outro backflush."
                )
            return existing
    operation_id = str(uuid4())
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
        source_type="MANUAL_ENTRY_BACKFLUSH",
        idempotency_key=f"{command_key}:entry" if command_key else None,
        operation_id=operation_id,
    )
    for index, row in enumerate(component_rows, start=1):
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
            source_type="BACKFLUSH_CONSUMPTION",
            idempotency_key=(
                f"{command_key}:component:{index}" if command_key else None
            ),
            operation_id=operation_id,
            parent_movement_id=entry.id,
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


def cancel_movement(
    db,
    movement,
    actor_user_id,
    reason,
    allow_any=False,
    allow_negative=False,
):
    if movement is None:
        raise ValueError("Movimentacao nao encontrada.")
    movement = (
        db.query(Movement)
        .filter(Movement.id == movement.id)
        .with_for_update()
        .one()
    )
    if movement.movement_status == "CANCELADA":
        return movement, movement.cancellation_reversal, True
    if movement.source_type == "MOVEMENT_CANCELLATION":
        raise ValueError(
            "Ajuste compensatorio de cancelamento nao pode ser cancelado diretamente."
        )
    if movement.operation_id:
        return _cancel_composite_movement(
            db,
            movement,
            actor_user_id,
            reason,
            allow_any=allow_any,
            allow_negative=allow_negative,
        )
    if not allow_any and movement.usuario_id != actor_user_id:
        raise ValueError("Voce so pode cancelar movimentacoes registradas por seu usuario.")
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("Informe o motivo do cancelamento.")
    if (
        movement.source_type in {"GOODS_RECEIPT", "GOODS_RECEIPT_REVERSAL"}
        and not allow_any
    ):
        raise ValueError(
            "Movimento de recebimento deve ser estornado pela Inspecao de Recebimento."
        )
    if movement.tipo in COMMITMENT_TYPES:
        has_active_consumption = (
            db.query(Movement.id)
            .filter(
                Movement.related_movement_id == movement.id,
                Movement.tipo == "BAIXA",
                Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
            )
            .first()
            is not None
        )
        if has_active_consumption:
            raise ValueError(
                "Cancelamento bloqueado: o empenho possui baixa ativa vinculada."
            )

    impact = to_decimal(movement.saldo_posterior) - to_decimal(movement.saldo_anterior)
    reversal = None
    if impact:
        reversal = register_movement(
            db,
            movement.sku,
            "AJUSTE",
            -impact,
            actor_user_id,
            documento=f"CANCELAMENTO-MOV-{movement.id}",
            observacao=f"Cancelamento da movimentacao {movement.id}: {reason}",
            allow_negative=allow_negative,
            commit=False,
            context_kind="LEGACY",
        )
        reversal.source_type = "MOVEMENT_CANCELLATION"
        reversal.related_movement_id = movement.id
        reversal.idempotency_key = f"movement-cancellation:{movement.id}"
        reversal.work_order_id = movement.work_order_id
        reversal.context_kind = movement.context_kind
        reversal.setor = movement.setor
        reversal.reference_text = movement.reference_text
        reversal.link_updated_at = now_utc()
        reversal.link_updated_by = actor_user_id

    movement.movement_status = "CANCELADA"
    movement.canceled_at = now_utc()
    movement.canceled_by = actor_user_id
    movement.cancel_reason = reason
    movement.reversal_movement_id = reversal.id if reversal else None
    clear_dashboard_movement_cache(db)
    db.commit()
    return movement, reversal, False


def _cancel_composite_movement(
    db,
    selected_movement,
    actor_user_id,
    reason,
    allow_any=False,
    allow_negative=False,
):
    operation_id = selected_movement.operation_id
    operation = (
        db.query(Movement)
        .filter(Movement.operation_id == operation_id)
        .order_by(Movement.id)
        .with_for_update()
        .all()
    )
    originals = [
        item for item in operation if item.source_type != "MOVEMENT_CANCELLATION"
    ]
    parents = [item for item in originals if item.parent_movement_id is None]
    if len(parents) != 1:
        raise ValueError("Operacao composta inconsistente: movimento pai nao identificado.")
    parent = parents[0]
    if selected_movement.id != parent.id:
        raise ValueError(
            f"Movimento filho do backflush. Cancele o conjunto pelo movimento pai {parent.id}."
        )
    statuses = {item.movement_status or ACTIVE_MOVEMENT_STATUS for item in originals}
    if statuses == {"CANCELADA"}:
        parent.canceled_operation_size = len(originals)
        return parent, parent.cancellation_reversal, True
    if statuses != {ACTIVE_MOVEMENT_STATUS}:
        raise ValueError(
            "Cancelamento parcial bloqueado: o conjunto possui estados divergentes."
        )
    if not allow_any and any(
        item.usuario_id != actor_user_id for item in originals
    ):
        raise ValueError(
            "Voce so pode cancelar conjuntos registrados integralmente por seu usuario."
        )
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("Informe o motivo do cancelamento.")
    if any(
        item.source_type in {"GOODS_RECEIPT", "GOODS_RECEIPT_REVERSAL"}
        for item in originals
    ):
        raise ValueError(
            "Movimento de recebimento deve ser estornado pela Inspecao de Recebimento."
        )

    sku_ids = sorted({item.sku_id for item in originals})
    for item in originals:
        ensure_balance(db, item.sku)
    db.flush()
    (
        db.query(StockBalance)
        .filter(StockBalance.sku_id.in_(sku_ids))
        .order_by(StockBalance.sku_id)
        .with_for_update()
        .all()
    )

    reversal_operation_id = str(uuid4())
    canceled_at = now_utc()
    reversals = {}
    children = sorted(
        (item for item in originals if item.id != parent.id),
        key=lambda item: (item.sku_id, item.id),
    )
    # Reverse component consumptions first. The parent entry is reversed last,
    # after all component stock has been restored.
    for original in [*children, parent]:
        impact = (
            to_decimal(original.saldo_posterior)
            - to_decimal(original.saldo_anterior)
        )
        reversal = register_movement(
            db,
            original.sku,
            "AJUSTE",
            -impact,
            actor_user_id,
            documento=f"CANCELAMENTO-OPERACAO-{parent.id}",
            observacao=(
                f"Estorno do movimento {original.id}, operacao {operation_id}: "
                f"{reason}"
            ),
            allow_negative=allow_negative,
            commit=False,
            related_movement_id=original.id,
            context_kind="LEGACY",
            source_type="MOVEMENT_CANCELLATION",
            idempotency_key=f"operation-cancellation:{operation_id}:{original.id}",
            operation_id=reversal_operation_id,
        )
        reversal.work_order_id = original.work_order_id
        reversal.context_kind = original.context_kind
        reversal.setor = original.setor
        reversal.reference_text = original.reference_text
        reversal.link_updated_at = canceled_at
        reversal.link_updated_by = actor_user_id
        reversals[original.id] = reversal

    parent_reversal = reversals[parent.id]
    for child in children:
        reversals[child.id].parent_movement_id = parent_reversal.id

    for original in originals:
        original.movement_status = "CANCELADA"
        original.canceled_at = canceled_at
        original.canceled_by = actor_user_id
        original.cancel_reason = reason
        original.reversal_movement_id = reversals[original.id].id

    parent.canceled_operation_size = len(originals)
    parent.cancellation_reversals = list(reversals.values())
    clear_dashboard_movement_cache(db)
    db.commit()
    return parent, parent_reversal, False


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
