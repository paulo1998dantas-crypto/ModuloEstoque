import shutil
from datetime import datetime

from config import BACKUPS_DIR, DB_PATH


def create_backup():
    if not DB_PATH.exists():
        raise FileNotFoundError("Banco de dados ainda nao foi criado.")
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUPS_DIR / f"estoque_backup_{stamp}.db"
    shutil.copy2(DB_PATH, target)
    return target
