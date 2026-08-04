import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"


class CommitmentAdminRoutesTest(unittest.TestCase):
    def test_id_based_consumption_and_correction_are_admin_only(self):
        script = textwrap.dedent(
            """
            from io import BytesIO

            from openpyxl import Workbook

            import app as app_module
            from database import SessionLocal
            from models import Movement, SKU, User
            from services.estoque_service import register_movement

            flask_app = app_module.app
            flask_app.config.update(TESTING=True)

            database = SessionLocal()
            operator = User(
                username="route-operator",
                password_hash="hash",
                role="OPERADOR",
                active=True,
            )
            database.add(operator)
            sku = SKU(
                sku="ROUTE-ADMIN-ONLY",
                descricao="Material do teste de permissao",
                unidade="UN",
                active=True,
            )
            database.add(sku)
            database.commit()
            register_movement(database, sku, "ENTRADA", 10, operator.id)
            commitment = register_movement(
                database,
                sku,
                "EMPENHO",
                4,
                operator.id,
                documento="SETOR",
            )
            admin = database.query(User).filter(User.role.in_(("ADM", "ADMIN"))).first()
            assert admin is not None
            operator_id = operator.id
            admin_id = admin.id
            commitment_id = commitment.id
            database.close()

            def login_as(client, user_id):
                with client.session_transaction() as session:
                    session.clear()
                    session["user_id"] = user_id

            client = flask_app.test_client()
            login_as(client, operator_id)

            response = client.get("/empenhos/corrigir", follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/dashboard")

            response = client.post(
                f"/movimentacoes/{commitment_id}/baixar-empenho",
                data={"quantidade": "1", "tipo": "EMPENHO"},
                follow_redirects=False,
            )
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/dashboard")

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Empenhos pendentes"
            worksheet.append(["ID_EMPENHO", "EMPENHO"])
            worksheet.append([commitment_id, 1])
            payload = BytesIO()
            workbook.save(payload)
            payload.seek(0)
            response = client.post(
                "/baixa",
                data={
                    "action": "import_file",
                    "file": (payload, "empenhos_pendentes.xlsx"),
                },
                content_type="multipart/form-data",
                follow_redirects=False,
            )
            assert response.status_code == 302
            assert response.headers["Location"].endswith("/baixa")

            response = client.get("/movimentacoes?tipo=EMPENHO")
            assert response.status_code == 200
            assert b"data-commitment-consumption" not in response.data

            response = client.post(
                "/baixa",
                data={
                    "action": "manual_baixa",
                    "sku": "ROUTE-ADMIN-ONLY",
                    "quantidade": "1",
                    "setor": "PRODUCAO",
                },
                follow_redirects=False,
            )
            assert response.status_code == 302
            database = SessionLocal()
            assert database.query(Movement).filter_by(tipo="BAIXA").count() == 1
            database.close()

            login_as(client, admin_id)
            response = client.get("/empenhos/corrigir")
            assert response.status_code == 200
            response = client.get("/movimentacoes?tipo=EMPENHO")
            assert response.status_code == 200
            assert b"data-commitment-consumption" in response.data
            """
        )
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "routes.sqlite"
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
