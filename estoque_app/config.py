import os
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EXPORTS_DIR = BASE_DIR / "exports"
BACKUPS_DIR = BASE_DIR / "backups"
LOGS_DIR = BASE_DIR / "logs"
TEMPLATES_ZPL_DIR = BASE_DIR / "templates_zpl"
DB_PATH = DATA_DIR / "estoque.db"
LABEL_TEMPLATE_PATH = TEMPLATES_ZPL_DIR / "etiqueta_base.zpl"
BUNDLED_LABEL_TEMPLATE_PATH = APP_ROOT / "templates_zpl" / "etiqueta_base.zpl"

for directory in (DATA_DIR, EXPORTS_DIR, BACKUPS_DIR, LOGS_DIR, TEMPLATES_ZPL_DIR):
    directory.mkdir(parents=True, exist_ok=True)

if not LABEL_TEMPLATE_PATH.exists() and BUNDLED_LABEL_TEMPLATE_PATH.exists():
    shutil.copy2(BUNDLED_LABEL_TEMPLATE_PATH, LABEL_TEMPLATE_PATH)


def build_database_url():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        return f"sqlite:///{DB_PATH.as_posix()}"

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    if database_url.startswith("postgresql://") and "+psycopg" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    if database_url.startswith("postgresql+psycopg://"):
        parts = urlsplit(database_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("sslmode", "require")
        database_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    return database_url


class Config:
    SECRET_KEY = os.environ.get("ESTOQUE_SECRET_KEY", "troque-esta-chave-em-producao-local")
    SQLALCHEMY_DATABASE_URI = build_database_url()
    DEFAULT_ADMIN_USERNAME = os.environ.get("ESTOQUE_ADMIN_USER", "admin")
    DEFAULT_ADMIN_PASSWORD = os.environ.get("ESTOQUE_ADMIN_PASSWORD", "admin123")
    LABEL_TEMPLATE_PATH = LABEL_TEMPLATE_PATH
    DEFAULT_PRINTER_NAME = os.environ.get("ZEBRA_PRINTER_NAME", "")
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
