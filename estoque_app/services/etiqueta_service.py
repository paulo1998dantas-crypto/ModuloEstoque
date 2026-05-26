from datetime import datetime
from pathlib import Path

from config import Config, EXPORTS_DIR
from models import LabelPrintJob, now_utc


def _safe_zpl_text(value):
    return str(value or "").replace("^", " ").replace("~", " ").replace("\r", " ").replace("\n", " ").strip()


def descricao_58(descricao):
    return _safe_zpl_text(descricao)[:58]


def render_label_zpl(sku, descricao):
    template_path = Path(Config.LABEL_TEMPLATE_PATH)
    if not template_path.exists():
        raise FileNotFoundError(f"Template ZPL nao encontrado: {template_path}")

    sku_text = _safe_zpl_text(sku)
    descricao_text = _safe_zpl_text(descricao)
    data_text = datetime.now().strftime("%d/%m/%Y")

    zpl = template_path.read_text(encoding="utf-8")
    replacements = {
        "{{SKU}}": sku_text,
        "{{DESCRICAO}}": descricao_text,
        "{{DESCRICAO_58}}": descricao_58(descricao_text),
        "{{DATA}}": data_text,
    }
    for placeholder, value in replacements.items():
        zpl = zpl.replace(placeholder, value)
    return zpl


def zpl_for_quantity(sku, descricao, quantidade):
    quantidade = max(int(quantidade or 1), 1)
    return "\n".join(render_label_zpl(sku, descricao) for _ in range(quantidade))


def save_zpl_file(zpl, prefix="etiqueta"):
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = EXPORTS_DIR / f"{prefix}_{stamp}.zpl"
    path.write_text(zpl, encoding="utf-8")
    return path


def print_zpl(zpl, printer_name=None):
    try:
        import win32print
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 nao esta instalado. Instale com: py -m pip install pywin32"
        ) from exc

    target_printer = printer_name or win32print.GetDefaultPrinter()
    if not target_printer:
        raise RuntimeError("Nenhuma impressora Zebra configurada.")

    handle = win32print.OpenPrinter(target_printer)
    try:
        job = win32print.StartDocPrinter(handle, 1, ("Etiqueta ZPL", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, zpl.encode("utf-8"))
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
        if not job.zpl_path:
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
