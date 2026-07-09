import hashlib
import os
from pathlib import Path

from config import DEFAULT_BOM_DIR, DEFAULT_SKUS_FILENAME
from services.excel_service import import_bom_from_files, import_skus_from_local_master
from services.estoque_service import get_setting, set_setting


SKUS_SOURCE_PATH_KEY = "local_skus_source_path"
SKUS_SOURCE_SIGNATURE_KEY = "local_skus_source_signature"
BOM_SOURCE_DIR_KEY = "local_bom_source_dir"
BOM_SOURCE_SIGNATURE_KEY = "local_bom_source_signature"
BOM_EXTENSIONS = {".xls", ".xlsx", ".xlsm", ".xltx", ".xltm"}


def _clean_path(value):
    return Path(os.path.expandvars(str(value or "").strip())).expanduser()


def get_skus_source_path(db):
    return _clean_path(get_setting(db, SKUS_SOURCE_PATH_KEY, str(DEFAULT_SKUS_FILENAME)))


def get_bom_source_dir(db):
    return _clean_path(get_setting(db, BOM_SOURCE_DIR_KEY, str(DEFAULT_BOM_DIR)))


def set_local_source_paths(db, skus_path, bom_dir):
    set_setting(db, SKUS_SOURCE_PATH_KEY, skus_path or str(DEFAULT_SKUS_FILENAME))
    set_setting(db, BOM_SOURCE_DIR_KEY, bom_dir or str(DEFAULT_BOM_DIR))


def _file_signature(path):
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


def _bom_files(source_dir):
    if not source_dir.exists() or not source_dir.is_dir():
        return []
    return sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in BOM_EXTENSIONS
        and not path.name.startswith("~$")
    )


def _directory_signature(source_dir, files):
    digest = hashlib.sha256()
    latest = 0
    total_size = 0
    for path in files:
        stat = path.stat()
        latest = max(latest, stat.st_mtime_ns)
        total_size += stat.st_size
        relative = path.relative_to(source_dir).as_posix().lower()
        digest.update(f"{relative}|{stat.st_size}|{stat.st_mtime_ns}\n".encode("utf-8"))
    return f"{source_dir.resolve()}|{len(files)}|{total_size}|{latest}|{digest.hexdigest()}"


def update_skus_from_local_file(db, force=False):
    path = get_skus_source_path(db)
    result = {
        "ok": False,
        "skipped": False,
        "source": str(path),
        "created": 0,
        "updated": 0,
        "balances_updated": 0,
        "status_updated": 0,
        "rows": 0,
        "duplicates_skipped": 0,
        "errors": [],
    }
    if not path.exists() or not path.is_file():
        result["skipped"] = True
        result["errors"].append("Arquivo Base CODs nao encontrado.")
        return result

    signature = _file_signature(path)
    if not force and get_setting(db, SKUS_SOURCE_SIGNATURE_KEY, "") == signature:
        result["ok"] = True
        result["skipped"] = True
        return result

    with path.open("rb") as file_obj:
        imported = import_skus_from_local_master(db, file_obj)
    result.update(imported)
    result["ok"] = not imported["errors"]
    if result["ok"]:
        set_setting(db, SKUS_SOURCE_SIGNATURE_KEY, signature)
    return result


def update_bom_from_local_dir(db, force=False):
    source_dir = get_bom_source_dir(db)
    files = _bom_files(source_dir)
    result = {
        "ok": False,
        "skipped": False,
        "source": str(source_dir),
        "processed": 0,
        "items": 0,
        "deleted": 0,
        "files": len(files),
        "errors": [],
    }
    if not source_dir.exists() or not source_dir.is_dir():
        result["skipped"] = True
        result["errors"].append("Pasta B.O.M local nao encontrada.")
        return result
    if not files:
        result["skipped"] = True
        result["errors"].append("Nenhuma planilha B.O.M encontrada na pasta local.")
        return result

    signature = _directory_signature(source_dir, files)
    if not force and get_setting(db, BOM_SOURCE_SIGNATURE_KEY, "") == signature:
        result["ok"] = True
        result["skipped"] = True
        return result

    imported = import_bom_from_files(db, files)
    result.update(imported)
    result["files"] = len(files)
    result["ok"] = not imported["errors"]
    if result["ok"]:
        set_setting(db, BOM_SOURCE_SIGNATURE_KEY, signature)
    return result
