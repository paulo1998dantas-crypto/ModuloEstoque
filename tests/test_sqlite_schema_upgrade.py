import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

import database  # noqa: E402
from config import Config  # noqa: E402


class SqliteSchemaUpgradeTest(unittest.TestCase):
    def test_legacy_database_is_upgraded_without_recreating_operational_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.sqlite3"
            engine = create_engine(f"sqlite:///{db_path}", future=True)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        create table users (
                            id integer primary key,
                            username varchar(80) not null unique,
                            password_hash varchar(255) not null,
                            role varchar(20) not null,
                            active boolean not null,
                            created_at datetime not null
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        create table skus (
                            id integer primary key,
                            sku varchar(80) not null unique,
                            descricao varchar(255) not null,
                            unidade varchar(20),
                            grupo varchar(120),
                            categoria varchar(120),
                            localizacao varchar(120),
                            estoque_minimo numeric(14,3),
                            active boolean not null,
                            created_at datetime not null,
                            updated_at datetime not null
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        create table movements (
                            id integer primary key,
                            sku_id integer not null,
                            tipo varchar(20) not null,
                            quantidade numeric(14,3) not null,
                            saldo_anterior numeric(14,3) not null,
                            saldo_posterior numeric(14,3) not null,
                            usuario_id integer not null,
                            documento varchar(120),
                            observacao text,
                            created_at datetime not null
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        insert into users
                            (id,username,password_hash,role,active,created_at)
                        values (1,'legacy','hash','OPERADOR',1,current_timestamp)
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        insert into skus
                            (id,sku,descricao,active,created_at,updated_at)
                        values (1,'LEG-001','Item legado',1,current_timestamp,current_timestamp)
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        insert into movements
                            (id,sku_id,tipo,quantidade,saldo_anterior,
                             saldo_posterior,usuario_id,created_at)
                        values (10,1,'ENTRADA',2,0,2,1,current_timestamp)
                        """
                    )
                )

            with (
                patch.object(database, "engine", engine),
                patch.object(
                    Config,
                    "SQLALCHEMY_DATABASE_URI",
                    f"sqlite:///{db_path}",
                ),
            ):
                database.init_db()
                database.init_db()

            inspector = inspect(engine)
            movement_columns = {
                column["name"] for column in inspector.get_columns("movements")
            }
            self.assertTrue(
                {
                    "source_type",
                    "source_id",
                    "source_line_id",
                    "idempotency_key",
                    "work_order_id",
                    "context_kind",
                    "setor",
                    "reference_text",
                    "link_updated_at",
                    "link_updated_by",
                    "movement_status",
                    "canceled_at",
                    "canceled_by",
                    "cancel_reason",
                    "reversal_movement_id",
                    "operation_id",
                    "parent_movement_id",
                }.issubset(movement_columns)
            )
            self.assertIn(
                "auth_version",
                {column["name"] for column in inspector.get_columns("users")},
            )
            indexes = {
                index["name"]: index
                for index in inspector.get_indexes("movements")
            }
            self.assertIn("ix_movements_work_order_id", indexes)
            self.assertIn("ix_movements_operation_id", indexes)
            self.assertTrue(indexes["ix_movements_idempotency_key"]["unique"])
            with engine.connect() as connection:
                legacy = connection.execute(
                    text(
                        "select context_kind,movement_status "
                        "from movements where id=10"
                    )
                ).one()
                self.assertEqual(("LEGACY", "ATIVA"), tuple(legacy))
                self.assertEqual(
                    1,
                    connection.execute(
                        text("select count(*) from movements")
                    ).scalar_one(),
                )
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
