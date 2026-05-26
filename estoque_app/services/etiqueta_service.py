from datetime import datetime
from pathlib import Path

from config import Config, EXPORTS_DIR
from models import LabelPrintJob, now_utc


def _safe_zpl_text(value):
    return str(value or "").replace("^", " ").replace("~", " ").replace("\r", " ").replace("\n", " ").strip()


def descricao_58(descricao):
    return _safe_zpl_text(descricao)[:58]


def description_layout(descricao):
    text = descricao_58(descricao)
    length = len(text)
    if length <= 24:
        return text, "42", "40", "1", "38"
    if length <= 42:
        return text, "34", "32", "2", "30"
    return text, "28", "27", "2", "28"


def render_label_zpl(sku, descricao, quantidade=1):
    template_path = Path(Config.LABEL_TEMPLATE_PATH)
    if not template_path.exists():
        raise FileNotFoundError(f"Template ZPL nao encontrado: {template_path}")

    sku_text = _safe_zpl_text(sku)
    descricao_text = _safe_zpl_text(descricao)
    descricao_label, desc_font_h, desc_font_w, desc_lines, desc_y = description_layout(descricao_text)
    data_text = datetime.now().strftime("%d/%m/%Y")
    quantidade_text = str(max(int(quantidade or 1), 1))

    zpl = template_path.read_text(encoding="utf-8")
    replacements = {
        "{{SKU}}": sku_text,
        "{{DESCRICAO}}": descricao_text,
        "{{DESCRICAO_58}}": descricao_label,
        "{{DESC_FONT_H}}": desc_font_h,
        "{{DESC_FONT_W}}": desc_font_w,
        "{{DESC_LINES}}": desc_lines,
        "{{DESC_Y}}": desc_y,
        "{{DATA}}": data_text,
        "{{QTD}}": quantidade_text,
        "~LBL_QTY,4~": quantidade_text,
        "~LBL_QTY": quantidade_text,
    }
    for placeholder, value in replacements.items():
        zpl = zpl.replace(placeholder, value)
    return zpl.lstrip("\ufeff")


def zpl_for_quantity(sku, descricao, quantidade):
    quantidade = max(int(quantidade or 1), 1)
    return render_label_zpl(sku, descricao, quantidade=quantidade)


def save_zpl_file(zpl, prefix="etiqueta"):
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = EXPORTS_DIR / f"{prefix}_{stamp}.zpl"
    path.write_text(zpl, encoding="utf-8")
    return path


def _printer_names(win32print):
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [printer[2] for printer in win32print.EnumPrinters(flags)]


def _printer_status(win32print, printer_name):
    handle = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(handle, 2)
        return int(info.get("Status") or 0)
    finally:
        win32print.ClosePrinter(handle)


def _printer_is_ready(win32print, printer_name):
    blocking_status = 0x00000001 | 0x00000002 | 0x00000008 | 0x00000010 | 0x00000020 | 0x00000080
    return (_printer_status(win32print, printer_name) & blocking_status) == 0


def _zebra_candidates(printer_names):
    return [name for name in printer_names if "zebra" in name.lower() or "zdesigner" in name.lower()]


def _first_ready_zebra(win32print, printer_names):
    candidates = _zebra_candidates(printer_names)
    ordered = [name for name in candidates if "(EPL)" not in name.upper()]
    ordered.extend(name for name in candidates if name not in ordered)
    for name in ordered:
        try:
            if _printer_is_ready(win32print, name):
                return name
        except Exception:
            continue
    return None


def _resolve_zpl_printer(win32print, printer_name=None):
    explicit_printer = (printer_name or "").strip()
    target_printer = explicit_printer or win32print.GetDefaultPrinter()
    if not target_printer:
        raise RuntimeError("Nenhuma impressora Zebra configurada.")

    available = _printer_names(win32print)
    if "(EPL)" in target_printer.upper():
        preferred = target_printer.replace(" (EPL)", "").replace("(EPL)", "").strip()
        for name in available:
            if name.lower() == preferred.lower() and _printer_is_ready(win32print, name):
                return name
        return target_printer

    try:
        if not _printer_is_ready(win32print, target_printer):
            ready_zebra = _first_ready_zebra(win32print, available)
            if ready_zebra:
                return ready_zebra
    except Exception:
        pass

    if not explicit_printer and "zebra" not in target_printer.lower() and "zdesigner" not in target_printer.lower():
        ready_zebra = _first_ready_zebra(win32print, available)
        if ready_zebra:
            return ready_zebra

    return target_printer


def print_zpl(zpl, printer_name=None):
    try:
        import win32print
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 nao esta instalado. Instale com: py -m pip install pywin32"
        ) from exc

    target_printer = _resolve_zpl_printer(win32print, printer_name)
    if not _printer_is_ready(win32print, target_printer):
        raise RuntimeError(f"A fila da impressora '{target_printer}' esta em erro/offline no Windows. Limpe a fila ou reconecte a Zebra.")

    handle = win32print.OpenPrinter(target_printer)
    try:
        job = win32print.StartDocPrinter(handle, 1, ("Etiqueta ZPL", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            zpl = zpl.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
            payload = zpl.encode("cp1252", errors="replace")
            written = win32print.WritePrinter(handle, payload)
            if written is not None and written != len(payload):
                raise RuntimeError(f"Falha no envio RAW para a Zebra: {written} de {len(payload)} bytes enviados.")
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)
    return target_printer


def create_label_job(db, sku, quantidade, origem, usuario_id, inventory_session_id=None):
    job = LabelPrintJob(
        sku_id=sku.id,
        quantidade=max(int(quantidade or 1), 1),
        status="PENDENTE",
        origem=origem,
        usuario_id=usuario_id,
        inventory_session_id=inventory_session_id,
    )
    db.add(job)
    db.commit()
    return job


def prepare_label_job_file(db, job):
    zpl = zpl_for_quantity(job.sku.sku, job.sku.descricao, job.quantidade)
    path = save_zpl_file(zpl, prefix=f"job_{job.id}_{job.sku.sku}")
    job.zpl_path = str(path)
    job.erro = None
    db.commit()
    return path


def print_label_job(db, job, printer_name=None):
    try:
        prepare_label_job_file(db, job)
        zpl = Path(job.zpl_path).read_text(encoding="utf-8")
        print_zpl(zpl, printer_name=printer_name)
        job.status = "IMPRESSO"
        job.erro = None
        job.printed_at = now_utc()
        db.commit()
        return job
    except Exception as exc:
        job.status = "ERRO"
        job.erro = str(exc)
        db.commit()
        raise
