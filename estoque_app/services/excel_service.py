from datetime import datetime
from pathlib import Path
import re
import unicodedata

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func, or_

from config import EXPORTS_DIR
from models import InventoryCount, InventorySession, LabelPrintJob, Movement, SKU, StockBalance
from services.etiqueta_service import create_label_job
from services.estoque_service import create_or_update_sku, decimal_to_str, get_sku_by_code, optional_decimal_to_str, to_decimal


SKU_IMPORT_COLUMNS = ["SKU", "DESCRICAO", "SALDO_ATUAL", "ESTOQUE_MINIMO"]
SKU_REQUIRED_COLUMNS = ["SKU", "DESCRICAO"]
STOCK_COLUMN_ALIASES = ["SALDO_ATUAL", "ESTOQUE_ATUAL", "ESTOQUE", "SALDO"]
LABEL_IMPORT_COLUMNS = ["SKU", "QUANTIDADE"]


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

    result = {"created": 0, "updated": 0, "errors": []}
    for row_number in range(header_row + 1, ws.max_row + 1):
        raw_sku = ws.cell(row_number, headers["SKU"]).value
        raw_desc = ws.cell(row_number, headers["DESCRICAO"]).value
        if not raw_sku and not raw_desc:
            continue

        data = {
            "sku": raw_sku,
            "descricao": raw_desc,
            "unidade": _cell(ws, row_number, headers, "UNIDADE"),
            "categoria": _cell(ws, row_number, headers, "CATEGORIA"),
            "localizacao": _cell(ws, row_number, headers, "LOCALIZACAO"),
            "active": True,
        }
        if "ESTOQUE_MINIMO" in headers:
            data["estoque_minimo"] = _cell(ws, row_number, headers, "ESTOQUE_MINIMO")
        if any(column in headers for column in STOCK_COLUMN_ALIASES):
            data["saldo_atual"] = _first_cell(ws, row_number, headers, STOCK_COLUMN_ALIASES, "0")
        try:
            _, created = create_or_update_sku(db, data)
            if created:
                result["created"] += 1
            else:
                result["updated"] += 1
        except Exception as exc:
            db.rollback()
            result["errors"].append(f"Linha {row_number}: {exc}")
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
        raw_sku = ws.cell(row_number, headers["SKU"]).value
        raw_qty = ws.cell(row_number, headers["QUANTIDADE"]).value or 1
        if not raw_sku:
            continue
        sku = get_sku_by_code(db, raw_sku)
        try:
            if not sku:
                raise ValueError("SKU nao cadastrado")
            if not sku.active:
                raise ValueError("SKU inativo")
            qty = int(to_decimal(raw_qty))
            if qty <= 0:
                raise ValueError("Quantidade deve ser maior que zero")
            create_label_job(db, sku, qty, origem, user_id, inventory_session_id)
            result["created"] += 1
        except Exception as exc:
            db.rollback()
            result["errors"].append(f"Linha {row_number}: {raw_sku} - {exc}")
    return result


def create_template_files(base_dir):
    template_path = Path(base_dir) / "template_importacao_skus.xlsx"
    sample_path = Path(base_dir) / "dados_exemplo.xlsx"
    label_template_path = Path(base_dir) / "template_etiquetas_lote.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "SKUs"
    ws.append(SKU_IMPORT_COLUMNS)
    _style_header(ws)
    for width, column in zip([18, 48, 18, 18], "ABCD"):
        ws.column_dimensions[column].width = width
    wb.save(template_path)

    wb = Workbook()
    ws = wb.active
    ws.title = "SKUs"
    ws.append(["SKU", "DESCRICAO", "SALDO_ATUAL", "ESTOQUE_MINIMO", "UNIDADE", "CATEGORIA", "LOCALIZACAO"])
    examples = [
        ["PAR-0001", "Parafuso sextavado M8 x 30 zincado", 125, 50, "UN", "Fixadores", "A1-01"],
        ["CAB-0012", "Cabo eletrico flexivel 2,5 mm preto", 80, "", "M", "Eletrica", "B2-04"],
        ["CON-0100", "Conector pneumatico reto 1/4", 12, 20, "UN", "Pneumatica", "C3-02"],
    ]
    for row in examples:
        ws.append(row)
    _style_header(ws)
    for width, column in zip([18, 48, 18, 18, 14, 24, 24], "ABCDEFG"):
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

    return template_path, sample_path, label_template_path


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
    ws.append(["SKU", "Descricao", "Unidade", "Categoria", "Localizacao", "Saldo atual", "Estoque minimo", "Ativo", "Status"])

    query = db.query(SKU).outerjoin(StockBalance)
    sku_filter = filters.get("sku")
    desc_filter = filters.get("descricao")
    category = filters.get("categoria")
    location = filters.get("localizacao")
    active = filters.get("active")
    low = filters.get("saldo_baixo")

    if sku_filter:
        query = query.filter(SKU.sku.ilike(f"%{sku_filter}%"))
    if desc_filter:
        query = query.filter(SKU.descricao.ilike(f"%{desc_filter}%"))
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
    title = "Relatorio completo de movimentacoes" if not tipo else f"Relatorio de {tipo.lower()}"
    _metadata(ws, title, user, f"tipo={tipo}" if tipo else "Sem filtros")
    ws.append([
        "ID",
        "Data/Hora",
        "Usuario",
        "SKU",
        "Descricao",
        "Tipo",
        "Quantidade",
        "Saldo anterior",
        "Saldo posterior",
        "Documento",
        "Observacao",
    ])
    query = db.query(Movement).join(SKU)
    if tipo:
        query = query.filter(Movement.tipo == tipo)
    for mv in query.order_by(Movement.created_at.desc()).all():
        ws.append([
            mv.id,
            mv.created_at.strftime("%d/%m/%Y %H:%M:%S"),
            mv.usuario.username,
            mv.sku.sku,
            mv.sku.descricao,
            mv.tipo,
            decimal_to_str(mv.quantidade),
            decimal_to_str(mv.saldo_anterior),
            decimal_to_str(mv.saldo_posterior),
            mv.documento,
            mv.observacao,
        ])
    _autosize(ws)
    return _save_report(wb, f"relatorio_{tipo.lower() if tipo else 'movimentacoes'}")


def export_inventory_report(db, user, session_id=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"
    _metadata(ws, "Relatorio de inventario", user, f"sessao={session_id}" if session_id else "Todas as sessoes")
    ws.append([
        "Sessao",
        "Status",
        "SKU",
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
    ws.append(["SKU", "Descricao", "Saldo sistema", "Contagem", "Diferenca", "Status"])

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
