import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"


class MovementFiltersTest(unittest.TestCase):
    def test_filters_combine_and_survive_commitment_cancellation(self):
        script = textwrap.dedent(
            """
            from urllib.parse import parse_qs, urlsplit
            from uuid import uuid4

            from sqlalchemy import text

            import app as app_module
            from database import SessionLocal
            from models import SKU, User
            from services.estoque_service import register_movement

            flask_app = app_module.app
            flask_app.config.update(TESTING=True)
            database = SessionLocal()
            for statement in (
                "create table erp_vehicles (id text primary key, chassi text not null)",
                "create table erp_vehicle_entries (id text primary key, vehicle_id text not null, item_number integer not null)",
                "create table erp_work_orders (id text primary key, vehicle_entry_id text not null, numero_os text not null, status text not null, technical_status text default 'ABERTA')",
            ):
                database.execute(text(statement))

            admin = database.query(User).filter(User.role.in_(("ADM", "ADMIN"))).first()
            assert admin is not None
            sku_a = SKU(sku="FILTER-CODE-A", descricao="Material filtrado A", unidade="UN", active=True)
            sku_b = SKU(sku="FILTER-CODE-B", descricao="Material filtrado B", unidade="UN", active=True)
            database.add_all((sku_a, sku_b))
            database.commit()

            def create_work_order(number, chassis):
                vehicle_id = str(uuid4())
                entry_id = str(uuid4())
                work_order_id = uuid4().hex
                database.execute(
                    text("insert into erp_vehicles(id,chassi) values(:id,:chassi)"),
                    {"id": vehicle_id, "chassi": chassis},
                )
                database.execute(
                    text("insert into erp_vehicle_entries(id,vehicle_id,item_number) values(:id,:vehicle,:item)"),
                    {"id": entry_id, "vehicle": vehicle_id, "item": number},
                )
                database.execute(
                    text("insert into erp_work_orders(id,vehicle_entry_id,numero_os,status,technical_status) values(:id,:entry,:numero,'ATIVA','ABERTA')"),
                    {"id": work_order_id, "entry": entry_id, "numero": str(number)},
                )
                database.commit()
                return work_order_id

            work_order_a = create_work_order(3061, "9BMTESTE00003061")
            work_order_b = create_work_order(3062, "9BMTESTE00003062")
            register_movement(database, sku_a, "ENTRADA", 10, admin.id)
            register_movement(database, sku_b, "ENTRADA", 10, admin.id)
            commitment_a = register_movement(
                database, sku_a, "EMPENHO", 2, admin.id, work_order_id=work_order_a
            )
            commitment_b = register_movement(
                database, sku_b, "EMPENHO", 3, admin.id, work_order_id=work_order_b
            )
            admin_id = admin.id
            commitment_a_id = commitment_a.id
            commitment_b_id = commitment_b.id
            database.close()

            client = flask_app.test_client()
            with client.session_transaction() as session:
                session["user_id"] = admin_id

            response = client.get(
                "/movimentacoes",
                query_string={
                    "tipo": "EMPENHO",
                    "movement_id": f"{commitment_a_id}, {commitment_b_id}",
                    "sku": "FILTER-CODE-A",
                    "vinculo": "3061",
                    "status": "ATIVA",
                },
            )
            assert response.status_code == 200
            html = response.get_data(as_text=True)
            assert f'data-commitment-id="{commitment_a_id}"' in html
            assert f'data-commitment-id="{commitment_b_id}"' not in html
            assert "1 movimentação(ões) encontrada(s)" in html

            response = client.get(
                "/movimentacoes",
                query_string={
                    "tipo": "EMPENHO",
                    "movement_id": f"{commitment_a_id}, {commitment_b_id}",
                },
            )
            html = response.get_data(as_text=True)
            assert f'data-commitment-id="{commitment_a_id}"' in html
            assert f'data-commitment-id="{commitment_b_id}"' in html

            response = client.post(
                f"/movimentacoes/{commitment_a_id}/cancelar",
                data={
                    "reason": "Realocar para outro carro",
                    "tipo": "EMPENHO",
                    "movement_id": f"{commitment_a_id}, {commitment_b_id}",
                    "sku": "FILTER-CODE-A",
                    "vinculo": "3061",
                },
                follow_redirects=False,
            )
            assert response.status_code == 302
            query = parse_qs(urlsplit(response.headers["Location"]).query)
            assert query["tipo"] == ["EMPENHO"]
            assert query["movement_id"] == [f"{commitment_a_id}, {commitment_b_id}"]
            assert query["sku"] == ["FILTER-CODE-A"]
            assert query["vinculo"] == ["3061"]
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "movement-filters.sqlite"
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONPATH": str(APP_DIR),
                    "ESTOQUE_DATABASE_MODE": "online",
                    "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
                    "ERP_SHARED_RBAC_ENABLED": "false",
                    "ERP_MOVEMENT_CONTEXT_ENABLED": "true",
                    "ERP_PORTAL_SSO_ENABLED": "0",
                }
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=APP_DIR,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )

        self.assertEqual(
            0,
            result.returncode,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
