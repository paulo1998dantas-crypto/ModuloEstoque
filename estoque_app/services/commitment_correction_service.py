import re
import unicodedata
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, text

from models import ErpMovementReferenceHistory, Movement, SKU, now_utc
from services.estoque_service import (
    clear_dashboard_movement_cache,
    ensure_balance,
    movement_context_from_movement,
    to_decimal,
    update_movement_context,
)


ACTIVE_STATUS = "ATIVA"
COMMITMENT_TYPES = {"EMPENHO", "SAIDA"}
ACTION_ALIASES = {
    "": "CORRIGIR",
    "CORRIGIR": "CORRIGIR",
    "ATUALIZAR": "CORRIGIR",
    "CANCELAR": "CANCELAR",
    "EXCLUIR": "CANCELAR",
    "DESVINCULAR": "DESVINCULAR",
    "IGNORAR": "IGNORAR",
}


def _text(value):
    return str(value or "").strip()


def _normalized(value):
    value = unicodedata.normalize("NFKD", _text(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value.upper()).strip()


def _compact(value):
    return re.sub(r"[^A-Z0-9]", "", _normalized(value))


def _work_order_catalog(db):
    try:
        rows = db.execute(
            text(
                """
                select w.id,w.numero_os,w.status,w.technical_status,
                       e.item_number,v.chassi
                  from erp_work_orders w
                  join erp_vehicle_entries e on e.id=w.vehicle_entry_id
                  join erp_vehicles v on v.id=e.vehicle_id
                 order by e.item_number desc,w.numero_os desc
                """
            )
        ).mappings().all()
    except Exception:
        return []
    catalog = []
    for raw in rows:
        row = dict(raw)
        row["numero_norm"] = _normalized(row.get("numero_os"))
        row["item_norm"] = _normalized(row.get("item_number"))
        row["chassi_norm"] = _compact(row.get("chassi"))
        row["chassi_reduzido"] = row["chassi_norm"][-8:]
        row["chassi_final"] = row["chassi_norm"][-4:]
        catalog.append(row)
    return catalog


def _latest_work_order_key(row):
    try:
        item_number = int(row.get("item_number") or 0)
    except (TypeError, ValueError):
        item_number = 0
    numero_digits = re.findall(r"\d+", _normalized(row.get("numero_os")))
    numero_number = int(numero_digits[-1]) if numero_digits else 0
    return item_number, numero_number, _normalized(row.get("numero_os"))


def resolve_any_work_order_reference(catalog, reference):
    """Resolve referencia historica sem depender de uma O.S. ainda ativa.

    A prioridade evita que os quatro ultimos caracteres de um chassi se
    sobreponham a um numero de O.S./ITEM explicitamente informado. Quando uma
    referencia de chassi pertence a mais de uma passagem do mesmo veiculo, a
    ocorrencia com maior ITEM (e depois maior numero de O.S.) e escolhida.
    """
    raw = _text(reference)
    if not raw:
        return None
    normalized = _normalized(raw)
    compact = _compact(raw)
    tokens = set(re.findall(r"[A-Z0-9]{4,}", normalized))
    explicit_numbers = {
        match
        for match in re.findall(
            r"(?:\bO\s*\.?\s*S\s*\.?|\bITEM)\s*[:#-]?\s*(\d{2,})",
            normalized,
        )
    }
    exact_number = normalized if re.fullmatch(r"\d+", normalized) else None
    # Muitos empenhos legados trazem somente "material 3100", sem o prefixo
    # O.S. Aceitamos um numero isolado, mas ignoramos numeros de NF/O.C./P.C.
    # e parcelas de datas para reduzir correspondencias acidentais.
    free_numbers = set(
        re.findall(r"(?<![\d/])(\d{3,})(?![\d/])", normalized)
    )
    excluded_numbers = set(
        re.findall(
            r"(?:\bNF\b|\bNOTA\b|\bO\s*\.?\s*C\s*\.?|\bP\s*\.?\s*C\s*\.?|\bPEDIDO\b)\s*[:#-]?\s*(\d{3,})",
            normalized,
        )
    )
    free_numbers.difference_update(excluded_numbers)
    matches = []
    for row in catalog:
        number_match = (
            (exact_number and exact_number in {row["numero_norm"], row["item_norm"]})
            or row["numero_norm"] in explicit_numbers
            or row["item_norm"] in explicit_numbers
            or row["numero_norm"] in free_numbers
            or row["item_norm"] in free_numbers
        )
        score = 400 if number_match else 0
        chassi = row["chassi_norm"]
        suffix8 = row["chassi_reduzido"]
        suffix4 = row["chassi_final"]
        if not score and chassi and (
            chassi == compact or (len(chassi) >= 8 and chassi in compact)
        ):
            score = 300
        if not score and len(suffix8) == 8 and (
            compact == suffix8 or suffix8 in tokens
        ):
            score = 200
        if not score and len(suffix4) == 4 and (
            compact == suffix4 or suffix4 in tokens
        ):
            score = 100
        if score:
            matches.append((score, row))
    if not matches:
        return None
    return max(
        matches,
        key=lambda match: (match[0], *_latest_work_order_key(match[1])),
    )[1]


def _active_related_consumed(db, movement_id):
    total = (
        db.query(Movement)
        .filter(
            Movement.related_movement_id == movement_id,
            Movement.tipo == "BAIXA",
            Movement.movement_status == ACTIVE_STATUS,
        )
        .all()
    )
    return sum((to_decimal(row.quantidade) for row in total), Decimal("0"))


def _operation_for_row(db, movement, row, catalog):
    action = ACTION_ALIASES.get(_normalized(row.get("acao_correcao")))
    errors = []
    warnings = []
    changes = []
    if action is None:
        errors.append(
            "ACAO_CORRECAO invalida. Use CORRIGIR, CANCELAR, DESVINCULAR ou IGNORAR."
        )
        action = "INVALIDA"
    if movement is None:
        errors.append("ID_EMPENHO nao encontrado.")
        return {
            "movement_id": row.get("movement_id"),
            "linha": row.get("linha"),
            "action": action,
            "changes": changes,
            "errors": errors,
            "warnings": warnings,
            "row": row,
        }
    if movement.tipo not in COMMITMENT_TYPES:
        errors.append("O ID informado nao corresponde a um empenho.")
    if movement.movement_status != ACTIVE_STATUS and action != "IGNORAR":
        errors.append("Empenho cancelado nao pode ser corrigido por esta planilha.")

    consumed = _active_related_consumed(db, movement.id)
    target_sku = movement.sku
    requested_code = _normalized(row.get("codigo"))
    if requested_code and requested_code != _normalized(movement.sku.sku):
        target_sku = (
            db.query(SKU)
            .filter(func.upper(SKU.sku) == requested_code, SKU.active.is_(True))
            .one_or_none()
        )
        if target_sku is None:
            errors.append(f"COD {requested_code} inexistente ou inativo.")
        elif consumed > 0:
            errors.append("COD nao pode ser alterado porque o empenho possui baixa ativa.")
        else:
            changes.append(
                {"field": "COD", "before": movement.sku.sku, "after": target_sku.sku}
            )

    requested_quantity = row.get("quantidade_empenhada")
    target_quantity = to_decimal(movement.quantidade)
    if requested_quantity not in (None, ""):
        try:
            target_quantity = to_decimal(requested_quantity)
            if target_quantity <= 0:
                raise ValueError("deve ser maior que zero")
            if target_quantity < consumed:
                raise ValueError(
                    f"nao pode ser menor que o total ja baixado ({consumed})"
                )
            if target_quantity != to_decimal(movement.quantidade):
                changes.append(
                    {
                        "field": "QUANTIDADE_EMPENHADA",
                        "before": to_decimal(movement.quantidade),
                        "after": target_quantity,
                    }
                )
        except Exception as exc:
            errors.append(f"Quantidade empenhada invalida: {exc}.")

    target_document = _text(row.get("documento_empenho"))
    if target_document != _text(movement.documento):
        changes.append(
            {
                "field": "DOCUMENTO_EMPENHO",
                "before": movement.documento or "",
                "after": target_document,
            }
        )
    target_note = _text(row.get("observacao_empenho"))
    if target_note != _text(movement.observacao):
        changes.append(
            {
                "field": "OBSERVACAO_EMPENHO",
                "before": movement.observacao or "",
                "after": target_note,
            }
        )

    resolved = None if action == "DESVINCULAR" else resolve_any_work_order_reference(
        catalog, target_document
    )
    target_context = movement_context_from_movement(movement)
    if action == "DESVINCULAR":
        target_context = {
            "work_order_id": None,
            "context_kind": "LEGACY",
            "setor": None,
            "reference_text": target_document or None,
        }
    elif resolved:
        target_context = {
            "work_order_id": str(resolved["id"]),
            "context_kind": "WORK_ORDER",
            "setor": None,
            "reference_text": None,
        }
    elif not movement.work_order_id and target_document:
        target_context = {
            "work_order_id": None,
            "context_kind": "REFERENCIA",
            "setor": None,
            "reference_text": target_document,
        }
        warnings.append("Documento nao corresponde a uma O.S./chassi conhecido; mantido como referencia livre.")
    if target_context != movement_context_from_movement(movement):
        changes.append(
            {
                "field": "VINCULO_OS",
                "before": movement.work_order_id or movement.reference_text or "sem vinculo",
                "after": (
                    f"O.S. {resolved.get('numero_os') or resolved.get('item_number')}"
                    if resolved
                    else target_context.get("reference_text") or "sem vinculo"
                ),
            }
        )

    if action == "CANCELAR":
        if consumed > 0:
            errors.append("Cancelamento bloqueado: o empenho possui baixa ativa vinculada.")
        changes.append(
            {"field": "STATUS", "before": movement.movement_status, "after": "CANCELADA"}
        )
    if action == "IGNORAR":
        changes = []

    return {
        "movement_id": movement.id,
        "linha": row.get("linha"),
        "action": action,
        "changes": changes,
        "errors": errors,
        "warnings": warnings,
        "row": row,
        "target_sku_id": target_sku.id if target_sku else None,
        "target_quantity": target_quantity,
        "target_document": target_document,
        "target_note": target_note,
        "target_context": target_context,
        "resolved_work_order": dict(resolved) if resolved else None,
    }


def preview_commitment_corrections(db, rows):
    catalog = _work_order_catalog(db)
    ids = [int(row["movement_id"]) for row in rows if row.get("movement_id")]
    movements = {
        movement.id: movement
        for movement in db.query(Movement).filter(Movement.id.in_(ids)).all()
    }
    operations = []
    unchanged = 0
    ignored = 0
    for row in rows:
        operation = _operation_for_row(
            db, movements.get(int(row["movement_id"])), row, catalog
        )
        if operation["action"] == "IGNORAR":
            ignored += 1
        elif operation["changes"] or operation["errors"]:
            operations.append(operation)
        else:
            unchanged += 1
    return {
        "operations": operations,
        "errors": [
            f"Linha {operation['linha']}: {error}"
            for operation in operations
            for error in operation["errors"]
        ],
        "changed": sum(1 for operation in operations if operation["changes"]),
        "unchanged": unchanged,
        "ignored": ignored,
        "total_rows": len(rows),
    }


def _write_metadata_audit(db, movement, actor_user_id, reason):
    context = movement_context_from_movement(movement)
    db.add(
        ErpMovementReferenceHistory(
            id=str(uuid4()),
            movement_id=movement.id,
            previous_work_order_id=context["work_order_id"],
            new_work_order_id=context["work_order_id"],
            previous_context_kind=context["context_kind"],
            new_context_kind=context["context_kind"],
            previous_setor=context["setor"],
            new_setor=context["setor"],
            previous_reference_text=context["reference_text"],
            new_reference_text=context["reference_text"],
            changed_by=actor_user_id,
            reason=reason,
        )
    )


def apply_commitment_corrections(db, rows, actor_user_id, global_reason=""):
    ids = sorted({int(row["movement_id"]) for row in rows if row.get("movement_id")})
    if ids:
        db.query(Movement).filter(Movement.id.in_(ids)).order_by(Movement.id).with_for_update().all()
    preview = preview_commitment_corrections(db, rows)
    if preview["errors"]:
        raise ValueError("; ".join(preview["errors"]))
    applied = 0
    canceled = 0
    linked = 0
    for operation in preview["operations"]:
        if not operation["changes"]:
            continue
        movement = db.get(Movement, operation["movement_id"])
        row_reason = _text(operation["row"].get("motivo_correcao"))
        reason = row_reason or _text(global_reason)
        if not reason:
            raise ValueError(
                f"Linha {operation['linha']}: informe MOTIVO_CORRECAO ou um motivo geral."
            )
        before_context = movement_context_from_movement(movement)
        context_changed = before_context != operation["target_context"]
        if operation["action"] == "CANCELAR":
            movement.movement_status = "CANCELADA"
            movement.canceled_at = now_utc()
            movement.canceled_by = actor_user_id
            movement.cancel_reason = reason
            canceled += 1
        else:
            if movement.sku_id != operation["target_sku_id"]:
                target_sku = db.get(SKU, operation["target_sku_id"])
                snapshot = to_decimal(ensure_balance(db, target_sku).saldo_atual)
                movement.sku = target_sku
                movement.saldo_anterior = snapshot
                movement.saldo_posterior = snapshot
            movement.quantidade = operation["target_quantity"]
            movement.documento = operation["target_document"] or None
            movement.observacao = operation["target_note"] or None
            if context_changed:
                update_movement_context(
                    db,
                    movement,
                    operation["target_context"],
                    actor_user_id,
                    reason=f"Correcao em massa: {reason}",
                    propagate_related=True,
                )
                if operation["target_context"].get("work_order_id"):
                    linked += 1
        changed_fields = ", ".join(change["field"] for change in operation["changes"])
        if not context_changed:
            _write_metadata_audit(
                db,
                movement,
                actor_user_id,
                f"Correcao em massa ({changed_fields}): {reason}",
            )
        applied += 1
    clear_dashboard_movement_cache(db)
    db.commit()
    return {
        "processed": applied,
        "canceled": canceled,
        "linked": linked,
        "unchanged": preview["unchanged"],
        "ignored": preview["ignored"],
    }
