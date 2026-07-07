from datetime import datetime
from pathlib import Path
import re
import unicodedata

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func, or_

from config import EXPORTS_DIR
from models import BomComponent, InventoryCount, InventorySession, LabelPrintJob, Movement, SKU, StockBalance
from services.etiqueta_service import create_label_job
from services.estoque_service import (
    create_or_update_sku,
    decimal_to_str,
    get_sku_by_code,
    normalize_sku,
    optional_decimal_to_str,
    register_movement,
    save_inventory_count,
    to_decimal,
)


SKU_IMPORT_COLUMNS = ["COD", "DESCRICAO", "UNIDADE", "GRUPO", "CATEGORIA", "SALDO_ATUAL"]
SKU_REQUIRED_COLUMNS = ["COD", "DESCRICAO"]
STOCK_COLUMN_ALIASES = ["SALDO_ATUAL", "ESTOQUE_ATUAL", "ESTOQUE", "SALDO"]
SKU_UNIT_ALIASES = ["UNIDADE", "UNIDADE_DE_MEDIDA", "UNIDADE_MEDIDA", "UM"]
LABEL_IMPORT_COLUMNS = ["COD", "QUANTIDADE"]
CONSUMPTION_IMPORT_COLUMNS = ["COD", "UNIDADE_DE_MEDIDA", "SALDO_CONSUMIDO"]
COMMITMENT_IMPORT_COLUMNS = ["COD", "UNIDADE_DE_MEDIDA", "SALDO_EMPENHADO"]
BOM_IMPORT_COLUMNS = ["ITEM_CODIGO", "COMPONENTE_CODIGO", "DESCRICAO", "UNIDADE", "QUANTIDADE"]
BOM_REQUIRED_COLUMNS = ["ITEM_CODIGO", "COMPONENTE_CODIGO", "DESCRICAO", "UNIDADE", "QUANTIDADE"]
INVENTORY_COUNT_IMPORT_COLUMNS = ["COD", "UNIDADE_DE_MEDIDA", "SALDO_CONTADO"]
INVENTORY_ADD_IMPORT_COLUMNS = ["COD", "UNIDADE_DE_MEDIDA", "SALDO_SOMAR"]
CONSUMPTION_UNIT_ALIASES = ["UNIDADE_DE_MEDIDA", "UNIDADE_MEDIDA", "UNIDADE", "UM"]
CONSUMPTION_QTY_ALIASES = [
    "SALDO_CONSUMIDO",
    "CONSUMO_REAL",
    "QUANTIDADE_CONSUMIDA",
    "QTD_CONSUMIDA",
    "CONSUMIDO",
]
COMMITMENT_QTY_ALIASES = [
    "SALDO_EMPENHADO",
    "QUANTIDADE_EMPENHADA",
    "QTD_EMPENHADA",
    "EMPENHADO",
]
INVENTORY_COUNT_QTY_ALIASES = [
    "SALDO_CONTADO",
    "QUANTIDADE_CONTADA",
    "QTD_CONTADA",
    "CONTAGEM",
    "SALDO_FISICO",
]
INVENTORY_ADD_QTY_ALIASES = [
    "SALDO_SOMAR",
    "QUANTIDADE_SOMAR",
    "QTD_SOMAR",
    "SOMAR_SALDO",
    "ENTRADA_MASSA",
]


def _normalize_header(value):
    text = unicodedata.normalize("NFKD", str(value or "").strip())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")


def _headers(ws, required_columns, max_scan_rows=25):
    for row_number in range(1, min(ws.max_row, max_scan_rows) + 1):
        headers = {
            _normalize_header(cell.value): idx
            for idx, cell in enumerate(ws[row_number], start=1)
            if cell.value is not None and str(cell.value).strip()
        }
        if "COD" in headers and "SKU" not in headers:
            headers["SKU"] = headers["COD"]
        elif "SKU" in headers and "COD" not in headers:
            headers["COD"] = headers["SKU"]
        if all(column in headers for column in required_columns):
            return headers, row_number
    return {}, 1


def _cell(ws, row_number, headers, column_name, default=None):
    index = headers.get(column_name)
    if not index:
        return default
    return ws.cell(row_number, index).value


def _first_cell(ws, row_number, headers, column_names, default=None):
    for column_name in column_names:
        value = _cell(ws, row_number, headers, column_name)
        if value is not None and str(value).strip() != "":
            return value
    return default


def _find_header(headers, aliases):
    for alias in aliases:
        if alias in headers:
            return alias
    return None


def _normalize_text(value):
    text = unicodedata.normalize("NFKD", str(value or "").strip())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.upper()).strip()


def _style_header(ws):
    fill = PatternFill("solid", fgColor="1F6FEB")
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = fill
    ws.freeze_panes = "A2"


def import_skus_from_excel(db, file_obj):
    wb = load_workbook(file_obj, data_only=True)
    ws = wb.active
    headers, header_row = _headers(ws, SKU_REQUIRED_COLUMNS)
    missing = [col for col in SKU_REQUIRED_COLUMNS if col not in headers]
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}")

    result = {"created": 0, "updated": 0, "balances_updated": 0, "errors": []}
    rows = []
    seen_skus = set()
    for row_number in range(header_row + 1, ws.max_row + 1):
        raw_sku = ws.cell(row_number, headers["COD"]).value
        raw_desc = ws.cell(row_number, headers["DESCRICAO"]).value
        if not raw_sku and not raw_desc:
            continue

        existing_sku = get_sku_by_code(db, raw_sku)
        data = {
            "sku": raw_sku,
            "descricao": raw_desc,
            "active": existing_sku.active if existing_sku else True,
        }
        optional_fields = {
            "unidade": SKU_UNIT_ALIASES,
            "grupo": ["GRUPO"],
            "categoria": ["CATEGORIA"],
        }
        for field_name, aliases in optional_fields.items():
            value = _first_cell(ws, row_number, headers, aliases)
            if value is not None and str(value).strip() != "":
                data[field_name] = value
        if any(column in headers for column in STOCK_COLUMN_ALIASES):
            saldo_atual = _first_cell(ws, row_number, headers, STOCK_COLUMN_ALIASES)
            if saldo_atual is not None and str(saldo_atual).strip() != "":
                data["saldo_atual"] = saldo_atual

        try:
            sku_code = normalize_sku(raw_sku)
            if not sku_code:
                raise ValueError("COD e obrigatorio.")
            if not str(raw_desc or "").strip():
                raise ValueError("Descricao e obrigatoria.")
            if sku_code in seen_skus:
                raise ValueError("COD duplicado na planilha.")
            seen_skus.add(sku_code)
            if "saldo_atual" in data:
                to_decimal(data.get("saldo_atual"))
            rows.append(data)
        except Exception as exc:
            result["errors"].append(f"Linha {row_number}: {exc}")

    if not rows and not result["errors"]:
        result["errors"].append("Nenhum COD encontrado na planilha.")
    if result["errors"]:
        db.rollback()
        return result

    try:
        for data in rows:
            _, created = create_or_update_sku(db, data, commit=False)
            if created:
                result["created"] += 1
            else:
                result["updated"] += 1
            if "saldo_atual" in data:
                result["balances_updated"] += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        result["created"] = 0
        result["updated"] = 0
        result["balances_updated"] = 0
        result["errors"].append(str(exc))
    return result


def import_label_jobs_from_excel(db, file_obj, user_id, inventory_session_id=None):
    wb = load_workbook(file_obj, data_only=True)
    ws = wb.active
    headers, header_row = _headers(ws, LABEL_IMPORT_COLUMNS)
    missing = [col for col in LABEL_IMPORT_COLUMNS if col not in headers]
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}")

    result = {"created": 0, "errors": []}
    origem = "INVENTARIO" if inventory_session_id else "EXCEL"
    for row_number in range(header_row + 1, ws.max_row + 1):
        raw_sku = ws.cell(row_number, headers["COD"]).value
        raw_qty = ws.cell(row_number, headers["QUANTIDADE"]).value or 1
        if not raw_sku:
            continue
        sku = get_sku_by_code(db, raw_sku)
        try:
            if not sku:
                raise ValueError("COD nao cadastrado")
            if not sku.active:
                raise ValueError("COD inativo")
            qty = int(to_decimal(raw_qty))
            if qty <= 0:
                raise ValueError("Quantidade deve ser maior que zero")
            create_label_job(db, sku, qty, origem, user_id, inventory_session_id)
            result["created"] += 1
        except Exception as exc:
            db.rollback()
            result["errors"].append(f"Linha {row_number}: {raw_sku} - {exc}")
    return result


def import_consumption_from_excel(db, file_obj, user_id, documento="", observacao="", allow_negative=False):
    wb = load_workbook(file_obj, data_only=True)
    ws = wb.active
    headers, header_row = _headers(ws, ["COD"])
    qty_header = _find_header(headers, CONSUMPTION_QTY_ALIASES)
    unit_header = _find_header(headers, CONSUMPTION_UNIT_ALIASES)
    missing = []
    if "COD" not in headers:
        missing.append("COD")
    if not unit_header:
        missing.append("UNIDADE_DE_MEDIDA")
    if not qty_header:
        missing.append("SALDO_CONSUMIDO")
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}")

    result = {"processed": 0, "total_consumed": "0", "errors": []}
    rows = []
    total_consumed = to_decimal(0)
    for row_number in range(header_row + 1, ws.max_row + 1):
        raw_sku = ws.cell(row_number, headers["COD"]).value
        raw_unit = ws.cell(row_number, headers[unit_header]).value
        raw_consumed = ws.cell(row_number, headers[qty_header]).value
        if not raw_sku and not raw_unit and not raw_consumed:
            continue

        try:
            sku = get_sku_by_code(db, raw_sku, active_only=True)
            if not sku:
                raise ValueError("COD nao cadastrado ou inativo")
            if raw_unit is None or str(raw_unit).strip() == "":
                raise ValueError("Unidade de medida e obrigatoria")
            if sku.unidade and _normalize_text(raw_unit) != _normalize_text(sku.unidade):
                raise ValueError(f"Unidade divergente. Cadastro: {sku.unidade}")
            consumed = to_decimal(raw_consumed)
            if consumed <= 0:
                raise ValueError("Saldo consumido deve ser maior que zero")
            if not allow_negative:
                saldo_atual = to_decimal(sku.balance.saldo_atual if sku.balance else 0)
                if saldo_atual - consumed < 0:
                    raise ValueError("Saldo insuficiente para baixa")
            rows.append((row_number, sku, str(raw_unit).strip(), consumed))
            total_consumed += consumed
        except Exception as exc:
            result["errors"].append(f"Linha {row_number}: {raw_sku or ''} - {exc}")

    if not rows and not result["errors"]:
        result["errors"].append("Nenhum consumo encontrado na planilha.")
    if result["errors"]:
        db.rollback()
        return result

    try:
        document = documento or f"BAIXA-EXCEL-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        for row_number, sku, unidade, consumed in rows:
            note = observacao or f"Baixa por consumo real via planilha. Linha {row_number}; unidade {unidade}."
            register_movement(
                db,
                sku,
                "BAIXA",
                consumed,
                user_id,
                documento=document,
                observacao=note,
                allow_negative=allow_negative,
                commit=False,
            )
            result["processed"] += 1
        db.commit()
        result["total_consumed"] = decimal_to_str(total_consumed)
    except Exception as exc:
        db.rollback()
        result["processed"] = 0
        result["total_consumed"] = "0"
        result["errors"].append(str(exc))
    return result


def import_commitments_from_excel(db, file_obj, user_id, documento="", observacao=""):
    wb = load_workbook(file_obj, data_only=True)
    ws = wb.active
    headers, header_row = _headers(ws, ["COD"])
    qty_header = _find_header(headers, COMMITMENT_QTY_ALIASES)
    unit_header = _find_header(headers, CONSUMPTION_UNIT_ALIASES)
    missing = []
    if "COD" not in headers:
        missing.append("COD")
    if not unit_header:
        missing.append("UNIDADE_DE_MEDIDA")
    if not qty_header:
        missing.append("SALDO_EMPENHADO")
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}")

    result = {"processed": 0, "total_committed": "0", "errors": []}
    rows = []
    total_committed = to_decimal(0)
    for row_number in range(header_row + 1, ws.max_row + 1):
        raw_sku = ws.cell(row_number, headers["COD"]).value
        raw_unit = ws.cell(row_number, headers[unit_header]).value
        raw_committed = ws.cell(row_number, headers[qty_header]).value
        if not raw_sku and not raw_unit and not raw_committed:
            continue

        try:
            sku = get_sku_by_code(db, raw_sku, active_only=True)
            if not sku:
                raise ValueError("COD nao cadastrado ou inativo")
            if raw_unit is None or str(raw_unit).strip() == "":
                raise ValueError("Unidade de medida e obrigatoria")
            if sku.unidade and _normalize_text(raw_unit) != _normalize_text(sku.unidade):
                raise ValueError(f"Unidade divergente. Cadastro: {sku.unidade}")
            committed = to_decimal(raw_committed)
            if committed <= 0:
                raise ValueError("Saldo empenhado deve ser maior que zero")
            rows.append((row_number, sku, str(raw_unit).strip(), committed))
            total_committed += committed
        except Exception as exc:
            result["errors"].append(f"Linha {row_number}: {raw_sku or ''} - {exc}")

    if not rows and not result["errors"]:
        result["errors"].append("Nenhum empenho encontrado na planilha.")
    if result["errors"]:
        db.rollback()
        return result

    try:
        document = documento or f"EMPENHO-EXCEL-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        for row_number, sku, unidade, committed in rows:
            note = observacao or f"Empenho inicial via planilha. Linha {row_number}; unidade {unidade}."
            register_movement(
                db,
                sku,
                "EMPENHO",
                committed,
                user_id,
                documento=document,
                observacao=note,
                commit=False,
            )
            result["processed"] += 1
        db.commit()
        result["total_committed"] = decimal_to_str(total_committed)
    except Exception as exc:
        db.rollback()
        result["processed"] = 0
        result["total_committed"] = "0"
        result["errors"].append(str(exc))
    return result


def import_bom_from_excel(db, file_obj):
    wb = load_workbook(file_obj, data_only=True)
    ws = wb.active
    headers, header_row = _headers(ws, BOM_REQUIRED_COLUMNS)
    missing = [col for col in BOM_REQUIRED_COLUMNS if col not in headers]
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}")

    result = {"processed": 0, "items": 0, "deleted": 0, "errors": []}
    rows = []
    parent_ids = set()
    seen_pairs = set()
    for row_number in range(header_row + 1, ws.max_row + 1):
        raw_item = ws.cell(row_number, headers["ITEM_CODIGO"]).value
        raw_component = ws.cell(row_number, headers["COMPONENTE_CODIGO"]).value
        raw_desc = ws.cell(row_number, headers["DESCRICAO"]).value
        raw_unit = ws.cell(row_number, headers["UNIDADE"]).value
        raw_qty = ws.cell(row_number, headers["QUANTIDADE"]).value
        if not raw_item and not raw_component and not raw_desc and not raw_unit and not raw_qty:
            continue

        try:
            item_sku = get_sku_by_code(db, raw_item, active_only=True)
            if not item_sku:
                raise ValueError("Item pai nao cadastrado ou inativo")
            component_sku = get_sku_by_code(db, raw_component, active_only=True)
            if not component_sku:
                raise ValueError("Componente nao cadastrado ou inativo")
            if item_sku.id == component_sku.id:
                raise ValueError("Item pai e componente nao podem ser iguais")
            descricao = str(raw_desc or "").strip()
            if not descricao:
                raise ValueError("Descricao e obrigatoria")
            unidade = str(raw_unit or "").strip()
            if not unidade:
                raise ValueError("Unidade e obrigatoria")
            if component_sku.unidade and _normalize_text(unidade) != _normalize_text(component_sku.unidade):
                raise ValueError(f"Unidade divergente. Cadastro do componente: {component_sku.unidade}")
            quantidade = to_decimal(raw_qty)
            if quantidade <= 0:
                raise ValueError("Quantidade deve ser maior que zero")
            pair = (item_sku.id, component_sku.id)
            if pair in seen_pairs:
                raise ValueError("Componente duplicado para o mesmo item na planilha")
            seen_pairs.add(pair)
            parent_ids.add(item_sku.id)
            rows.append(
                BomComponent(
                    item_sku_id=item_sku.id,
                    component_sku_id=component_sku.id,
                    descricao=descricao,
                    unidade=unidade,
                    quantidade=quantidade,
                )
            )
        except Exception as exc:
            result["errors"].append(f"Linha {row_number}: {raw_item or ''} / {raw_component or ''} - {exc}")

    if not rows and not result["errors"]:
        result["errors"].append("Nenhuma estrutura B.O.M encontrada na planilha.")
    if result["errors"]:
        db.rollback()
        return result

    try:
        deleted = (
            db.query(BomComponent)
            .filter(BomComponent.item_sku_id.in_(parent_ids))
            .delete(synchronize_session=False)
        )
        for row in rows:
            db.add(row)
        db.commit()
        result["processed"] = len(rows)
        result["items"] = len(parent_ids)
        result["deleted"] = deleted
    except Exception as exc:
        db.rollback()
        result["processed"] = 0
        result["items"] = 0
        result["deleted"] = 0
        result["errors"].append(str(exc))
    return result


def import_inventory_counts_from_excel(db, file_obj, session_id, user_id):
    wb = load_workbook(file_obj, data_only=True)
    ws = wb.active
    headers, header_row = _headers(ws, ["COD"])
    qty_header = _find_header(headers, INVENTORY_COUNT_QTY_ALIASES)
    unit_header = _find_header(headers, CONSUMPTION_UNIT_ALIASES)
    missing = []
    if "COD" not in headers:
        missing.append("COD")
    if not qty_header:
        missing.append("SALDO_CONTADO")
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}")

    result = {"processed": 0, "errors": []}
    rows = []
    seen_skus = set()
    for row_number in range(header_row + 1, ws.max_row + 1):
        raw_sku = ws.cell(row_number, headers["COD"]).value
        raw_unit = ws.cell(row_number, headers[unit_header]).value if unit_header else ""
        raw_counted = ws.cell(row_number, headers[qty_header]).value
        if not raw_sku and not raw_unit and raw_counted in (None, ""):
            continue

        try:
            sku = get_sku_by_code(db, raw_sku, active_only=True)
            if not sku:
                raise ValueError("COD nao cadastrado ou inativo")
            sku_code = normalize_sku(raw_sku)
            if sku_code in seen_skus:
                raise ValueError("COD duplicado na planilha")
            seen_skus.add(sku_code)
            if raw_unit is not None and str(raw_unit).strip():
                if sku.unidade and _normalize_text(raw_unit) != _normalize_text(sku.unidade):
                    raise ValueError(f"Unidade divergente. Cadastro: {sku.unidade}")
            counted = to_decimal(raw_counted)
            if counted < 0:
                raise ValueError("Saldo contado nao pode ser negativo")
            rows.append((row_number, sku, counted))
        except Exception as exc:
            result["errors"].append(f"Linha {row_number}: {raw_sku or ''} - {exc}")

    if not rows and not result["errors"]:
        result["errors"].append("Nenhuma contagem encontrada na planilha.")
    if result["errors"]:
        db.rollback()
        return result

    try:
        for _, sku, counted in rows:
            save_inventory_count(db, session_id, sku, counted, user_id, commit=False)
            result["processed"] += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        result["processed"] = 0
        result["errors"].append(str(exc))
    return result


def import_inventory_balance_additions_from_excel(db, file_obj, session_id, user_id):
    wb = load_workbook(file_obj, data_only=True)
    ws = wb.active
    headers, header_row = _headers(ws, ["COD"])
    qty_header = _find_header(headers, INVENTORY_ADD_QTY_ALIASES)
    unit_header = _find_header(headers, CONSUMPTION_UNIT_ALIASES)
    missing = []
    if "COD" not in headers:
        missing.append("COD")
    if not qty_header:
        missing.append("SALDO_SOMAR")
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}")

    result = {"processed": 0, "total_added": "0", "errors": []}
    rows = []
    seen_skus = set()
    total_added = to_decimal(0)
    for row_number in range(header_row + 1, ws.max_row + 1):
        raw_sku = ws.cell(row_number, headers["COD"]).value
        raw_unit = ws.cell(row_number, headers[unit_header]).value if unit_header else ""
        raw_addition = ws.cell(row_number, headers[qty_header]).value
        if not raw_sku and not raw_unit and raw_addition in (None, ""):
            continue

        try:
            sku = get_sku_by_code(db, raw_sku, active_only=True)
            if not sku:
                raise ValueError("COD nao cadastrado ou inativo")
            sku_code = normalize_sku(raw_sku)
            if sku_code in seen_skus:
                raise ValueError("COD duplicado na planilha")
            seen_skus.add(sku_code)
            if raw_unit is not None and str(raw_unit).strip():
                if sku.unidade and _normalize_text(raw_unit) != _normalize_text(sku.unidade):
                    raise ValueError(f"Unidade divergente. Cadastro: {sku.unidade}")
            addition = to_decimal(raw_addition)
            if addition <= 0:
                raise ValueError("Saldo a somar deve ser maior que zero")
            saldo_atual = to_decimal(sku.balance.saldo_atual if sku.balance else 0)
            counted = saldo_atual + addition
            rows.append((row_number, sku, counted, addition))
            total_added += addition
        except Exception as exc:
            result["errors"].append(f"Linha {row_number}: {raw_sku or ''} - {exc}")

    if not rows and not result["errors"]:
        result["errors"].append("Nenhum saldo a somar encontrado na planilha.")
    if result["errors"]:
        db.rollback()
        return result

    try:
        for _, sku, counted, _ in rows:
            save_inventory_count(db, session_id, sku, counted, user_id, commit=False)
            result["processed"] += 1
        db.commit()
        result["total_added"] = decimal_to_str(total_added)
    except Exception as exc:
        db.rollback()
        result["processed"] = 0
        result["total_added"] = "0"
        result["errors"].append(str(exc))
    return result


def create_template_files(base_dir):
    template_path = Path(base_dir) / "template_importacao_skus.xlsx"
    sample_path = Path(base_dir) / "dados_exemplo.xlsx"
    label_template_path = Path(base_dir) / "template_etiquetas_lote.xlsx"
    consumption_template_path = Path(base_dir) / "template_baixa_consumo.xlsx"
    commitment_template_path = Path(base_dir) / "template_empenhos.xlsx"
    bom_template_path = Path(base_dir) / "template_bom.xlsx"
    inventory_count_template_path = Path(base_dir) / "template_contagem_inventario.xlsx"
    inventory_add_template_path = Path(base_dir) / "template_somar_saldo_inventario.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Codigos"
    ws.append(SKU_IMPORT_COLUMNS)
    _style_header(ws)
    for width, column in zip([18, 48, 16, 24, 24, 18], "ABCDEF"):
        ws.column_dimensions[column].width = width
    wb.save(template_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Codigos"
    ws.append(SKU_IMPORT_COLUMNS)
    examples = [
        ["PAR-0001", "Parafuso sextavado M8 x 30 zincado", "UN", "Ferragens", "Fixadores", 125],
        ["CAB-0012", "Cabo eletrico flexivel 2,5 mm preto", "M", "Componentes", "Eletrica", ""],
        ["CON-0100", "Conector pneumatico reto 1/4", "UN", "Componentes", "Pneumatica", 12],
    ]
    for row in examples:
        ws.append(row)
    _style_header(ws)
    for width, column in zip([18, 48, 16, 24, 24, 18], "ABCDEF"):
        ws.column_dimensions[column].width = width
    wb.save(sample_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Etiquetas"
    ws.append(LABEL_IMPORT_COLUMNS)
    ws.append(["PAR-0001", 2])
    ws.append(["CAB-0012", 1])
    _style_header(ws)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 16
    wb.save(label_template_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Baixa"
    ws.append(CONSUMPTION_IMPORT_COLUMNS)
    ws.append(["PAR-0001", "UN", 3])
    ws.append(["CAB-0012", "M", 12.5])
    _style_header(ws)
    for width, column in zip([22, 22, 20], "ABC"):
        ws.column_dimensions[column].width = width
    wb.save(consumption_template_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Empenhos"
    ws.append(COMMITMENT_IMPORT_COLUMNS)
    ws.append(["PAR-0001", "UN", 5])
    ws.append(["CAB-0012", "M", 18])
    _style_header(ws)
    for width, column in zip([22, 22, 20], "ABC"):
        ws.column_dimensions[column].width = width
    wb.save(commitment_template_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append(BOM_IMPORT_COLUMNS)
    ws.append(["PROD-0001", "PAR-0001", "Parafuso sextavado M8 x 30 zincado", "UN", 4])
    ws.append(["PROD-0001", "CAB-0012", "Cabo eletrico flexivel 2,5 mm preto", "M", 2.5])
    _style_header(ws)
    for width, column in zip([22, 24, 48, 14, 18], "ABCDE"):
        ws.column_dimensions[column].width = width
    wb.save(bom_template_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Contagem"
    ws.append(INVENTORY_COUNT_IMPORT_COLUMNS)
    ws.append(["PAR-0001", "UN", 124])
    ws.append(["CAB-0012", "M", 78.5])
    _style_header(ws)
    for width, column in zip([22, 22, 18], "ABC"):
        ws.column_dimensions[column].width = width
    wb.save(inventory_count_template_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "Somar saldo"
    ws.append(INVENTORY_ADD_IMPORT_COLUMNS)
    ws.append(["PAR-0001", "UN", 10])
    ws.append(["CAB-0012", "M", 4.5])
    _style_header(ws)
    for width, column in zip([22, 22, 18], "ABC"):
        ws.column_dimensions[column].width = width
    wb.save(inventory_add_template_path)

    return (
        template_path,
        sample_path,
        label_template_path,
        consumption_template_path,
        commitment_template_path,
        bom_template_path,
        inventory_count_template_path,
        inventory_add_template_path,
    )


def _metadata(ws, title, user, filters=None):
    ws.append([title])
    ws.append(["Gerado em", datetime.now().strftime("%d/%m/%Y %H:%M:%S")])
    ws.append(["Usuario", user.username if user else ""])
    ws.append(["Filtros", filters or "Sem filtros"])
    ws.append([])


def _autosize(ws):
    for column_cells in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 60)


def _save_report(wb, prefix):
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORTS_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb.save(path)
    return path


def export_stock_report(db, user, filters):
    wb = Workbook()
    ws = wb.active
    ws.title = "Estoque"
    _metadata(ws, "Relatorio de estoque atual", user, filters)
    ws.append(["COD", "Descricao", "Unidade", "Grupo", "Categoria", "Localizacao", "Saldo atual", "Estoque minimo", "Ativo", "Status"])

    query = db.query(SKU).outerjoin(StockBalance)
    sku_filter = filters.get("sku")
    desc_filter = filters.get("descricao")
    group = filters.get("grupo")
    category = filters.get("categoria")
    location = filters.get("localizacao")
    active = filters.get("active")
    low = filters.get("saldo_baixo")

    if sku_filter:
        query = query.filter(SKU.sku.ilike(f"%{sku_filter}%"))
    if desc_filter:
        query = query.filter(SKU.descricao.ilike(f"%{desc_filter}%"))
    if group:
        query = query.filter(SKU.grupo.ilike(f"%{group}%"))
    if category:
        query = query.filter(SKU.categoria.ilike(f"%{category}%"))
    if location:
        query = query.filter(SKU.localizacao.ilike(f"%{location}%"))
    if active == "1":
        query = query.filter(SKU.active.is_(True))
    elif active == "0":
        query = query.filter(SKU.active.is_(False))
    if low == "1":
        query = query.filter(SKU.estoque_minimo.isnot(None))
        query = query.filter(or_(StockBalance.saldo_atual <= SKU.estoque_minimo, StockBalance.saldo_atual.is_(None)))

    for sku in query.order_by(SKU.sku).all():
        saldo = sku.balance.saldo_atual if sku.balance else 0
        minimo = sku.estoque_minimo
        if to_decimal(saldo) <= 0:
            status = "ZERADO"
        elif minimo is not None and to_decimal(saldo) <= to_decimal(minimo):
            status = "BAIXO"
        else:
            status = "OK"
        ws.append([
            sku.sku,
            sku.descricao,
            sku.unidade,
            sku.grupo,
            sku.categoria,
            sku.localizacao,
            decimal_to_str(saldo),
            optional_decimal_to_str(sku.estoque_minimo),
            "Sim" if sku.active else "Nao",
            status,
        ])
    _autosize(ws)
    return _save_report(wb, "relatorio_estoque")


def export_movements_report(db, user, tipo=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Movimentacoes"
    if isinstance(tipo, (list, tuple, set)):
        tipo_label = ", ".join(tipo)
        title = f"Relatorio de {tipo_label.lower()}"
        filter_label = f"tipo={tipo_label}"
    else:
        title = "Relatorio completo de movimentacoes" if not tipo else f"Relatorio de {tipo.lower()}"
        filter_label = f"tipo={tipo}" if tipo else "Sem filtros"
    _metadata(ws, title, user, filter_label)
    ws.append([
        "ID",
        "Data/Hora",
        "Usuario",
        "COD",
        "Descricao",
        "Tipo",
        "Quantidade",
        "Saldo anterior",
        "Saldo posterior",
        "Documento",
        "Observacao",
    ])
    query = db.query(Movement).join(SKU)
    if isinstance(tipo, (list, tuple, set)):
        query = query.filter(Movement.tipo.in_(tipo))
    elif tipo:
        query = query.filter(Movement.tipo == tipo)
    for mv in query.order_by(Movement.created_at.desc()).all():
        tipo_display = "EMPENHO" if mv.tipo == "SAIDA" else mv.tipo
        ws.append([
            mv.id,
            mv.created_at.strftime("%d/%m/%Y %H:%M:%S"),
            mv.usuario.username,
            mv.sku.sku,
            mv.sku.descricao,
            tipo_display,
            decimal_to_str(mv.quantidade),
            decimal_to_str(mv.saldo_anterior),
            decimal_to_str(mv.saldo_posterior),
            mv.documento,
            mv.observacao,
        ])
    _autosize(ws)
    if isinstance(tipo, (list, tuple, set)):
        prefix = "relatorio_empenhos"
    else:
        prefix = f"relatorio_{tipo.lower() if tipo else 'movimentacoes'}"
    return _save_report(wb, prefix)


def export_inventory_report(db, user, session_id=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"
    _metadata(ws, "Relatorio de inventario", user, f"sessao={session_id}" if session_id else "Todas as sessoes")
    ws.append([
        "Sessao",
        "Status",
        "COD",
        "Descricao",
        "Saldo sistema",
        "Quantidade contada",
        "Diferenca",
        "Contado por",
        "Contado em",
    ])
    query = db.query(InventoryCount).join(InventorySession)
    if session_id:
        query = query.filter(InventoryCount.session_id == session_id)
    for count in query.order_by(InventoryCount.counted_at.desc()).all():
        ws.append([
            count.session_id,
            count.session.status,
            count.sku.sku,
            count.sku.descricao,
            decimal_to_str(count.saldo_sistema),
            decimal_to_str(count.quantidade_contada),
            decimal_to_str(count.diferenca),
            count.user.username,
            count.counted_at.strftime("%d/%m/%Y %H:%M:%S"),
        ])
    _autosize(ws)
    return _save_report(wb, "relatorio_inventario")


def export_inventory_preview(db, user, session):
    wb = Workbook()
    ws = wb.active
    ws.title = "Previa"
    _metadata(ws, "Previa do inventario", user, f"sessao={session.id if session else ''}")
    ws.append(["COD", "Descricao", "Saldo sistema", "Contagem", "Diferenca", "Status"])

    counts_by_sku = {}
    if session:
        counts_by_sku = {
            count.sku_id: count
            for count in db.query(InventoryCount).filter_by(session_id=session.id).all()
        }
    for sku in db.query(SKU).filter(SKU.active.is_(True)).order_by(SKU.sku).all():
        count = counts_by_sku.get(sku.id)
        saldo = sku.balance.saldo_atual if sku.balance else 0
        ws.append([
            sku.sku,
            sku.descricao,
            decimal_to_str(saldo),
            decimal_to_str(count.quantidade_contada) if count else "",
            decimal_to_str(count.diferenca) if count else "",
            "CONTADO" if count else "PENDENTE",
        ])
    _autosize(ws)
    return _save_report(wb, "previa_inventario")


def label_queue_summary(db, session_id=None):
    query = db.query(LabelPrintJob)
    if session_id:
        query = query.filter(LabelPrintJob.inventory_session_id == session_id)
    rows = query.all()
    return {
        "total": len(rows),
        "pendente": sum(1 for row in rows if row.status == "PENDENTE"),
        "impresso": sum(1 for row in rows if row.status == "IMPRESSO"),
        "erro": sum(1 for row in rows if row.status == "ERRO"),
    }
