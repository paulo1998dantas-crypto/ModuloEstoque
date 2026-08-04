import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, inspect, text

from models import BomComponent, Movement, SKU


TERMINAL_WORK_ORDER_STATUSES = {
    "FINALIZADA",
    "ENTREGUE",
    "RETIRADA",
    "CANCELADA",
    "ARQUIVADA",
}
ACTIVE_MOVEMENT_STATUS = "ATIVA"
COMMITMENT_TYPES = {"EMPENHO", "SAIDA"}


def _decimal(value):
    try:
        return Decimal(str(value or 0).replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _normalize_code(value):
    return str(value or "").strip().upper()


def _uuid_key(value):
    """Compara UUIDs vindos como hex ou no formato canônico com hífens."""
    return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


def _schema_ready(db):
    bind = db.get_bind()
    schema = None if bind.dialect.name == "sqlite" else "public"
    inspector = inspect(bind)
    required = {"suprimentos_documentos", "erp_work_orders", "erp_vehicle_entries", "erp_vehicles"}
    return all(inspector.has_table(table, schema=schema) for table in required)


def _work_order_documents(db, work_order_id=None):
    if not _schema_ready(db):
        return []
    rows = db.execute(
        text(
            """
            select w.id as work_order_id,w.numero_os,w.status,w.technical_status,
                   w.cliente_nome,e.item_number,v.chassi,
                   d.id as document_id,d.numero as document_number,d.status as document_status,
                   d.composicao,d.updated_at as document_updated_at,
                   case when d.erp_work_order_id=w.id then 1 else 0 end as direct_link
              from erp_work_orders w
              join erp_vehicle_entries e on e.id=w.vehicle_entry_id
              join erp_vehicles v on v.id=e.vehicle_id
              join suprimentos_documentos d
                on d.tipo='os'
               and (d.erp_work_order_id=w.id or d.numero=w.numero_os)
             where coalesce(w.technical_status,'ABERTA')='ABERTA'
               and upper(coalesce(w.status,'')) not in
                   ('FINALIZADA','ENTREGUE','RETIRADA','CANCELADA','ARQUIVADA')
             order by e.item_number desc,direct_link desc,d.updated_at desc
            """
        )
    ).mappings()
    selected = {}
    for raw in rows:
        row = dict(raw)
        key = _uuid_key(row["work_order_id"])
        if work_order_id and key != _uuid_key(work_order_id):
            continue
        selected.setdefault(key, row)
    return list(selected.values())


def _bom_catalog(db):
    skus = db.query(SKU).all()
    sku_by_id = {sku.id: sku for sku in skus}
    sku_by_code = {_normalize_code(sku.sku): sku for sku in skus}
    children = defaultdict(list)
    for component in db.query(BomComponent).all():
        parent = sku_by_id.get(component.item_sku_id)
        child = sku_by_id.get(component.component_sku_id)
        if not parent or not child:
            continue
        children[_normalize_code(parent.sku)].append(
            (_normalize_code(child.sku), _decimal(component.quantidade))
        )
    return sku_by_code, children


def _explode_coverage(code, quantity, children):
    result = defaultdict(lambda: Decimal("0"))

    def visit(current_code, current_qty, ancestry):
        normalized = _normalize_code(current_code)
        if not normalized or current_qty <= 0:
            return
        result[normalized] += current_qty
        if normalized in ancestry:
            return
        next_ancestry = set(ancestry)
        next_ancestry.add(normalized)
        for child_code, base_qty in children.get(normalized, []):
            if child_code in next_ancestry or base_qty <= 0:
                continue
            visit(child_code, current_qty * base_qty, next_ancestry)

    visit(code, _decimal(quantity), set())
    return result


def _shared_commitment_candidates(db, children, needed_codes):
    """Return active, unallocated commitments that can cover each needed SKU.

    A shared commitment remains outside any O.S. until an administrator creates
    a linked BAIXA from it.  Its balance is therefore informative only: it must
    never reduce several O.S. needs merely because the same pool is visible to
    all of them.
    """
    needed_codes = {_normalize_code(code) for code in needed_codes if code}
    if not needed_codes:
        return defaultdict(list)

    commitments = (
        db.query(Movement)
        .filter(
            Movement.tipo.in_(COMMITMENT_TYPES),
            Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
            Movement.work_order_id.is_(None),
        )
        .order_by(Movement.created_at.asc(), Movement.id.asc())
        .all()
    )
    if not commitments:
        return defaultdict(list)

    commitment_ids = [movement.id for movement in commitments]
    consumed = {
        movement_id: _decimal(quantity)
        for movement_id, quantity in (
            db.query(
                Movement.related_movement_id,
                func.coalesce(func.sum(Movement.quantidade), 0),
            )
            .filter(
                Movement.related_movement_id.in_(commitment_ids),
                Movement.tipo == "BAIXA",
                Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
            )
            .group_by(Movement.related_movement_id)
            .all()
        )
    }

    by_needed_code = defaultdict(list)
    for movement in commitments:
        pending = max(
            _decimal(movement.quantidade) - consumed.get(movement.id, Decimal("0")),
            Decimal("0"),
        )
        if pending <= 0 or not movement.sku:
            continue
        source_code = _normalize_code(movement.sku.sku)
        coverage_per_unit = _explode_coverage(source_code, Decimal("1"), children)
        for needed_code in needed_codes.intersection(coverage_per_unit):
            factor = coverage_per_unit[needed_code]
            if factor <= 0:
                continue
            by_needed_code[needed_code].append(
                {
                    "movement_id": movement.id,
                    "codigo": source_code,
                    "descricao": movement.sku.descricao or "",
                    "unidade": movement.sku.unidade or "",
                    "quantidade_pendente": pending,
                    "fator_cobertura": factor,
                    "quantidade_equivalente": pending * factor,
                    "setor": movement.setor or "",
                    "referencia": movement.reference_text or "",
                    "documento": movement.documento or "",
                    "created_at": movement.created_at,
                }
            )
    return by_needed_code


def calculate_work_order_needs(db, work_order_id=None, pending_only=False):
    """Calcula necessidade vigente sem gravar saldos ou movimentos.

    A composicao da O.S. e a fonte da necessidade. Empenhos cobrem o produto
    empenhado e toda a sua arvore B.O.M. Uma BAIXA filha de um empenho nao e
    somada novamente; uma BAIXA avulsa vinculada a O.S. conta como cobertura.
    """
    documents = _work_order_documents(db, work_order_id=work_order_id)
    if not documents:
        return {
            "lines": [],
            "summary": {"work_orders": 0, "need_items": 0, "covered_items": 0, "pending_items": 0},
        }

    sku_by_code, children = _bom_catalog(db)
    work_order_ids = {_uuid_key(row["work_order_id"]) for row in documents}
    movements = (
        db.query(Movement)
        .filter(
            Movement.work_order_id.isnot(None),
            Movement.movement_status == ACTIVE_MOVEMENT_STATUS,
            Movement.tipo.in_(["EMPENHO", "SAIDA", "BAIXA"]),
        )
        .all()
    )
    movements = [
        movement
        for movement in movements
        if _uuid_key(movement.work_order_id) in work_order_ids
    ]
    active_commitment_ids = {
        movement.id for movement in movements if movement.tipo in COMMITMENT_TYPES
    }
    coverage = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for movement in movements:
        if movement.tipo == "BAIXA" and movement.related_movement_id in active_commitment_ids:
            continue
        if movement.tipo not in COMMITMENT_TYPES and movement.tipo != "BAIXA":
            continue
        exploded = _explode_coverage(movement.sku.sku, movement.quantidade, children)
        destination = coverage[_uuid_key(movement.work_order_id)]
        for code, quantity in exploded.items():
            destination[code] += quantity

    all_lines = []
    for document in documents:
        required = defaultdict(lambda: Decimal("0"))
        metadata = {}
        for line in _json_list(document.get("composicao")):
            code = _normalize_code(line.get("codigo"))
            quantity = _decimal(line.get("qtd", line.get("quantidade")))
            if not code or quantity <= 0:
                continue
            required[code] += quantity
            current = metadata.setdefault(
                code,
                {
                    "descricao": str(line.get("descricao") or "").strip(),
                    "unidade": str(line.get("unidade") or "").strip(),
                    "setores": set(),
                    "levels": set(),
                    "parent_codes": set(),
                },
            )
            if line.get("setor"):
                current["setores"].add(str(line["setor"]).strip())
            try:
                current["levels"].add(int(line.get("level", 0) or 0))
            except (TypeError, ValueError):
                current["levels"].add(0)
            if line.get("item"):
                current["parent_codes"].add(_normalize_code(line["item"]))

        remaining_coverage = dict(coverage[_uuid_key(document["work_order_id"])])
        for code in sorted(required):
            need = required[code]
            covered = min(need, max(remaining_coverage.get(code, Decimal("0")), Decimal("0")))
            pending = max(need - covered, Decimal("0"))
            meta = metadata[code]
            sku = sku_by_code.get(code)
            row = {
                "work_order_id": str(document["work_order_id"]),
                "numero_os": document.get("numero_os") or document.get("document_number"),
                "item_number": document.get("item_number"),
                "chassi": document.get("chassi") or "",
                "cliente_nome": document.get("cliente_nome") or "",
                "document_id": document.get("document_id"),
                "codigo": code,
                "descricao": meta["descricao"] or (sku.descricao if sku else ""),
                "unidade": meta["unidade"] or ((sku.unidade or "") if sku else ""),
                "quantidade_necessaria": need,
                "quantidade_coberta": covered,
                "quantidade_pendente": pending,
                "setor": " / ".join(sorted(value for value in meta["setores"] if value)),
                "nivel_minimo": min(meta["levels"]) if meta["levels"] else 0,
                "itens_pai": ", ".join(sorted(value for value in meta["parent_codes"] if value)),
                "status_necessidade": "PENDENTE" if pending > 0 else "COBERTA",
            }
            all_lines.append(row)

    shared_candidates = _shared_commitment_candidates(
        db,
        children,
        {line["codigo"] for line in all_lines if line["quantidade_pendente"] > 0},
    )
    shared_movement_ids = set()
    for line in all_lines:
        candidates = shared_candidates.get(line["codigo"], [])
        line["saldo_fluxo_compartilhado"] = sum(
            (candidate["quantidade_equivalente"] for candidate in candidates),
            Decimal("0"),
        )
        line["empenhos_compartilhados"] = candidates
        shared_movement_ids.update(candidate["movement_id"] for candidate in candidates)

    summary = {
        "work_orders": len({line["work_order_id"] for line in all_lines}),
        "need_items": len(all_lines),
        "covered_items": sum(
            1 for line in all_lines if line["quantidade_pendente"] <= 0
        ),
        "pending_items": sum(
            1 for line in all_lines if line["quantidade_pendente"] > 0
        ),
        "quantidade_necessaria": sum(
            (line["quantidade_necessaria"] for line in all_lines), Decimal("0")
        ),
        "quantidade_coberta": sum(
            (line["quantidade_coberta"] for line in all_lines), Decimal("0")
        ),
        "quantidade_pendente": sum(
            (line["quantidade_pendente"] for line in all_lines), Decimal("0")
        ),
        "empenhos_compartilhados": len(shared_movement_ids),
    }
    lines = (
        [line for line in all_lines if line["quantidade_pendente"] > 0]
        if pending_only
        else all_lines
    )
    return {"lines": lines, "summary": summary}
