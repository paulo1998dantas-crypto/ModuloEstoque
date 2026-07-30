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
        movement_additions = {
            "related_movement_id": "INTEGER",
            "source_type": "VARCHAR(40)",
            "source_id": "CHAR(32)",
            "source_line_id": "CHAR(32)",
            "idempotency_key": "VARCHAR(160)",
            "work_order_id": "CHAR(32)",
            "context_kind": "VARCHAR(20)",
            "setor": "VARCHAR(120)",
            "reference_text": "VARCHAR(255)",
            "link_updated_at": "DATETIME",
            "link_updated_by": "INTEGER",
            "movement_status": "VARCHAR(20) NOT NULL DEFAULT 'ATIVA'",
            "canceled_at": "DATETIME",
            "canceled_by": "INTEGER",
            "cancel_reason": "TEXT",
            "reversal_movement_id": "INTEGER",
            "operation_id": "CHAR(32)",
            "parent_movement_id": "INTEGER",
        }
        for column_name, column_type in movement_additions.items():
            if column_name not in movement_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE movements ADD COLUMN "
                        f"{column_name} {column_type}"
                    )
                )
        user_columns = {
            row[1]: row
            for row in connection.execute(text("PRAGMA table_info(users)"))
        }
        if "auth_version" not in user_columns:
            connection.execute(
                text(
                    "ALTER TABLE users ADD COLUMN "
                    "auth_version INTEGER NOT NULL DEFAULT 1"
                )
            )
        connection.execute(
            text(
                "UPDATE movements SET context_kind='LEGACY' "
                "WHERE context_kind IS NULL OR trim(context_kind)=''"
            )
        )
        connection.execute(
            text(
                "UPDATE movements SET movement_status='ATIVA' "
                "WHERE movement_status IS NULL OR trim(movement_status)=''"
            )
        )
        movement_indexes = (
            "CREATE INDEX IF NOT EXISTS ix_movements_related_movement_id "
            "ON movements (related_movement_id)",
            "CREATE INDEX IF NOT EXISTS ix_movements_source_type "
            "ON movements (source_type)",
            "CREATE INDEX IF NOT EXISTS ix_movements_source_id "
            "ON movements (source_id)",
            "CREATE INDEX IF NOT EXISTS ix_movements_source_line_id "
            "ON movements (source_line_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_movements_idempotency_key "
            "ON movements (idempotency_key)",
            "CREATE INDEX IF NOT EXISTS ix_movements_work_order_id "
            "ON movements (work_order_id)",
            "CREATE INDEX IF NOT EXISTS ix_movements_movement_status "
            "ON movements (movement_status)",
            "CREATE INDEX IF NOT EXISTS ix_movements_operation_id "
            "ON movements (operation_id)",
            "CREATE INDEX IF NOT EXISTS ix_movements_parent_movement_id "
            "ON movements (parent_movement_id)",
        )
        for statement in movement_indexes:
            connection.execute(text(statement))
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
