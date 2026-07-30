"""Controlled material conversion orders (O.P.).

An O.P. is intentionally separate from vehicle work orders.  It reserves the
material sent to a shop and, on completion, performs one atomic conversion:
consume the reserved input(s) and receive the configured final SKU.
"""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func

from models import (
    ErpProductionOrder,
    ErpProductionOrderEvent,
    ErpProductionOrderInput,
    Movement,
    SKU,
)
from services.estoque_service import (
    ACTIVE_MOVEMENT_STATUS,
    cancel_movement,
    get_sku_by_code,
    pending_commitments_by_sku,
    register_consumption_from_commitment,
    register_movement,
    to_decimal,
)


OPEN_STATUSES = {"RASCUNHO", "LIBERADA", "EM_SERRALHERIA", "EMPENHADA", "PARCIAL"}


def _clean(value):
    return str(value or "").strip()


def _snapshot(sku, catalog_snapshot=None):
    catalog_snapshot = catalog_snapshot if isinstance(catalog_snapshot, dict) else {}
    return {
        "sku": sku.sku,
        "descricao": sku.descricao,
        "unidade": sku.unidade or "UN",
        "cadastro": catalog_snapshot,
    }


def _event(db, order_id, action, actor_user_id, details=None):
    db.add(
        ErpProductionOrderEvent(
            production_order_id=order_id,
            action=action,
            actor_user_id=actor_user_id,
            details=details or {},
        )
    )


def _next_number():
    # The UUID is the technical key.  This human-readable number remains safe
    # even when more than one service creates a draft simultaneously.
    return f"OP-{datetime.utcnow():%Y%m%d}-{uuid4().hex[:8].upper()}"


def _order_dict(order, include_events=False):
    inputs = []
    for line in order.inputs:
        inputs.append(
            {
                "id": str(line.id),
                "numero_linha": line.numero_linha,
                "sku_id": line.source_sku_id,
                "sku": line.source_sku.sku if line.source_sku else "",
                "descricao": line.source_sku.descricao if line.source_sku else "",
                "unidade": line.source_sku.unidade if line.source_sku else "UN",
                "quantidade_planejada": str(line.quantidade_planejada),
                "quantidade_empenhada": str(line.quantidade_empenhada),
                "quantidade_baixada": str(line.quantidade_baixada),
                "commitment_movement_id": line.commitment_movement_id,
                "consumption_movement_id": line.consumption_movement_id,
                "snapshot": line.source_snapshot or {},
            }
        )
    result = {
        "id": str(order.id),
        "numero_op": order.numero_op,
        "status": order.status,
        "setor": order.setor,
        "target_sku_id": order.target_sku_id,
        "target_sku": order.target_sku.sku if order.target_sku else "",
        "target_descricao": order.target_sku.descricao if order.target_sku else "",
        "unidade": order.unidade or (order.target_sku.unidade if order.target_sku else "UN"),
        "quantidade_planejada": str(order.quantidade_planejada),
        "quantidade_produzida": str(order.quantidade_produzida),
        "producao_tipo": order.producao_tipo,
        "destino_descricao": order.destino_descricao or "",
        "chassi_lote": order.chassi_lote or "",
        "cliente_nome": order.cliente_nome or "",
        "municipio": order.municipio or "",
        "mmv": order.mmv or "",
        "observacoes": order.observacoes or "",
        "target_snapshot": order.target_snapshot or {},
        "selected_parameters": order.selected_parameters or [],
        "process_snapshot": order.process_snapshot or [],
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None,
        "cancel_reason": order.cancel_reason or "",
        "inputs": inputs,
    }
    if include_events:
        result["events"] = [
            {
                "action": event.action,
                "actor_user_id": event.actor_user_id,
                "details": event.details or {},
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in sorted(
                order.events, key=lambda item: (item.created_at, str(item.id))
            )
        ]
    return result


def _get_order(db, order_id, lock=False):
    query = db.query(ErpProductionOrder).filter(ErpProductionOrder.id == str(order_id))
    if lock:
        query = query.with_for_update()
    order = query.one_or_none()
    if not order:
        raise ValueError("Ordem de Produção não encontrada.")
    return order


def create_production_order(db, data, actor_user_id):
    key = _clean(data.get("idempotency_key"))
    if not key:
        raise ValueError("idempotency_key é obrigatória.")
    existing = db.query(ErpProductionOrder).filter_by(idempotency_key=key).one_or_none()
    if existing:
        return {"order": _order_dict(existing, include_events=True), "replayed": True}

    target = get_sku_by_code(db, data.get("target_sku"), active_only=True)
    if not target:
        raise ValueError("O SKU final deve existir e estar ativo no Estoque.")
    quantity = to_decimal(data.get("quantidade_planejada"))
    if quantity <= 0:
        raise ValueError("Informe uma quantidade planejada maior que zero.")
    raw_inputs = data.get("inputs") or []
    if not raw_inputs:
        raise ValueError("Informe ao menos um SKU de composição/origem.")

    order = ErpProductionOrder(
        numero_op=_clean(data.get("numero_op")) or _next_number(),
        status="LIBERADA",
        setor=_clean(data.get("setor")) or "SERRALHERIA",
        target_sku_id=target.id,
        quantidade_planejada=quantity,
        unidade=_clean(data.get("unidade")) or target.unidade or "UN",
        producao_tipo=_clean(data.get("producao_tipo")).upper() or "ESTOQUE",
        destino_descricao=_clean(data.get("destino_descricao")),
        chassi_lote=_clean(data.get("chassi_lote")),
        cliente_nome=_clean(data.get("cliente_nome")),
        municipio=_clean(data.get("municipio")),
        mmv=_clean(data.get("mmv")),
        observacoes=_clean(data.get("observacoes")),
        target_snapshot=_snapshot(target, data.get("target_snapshot")),
        selected_parameters=data.get("selected_parameters") or [],
        process_snapshot=data.get("process_snapshot") or [],
        idempotency_key=key,
        created_by=actor_user_id,
        released_by=actor_user_id,
    )
    db.add(order)
    db.flush()
    for number, raw_line in enumerate(raw_inputs, start=1):
        source = get_sku_by_code(db, raw_line.get("sku"), active_only=True)
        if not source:
            raise ValueError(f"SKU de composição inválido na linha {number}.")
        source_quantity = to_decimal(raw_line.get("quantidade"))
        if source_quantity <= 0:
            raise ValueError(f"Quantidade de composição inválida na linha {number}.")
        db.add(
            ErpProductionOrderInput(
                production_order_id=order.id,
                numero_linha=number,
                source_sku_id=source.id,
                quantidade_planejada=source_quantity,
                source_snapshot=_snapshot(source, raw_line.get("snapshot")),
            )
        )
    _event(db, order.id, "LIBERADA", actor_user_id, {"numero_op": order.numero_op})
    db.commit()
    db.refresh(order)
    return {"order": _order_dict(order, include_events=True), "replayed": False}


def list_production_orders(db, status="", limit=100):
    query = db.query(ErpProductionOrder).order_by(ErpProductionOrder.created_at.desc())
    if _clean(status):
        query = query.filter(ErpProductionOrder.status == _clean(status).upper())
    return {"orders": [_order_dict(order) for order in query.limit(max(1, min(int(limit), 250))).all()]}


def production_order_detail(db, order_id):
    return {"order": _order_dict(_get_order(db, order_id), include_events=True)}


def commit_production_order(db, order_id, actor_user_id):
    order = _get_order(db, order_id, lock=True)
    if order.status in {"EMPENHADA", "EM_SERRALHERIA"}:
        return {"order": _order_dict(order, include_events=True), "movement_ids": [], "replayed": True}
    if order.status not in {"LIBERADA", "EM_SERRALHERIA", "EMPENHADA"}:
        raise ValueError("A O.P. não está disponível para empenho.")
    lines = (
        db.query(ErpProductionOrderInput)
        .filter_by(production_order_id=order.id)
        .order_by(ErpProductionOrderInput.numero_linha)
        .with_for_update()
        .all()
    )
    pending_by_sku = pending_commitments_by_sku(db, [line.source_sku_id for line in lines])
    movements = []
    for line in lines:
        if line.commitment_movement_id:
            continue
        source = line.source_sku
        available = to_decimal(source.balance.saldo_atual if source.balance else 0) - pending_by_sku.get(source.id, Decimal("0"))
        if line.quantidade_planejada > available:
            raise ValueError(
                f"Empenho bloqueado para {source.sku}: disponível {available}, solicitado {line.quantidade_planejada}."
            )
        movement = register_movement(
            db,
            source,
            "EMPENHO",
            line.quantidade_planejada,
            actor_user_id,
            documento=order.numero_op,
            observacao=f"Material enviado para {order.setor}: O.P. {order.numero_op}.",
            setor=order.setor,
            reference_text=order.numero_op,
            source_type="PRODUCTION_ORDER",
            source_id=order.id,
            source_line_id=line.id,
            idempotency_key=f"production-order:{order.id}:commit:{line.id}",
            require_available_for_commitment=True,
            commit=False,
        )
        line.commitment_movement_id = movement.id
        line.quantidade_empenhada = line.quantidade_planejada
        pending_by_sku[source.id] = pending_by_sku.get(source.id, Decimal("0")) + line.quantidade_planejada
        movements.append(movement.id)
    order.status = "EMPENHADA"
    _event(db, order.id, "EMPENHADA", actor_user_id, {"movement_ids": movements})
    db.commit()
    return {"order": _order_dict(order, include_events=True), "movement_ids": movements}


def complete_production_order(db, order_id, actor_user_id, quantity=None):
    order = _get_order(db, order_id, lock=True)
    if order.status == "CONCLUIDA":
        return {"order": _order_dict(order, include_events=True), "output_movement_id": None, "replayed": True}
    if order.status not in {"EMPENHADA", "EM_SERRALHERIA", "PARCIAL"}:
        raise ValueError("A O.P. deve estar empenhada antes do backflush.")
    produced = to_decimal(quantity if quantity not in (None, "") else order.quantidade_planejada)
    if produced != to_decimal(order.quantidade_planejada):
        raise ValueError("A conclusão parcial da O.P. ainda não é suportada; conclua a quantidade planejada.")
    lines = (
        db.query(ErpProductionOrderInput)
        .filter_by(production_order_id=order.id)
        .order_by(ErpProductionOrderInput.numero_linha)
        .with_for_update()
        .all()
    )
    if any(line.commitment_movement_id is None for line in lines):
        raise ValueError("Empenhe todos os itens de composição antes de concluir a O.P.")

    operation_id = str(order.id)
    consumption_ids = []
    first_consumption_id = None
    for line in lines:
        commitment = db.get(Movement, line.commitment_movement_id)
        if not commitment or commitment.movement_status != ACTIVE_MOVEMENT_STATUS:
            raise ValueError("Existe empenho inválido ou cancelado na O.P.")
        consumption = register_consumption_from_commitment(
            db,
            commitment,
            line.quantidade_planejada,
            actor_user_id,
            documento=order.numero_op,
            observacao=f"Backflush da O.P. {order.numero_op}.",
            setor=order.setor,
            reference_text=order.numero_op,
            source_type="PRODUCTION_ORDER",
            source_id=order.id,
            source_line_id=line.id,
            operation_id=operation_id,
            parent_movement_id=first_consumption_id,
            idempotency_key=f"production-order:{order.id}:consume:{line.id}",
            commit=False,
        )
        first_consumption_id = first_consumption_id or consumption.id
        line.consumption_movement_id = consumption.id
        line.quantidade_baixada = line.quantidade_planejada
        consumption_ids.append(consumption.id)

    output = register_movement(
        db,
        order.target_sku,
        "ENTRADA",
        produced,
        actor_user_id,
        documento=order.numero_op,
        observacao=f"Produto final gerado pelo backflush da O.P. {order.numero_op}.",
        setor=order.setor,
        reference_text=order.numero_op,
        source_type="PRODUCTION_ORDER",
        source_id=order.id,
        operation_id=operation_id,
        parent_movement_id=first_consumption_id,
        idempotency_key=f"production-order:{order.id}:output",
        commit=False,
    )
    order.status = "CONCLUIDA"
    order.quantidade_produzida = produced
    order.completed_by = actor_user_id
    order.completed_at = datetime.utcnow()
    order.completed_operation_id = operation_id
    _event(
        db,
        order.id,
        "CONCLUIDA_BACKFLUSH",
        actor_user_id,
        {"consumption_movement_ids": consumption_ids, "output_movement_id": output.id},
    )
    db.commit()
    return {"order": _order_dict(order, include_events=True), "output_movement_id": output.id}


def cancel_production_order(db, order_id, actor_user_id, reason):
    order = _get_order(db, order_id, lock=True)
    reason = _clean(reason)
    if not reason:
        raise ValueError("Informe o motivo do cancelamento da O.P.")
    if order.status == "CANCELADA":
        return {"order": _order_dict(order, include_events=True), "replayed": True}
    related = None
    if order.completed_operation_id:
        related = (
            db.query(Movement)
            .filter(
                Movement.operation_id == str(order.completed_operation_id),
                Movement.parent_movement_id.is_(None),
                Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
            )
            .order_by(Movement.id.asc())
            .first()
        )
        if related:
            cancel_movement(db, related, actor_user_id, reason, allow_any=True)
    else:
        commitments = [
            db.get(Movement, line.commitment_movement_id)
            for line in order.inputs
            if line.commitment_movement_id
        ]
        for commitment in commitments:
            if commitment and commitment.movement_status == ACTIVE_MOVEMENT_STATUS:
                cancel_movement(db, commitment, actor_user_id, reason, allow_any=True)
    order.status = "CANCELADA"
    order.cancelled_at = datetime.utcnow()
    order.cancelled_by = actor_user_id
    order.cancel_reason = reason
    _event(db, order.id, "CANCELADA", actor_user_id, {"motivo": reason})
    db.commit()
    return {"order": _order_dict(order, include_events=True), "replayed": False}
