from contextlib import contextmanager
from urllib.parse import urlsplit

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
from sqlalchemy.pool import NullPool

from config import Config


engine_kwargs = {
    "future": True,
    "pool_pre_ping": True,
}

if Config.SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["connect_args"] = {"prepare_threshold": None}
    db_port = urlsplit(Config.SQLALCHEMY_DATABASE_URI).port
    if db_port == 6543:
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 5
        engine_kwargs["pool_recycle"] = 1800

engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, **engine_kwargs)
SessionLocal = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
)
Base = declarative_base()


def init_db():
    import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_sku_schema()


def migrate_sku_schema():
    if not Config.SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        with engine.begin() as connection:
            if "grupo" not in _table_columns(connection, "skus"):
                connection.execute(text("ALTER TABLE skus ADD COLUMN grupo VARCHAR(120)"))
            if "related_movement_id" not in _table_columns(connection, "movements"):
                connection.execute(text("ALTER TABLE movements ADD COLUMN related_movement_id INTEGER"))
        return

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        skus_columns = {
            row[1]: row
            for row in connection.execute(text("PRAGMA table_info(skus)"))
        }
        if "grupo" not in skus_columns:
            connection.execute(text("ALTER TABLE skus ADD COLUMN grupo VARCHAR(120)"))
            skus_columns = {
                row[1]: row
                for row in connection.execute(text("PRAGMA table_info(skus)"))
            }
        movement_columns = {
            row[1]: row
            for row in connection.execute(text("PRAGMA table_info(movements)"))
        }
        if "related_movement_id" not in movement_columns:
            connection.execute(text("ALTER TABLE movements ADD COLUMN related_movement_id INTEGER"))
        estoque_minimo = skus_columns.get("estoque_minimo")
        if not estoque_minimo or not estoque_minimo[3]:
            return
        try:
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            connection.execute(text("DROP TABLE IF EXISTS skus_new"))
            connection.execute(text("""
                CREATE TABLE skus_new (
                    id INTEGER NOT NULL,
                    sku VARCHAR(80) NOT NULL,
                    descricao VARCHAR(255) NOT NULL,
                    unidade VARCHAR(20),
                    grupo VARCHAR(120),
                    categoria VARCHAR(120),
                    localizacao VARCHAR(120),
                    estoque_minimo NUMERIC(14, 3),
                    active BOOLEAN NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (id)
                )
            """))
            connection.execute(text("""
                INSERT INTO skus_new (
                    id, sku, descricao, unidade, grupo, categoria, localizacao,
                    estoque_minimo, active, created_at, updated_at
                )
                SELECT
                    id, sku, descricao, unidade, grupo, categoria, localizacao,
                    estoque_minimo, active, created_at, updated_at
                FROM skus
            """))
            connection.execute(text("DROP TABLE skus"))
            connection.execute(text("ALTER TABLE skus_new RENAME TO skus"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_skus_sku ON skus (sku)"))
        finally:
            connection.execute(text("PRAGMA foreign_keys=ON"))


def _table_columns(connection, table_name):
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
