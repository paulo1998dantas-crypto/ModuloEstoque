import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from models import BomComponent, SKU
from services.estoque_service import (
    ensure_balance,
    get_setting,
    normalize_sku,
    set_setting,
    to_decimal,
)


REGISTRATIONS_TABLE = "cadastro_registros"
BOM_COMPONENTS_TABLE = "cadastro_bom_componentes"
PAGE_SIZE = 1000
DEFAULT_SYNC_INTERVAL_SECONDS = 60
SKU_SYNC_KEY = "cadastro_supabase_skus_synced_at"
BOM_SYNC_KEY = "cadastro_supabase_bom_synced_at"


class CadastroSupabaseError(RuntimeError):
    pass


def _clean(value):
    return "" if value is None else str(value).strip()


def _supabase_url():
    return (
        _clean(os.environ.get("ESTOQUE_CADASTRO_SUPABASE_URL"))
        or _clean(os.environ.get("CADASTRO_SUPABASE_URL"))
        or _clean(os.environ.get("SUPABASE_URL"))
    ).rstrip("/")


def _service_key():
    return (
        _clean(os.environ.get("ESTOQUE_CADASTRO_SUPABASE_SERVICE_ROLE_KEY"))
        or _clean(os.environ.get("CADASTRO_SUPABASE_SERVICE_ROLE_KEY"))
        or _clean(os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))
    )


def configured():
    return bool(_supabase_url() and _service_key())


def enabled():
    mode = _clean(os.environ.get("ESTOQUE_CADASTRO_SYNC_MODE")).lower()
    if mode in {"local", "off", "offline", "arquivo", "excel"}:
        return False
    if mode in {"supabase", "online", "cadastro", "database", "banco"}:
        return True
    return configured()


def status():
    return {
        "enabled": enabled(),
        "configured": configured(),
        "url": _supabase_url(),
        "registrations_table": REGISTRATIONS_TABLE,
        "bom_components_table": BOM_COMPONENTS_TABLE,
    }


def _sync_interval_seconds():
    raw_value = _clean(os.environ.get("ESTOQUE_CADASTRO_SYNC_INTERVAL_SECONDS"))
    if not raw_value:
        return DEFAULT_SYNC_INTERVAL_SECONDS
    try:
        return max(0, int(raw_value))
    except ValueError:
        return DEFAULT_SYNC_INTERVAL_SECONDS


def _last_sync_is_fresh(db, key):
    interval = _sync_interval_seconds()
    if interval <= 0:
        return False
    last_sync = get_setting(db, key, "")
    if not last_sync:
        return False
    try:
        loaded_at = datetime.fromisoformat(last_sync).timestamp()
    except ValueError:
        return False
    return time.time() - loaded_at < interval


def _mark_synced(db, key):
    set_setting(db, key, datetime.now(timezone.utc).isoformat())


def _headers():
    key = _service_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _request_rows(table, select, order, offset):
    if not configured():
        raise CadastroSupabaseError("Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY.")
    query = [
        ("select", select),
        ("order", order),
        ("limit", str(PAGE_SIZE)),
        ("offset", str(offset)),
    ]
    url = f"{_supabase_url()}/rest/v1/{table}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else []
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise CadastroSupabaseError(f"Erro Supabase {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise CadastroSupabaseError(f"Nao foi possivel conectar ao Supabase: {exc}") from exc


def _all_rows(table, select, order):
    rows = []
    offset = 0
    while True:
        page = _request_rows(table, select, order, offset)
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def _first_value(values, keys):
    if not isinstance(values, dict):
        return ""
    for key in keys:
        value = _clean(values.get(key))
        if value:
            return value
    lowered = {str(key).lower(): _clean(value) for key, value in values.items()}
    for key in keys:
        value = lowered.get(key.lower(), "")
        if value:
            return value
    return ""


def _compact_description(*parts):
    return " ".join(_clean(part) for part in parts if _clean(part))


def _limit_text(value, max_length):
    text = _clean(value)
    if max_length and len(text) > max_length:
        return text[:max_length]
    return text


def _group_from_sku(sku):
    return {
        "10": "10 - INSUMO",
        "20": "20 - PRODUTO EM PROCESSO",
        "30": "30 - CONJUNTO / KIT",
        "40": "40 - TRANSFORMACAO",
        "50": "50 - MRO",
    }.get(_clean(sku)[:2], "")


def _status_to_active(value):
    text = _clean(value).upper()
    if not text:
        return True
    return text not in {
        "0",
        "FALSE",
        "NAO",
        "NO",
        "OFF",
        "INATIVO",
        "INATIVA",
        "INATIVADO",
        "INATIVADA",
        "DESATIVADO",
        "DESATIVADA",
        "CANCELADO",
        "CANCELADA",
        "OBSOLETO",
        "OBSOLETA",
        "BLOQUEADO",
        "BLOQUEADA",
    }


def _row_to_sku_data(row):
    sku = normalize_sku(row.get("sku"))
    values = row.get("field_values") if isinstance(row.get("field_values"), dict) else {}
    descricao = _clean(row.get("descricao_primaria")) or _clean(row.get("descricao_secundaria")) or sku
    unidade = _clean(row.get("unidade")) or _first_value(
        values,
        [
            "unidade",
            "unidade_comercial",
            "unidade_interna",
            "un_medi_comercial",
            "un_medi_interna",
            "un_medida",
            "un",
            "um",
        ],
    )
    grupo = _first_value(values, ["grupo", "prefixo"]) or _group_from_sku(sku)
    categoria = _clean(row.get("category_label"))
    status_value = row.get("ativo")
    active = bool(status_value) if isinstance(status_value, bool) else _status_to_active(
        _first_value(values, ["status", "situacao", "situacao_cadastro"])
    )
    return {
        "sku": sku,
        "descricao": _limit_text(descricao or sku, 255),
        "unidade": _limit_text(unidade, 20),
        "grupo": _limit_text(grupo, 120),
        "categoria": _limit_text(categoria, 120),
        "active": active,
    }


def sync_skus_from_cadastro(db, force=False):
    result = {
        "ok": False,
        "skipped": False,
        "source": "Supabase Cadastro",
        "created": 0,
        "updated": 0,
        "balances_updated": 0,
        "status_updated": 0,
        "rows": 0,
        "duplicates_skipped": 0,
        "not_in_source": 0,
        "errors": [],
    }
    if not enabled():
        result["skipped"] = True
        result["errors"].append("Sincronizacao com Cadastro Supabase desativada ou sem credenciais.")
        return result
    if not force and _last_sync_is_fresh(db, SKU_SYNC_KEY):
        result["ok"] = True
        result["skipped"] = True
        return result

    rows = _all_rows(
        REGISTRATIONS_TABLE,
        "sku,category_label,descricao_primaria,descricao_secundaria,sufixo,unidade,ativo,field_values,updated_at",
        "sku.asc",
    )
    seen = set()
    sku_rows = []
    for row in rows:
        data = _row_to_sku_data(row)
        sku_code = data["sku"]
        if not sku_code:
            continue
        if sku_code in seen:
            result["duplicates_skipped"] += 1
            continue
        seen.add(sku_code)
        sku_rows.append(data)

    if not sku_rows:
        result["errors"].append("Nenhum COD encontrado em cadastro_registros.")
        return result

    try:
        existing = {normalize_sku(sku.sku): sku for sku in db.query(SKU).all()}
        for data in sku_rows:
            sku = existing.get(data["sku"])
            created = sku is None
            if created:
                sku = SKU(sku=data["sku"], descricao=data["descricao"], active=data["active"])
                db.add(sku)
                existing[data["sku"]] = sku
            else:
                if sku.active != data["active"]:
                    result["status_updated"] += 1
                sku.descricao = data["descricao"]
                sku.active = data["active"]
            sku.unidade = data["unidade"] or None
            sku.grupo = data["grupo"] or None
            sku.categoria = data["categoria"] or None
            db.flush()
            if created:
                ensure_balance(db, sku)
                result["created"] += 1
            else:
                result["updated"] += 1

        result["not_in_source"] = max(0, len(existing) - len(seen))
        result["rows"] = len(sku_rows)
        db.commit()
        _mark_synced(db, SKU_SYNC_KEY)
        result["ok"] = True
    except Exception as exc:
        db.rollback()
        result["created"] = 0
        result["updated"] = 0
        result["status_updated"] = 0
        result["rows"] = 0
        result["errors"].append(str(exc))
    return result


def sync_bom_from_cadastro(db, force=False):
    result = {
        "ok": False,
        "skipped": False,
        "source": "Supabase Cadastro",
        "processed": 0,
        "items": 0,
        "deleted": 0,
        "files": 0,
        "errors": [],
        "warnings": [],
    }
    if not enabled():
        result["skipped"] = True
        result["errors"].append("Sincronizacao com Cadastro Supabase desativada ou sem credenciais.")
        return result
    if not force and _last_sync_is_fresh(db, BOM_SYNC_KEY):
        result["ok"] = True
        result["skipped"] = True
        return result

    rows = _all_rows(
        BOM_COMPONENTS_TABLE,
        "parent_sku,component_sku,component_descricao,unidade,quantidade,ordem",
        "parent_sku.asc,ordem.asc",
    )
    if not rows:
        result["errors"].append("Nenhuma B.O.M encontrada em cadastro_bom_componentes.")
        return result

    try:
        sku_cache = {normalize_sku(sku.sku): sku for sku in db.query(SKU).filter(SKU.active.is_(True)).all()}
        parent_ids = set()
        rows_by_pair = {}
        for index, row in enumerate(rows, 1):
            parent_code = normalize_sku(row.get("parent_sku"))
            component_code = normalize_sku(row.get("component_sku"))
            if not parent_code or not component_code:
                result["warnings"].append(
                    f"Linha Supabase {index}: item pai ou componente em branco; estrutura pendente ignorada no estoque."
                )
                continue
            parent = sku_cache.get(parent_code)
            component = sku_cache.get(component_code)
            if not parent:
                result["warnings"].append(f"Linha Supabase {index}: item pai {parent_code} nao cadastrado/ativo no estoque.")
                continue
            if not component:
                result["warnings"].append(f"Linha Supabase {index}: componente {component_code} nao cadastrado/ativo no estoque.")
                continue
            if parent.id == component.id:
                result["warnings"].append(f"Linha Supabase {index}: item pai e componente iguais ({parent_code}).")
                continue
            quantidade = to_decimal(row.get("quantidade") or 1)
            if quantidade <= 0:
                result["warnings"].append(f"Linha Supabase {index}: quantidade invalida para {parent_code}/{component_code}.")
                continue
            pair = (parent.id, component.id)
            if pair in rows_by_pair:
                rows_by_pair[pair].quantidade = to_decimal(rows_by_pair[pair].quantidade) + quantidade
                continue
            parent_ids.add(parent.id)
            rows_by_pair[pair] = BomComponent(
                item_sku_id=parent.id,
                component_sku_id=component.id,
                descricao=component.descricao or _clean(row.get("component_descricao")),
                unidade=component.unidade or _clean(row.get("unidade")),
                quantidade=quantidade,
            )

        if not rows_by_pair:
            result["errors"].append("Nenhuma linha B.O.M valida para sincronizar no estoque.")
            return result

        deleted = (
            db.query(BomComponent)
            .filter(BomComponent.item_sku_id.in_(parent_ids))
            .delete(synchronize_session=False)
        )
        for component in rows_by_pair.values():
            db.add(component)
        db.commit()
        _mark_synced(db, BOM_SYNC_KEY)
        result["processed"] = len(rows_by_pair)
        result["items"] = len(parent_ids)
        result["deleted"] = deleted
        result["ok"] = True
    except Exception as exc:
        db.rollback()
        result["processed"] = 0
        result["items"] = 0
        result["deleted"] = 0
        result["errors"].append(str(exc))
    return result


def sync_catalog_from_cadastro(db, include_bom=False, force=False):
    sku_result = sync_skus_from_cadastro(db, force=force)
    bom_result = None
    if include_bom and not sku_result.get("errors"):
        bom_result = sync_bom_from_cadastro(db, force=force)
    return sku_result, bom_result
