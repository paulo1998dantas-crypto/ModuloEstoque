import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


APP_DIR = Path(__file__).resolve().parents[1] / "estoque_app"
sys.path.insert(0, str(APP_DIR))

from auth import (  # noqa: E402
    PERMISSION_DEFINITIONS,
    ROLE_PERMISSION_MAP,
    can,
    effective_permissions,
    effective_roles,
    ensure_initial_data,
    record_auth_audit,
    sync_user_roles,
)
from config import Config  # noqa: E402
from database import Base  # noqa: E402
from models import (  # noqa: E402
    ErpAuthAuditEvent,
    ErpPermission,
    ErpRole,
    ErpRolePermission,
    ErpUserPermissionOverride,
    ErpUserRole,
    User,
)


class SharedRbacTest(unittest.TestCase):
    def setUp(self):
        self.previous_flag = os.environ.get("ERP_SHARED_RBAC_ENABLED")
        os.environ["ERP_SHARED_RBAC_ENABLED"] = "true"
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)
        self.db = self.Session()
        self.user = User(
            username="multi-perfil",
            password_hash="hash",
            role="OPERADOR",
            active=True,
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add_all(
            [
                ErpRole(code="OPERADOR", name="Operador", description=""),
                ErpRole(code="COMPRADOR", name="Comprador", description=""),
                ErpPermission(
                    code="estoque.entry.create",
                    module="ESTOQUE",
                    description="Entrada",
                ),
                ErpPermission(
                    code="suprimentos.purchase.create",
                    module="SUPRIMENTOS",
                    description="Compra",
                ),
                ErpRolePermission(
                    role_code="OPERADOR",
                    permission_code="estoque.entry.create",
                ),
                ErpRolePermission(
                    role_code="COMPRADOR",
                    permission_code="suprimentos.purchase.create",
                ),
                ErpUserRole(
                    user_id=self.user.id,
                    role_code="OPERADOR",
                ),
                ErpUserRole(
                    user_id=self.user.id,
                    role_code="COMPRADOR",
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if self.previous_flag is None:
            os.environ.pop("ERP_SHARED_RBAC_ENABLED", None)
        else:
            os.environ["ERP_SHARED_RBAC_ENABLED"] = self.previous_flag

    def test_multiple_roles_are_unioned(self):
        self.assertEqual(
            {"OPERADOR", "COMPRADOR"},
            effective_roles(self.user, self.db),
        )
        self.assertEqual(
            {"estoque.entry.create", "suprimentos.purchase.create"},
            effective_permissions(self.user, self.db),
        )
        self.assertTrue(can(self.user, "estoque.entry.create", self.db))
        self.assertTrue(can(self.user, "suprimentos.purchase.create", self.db))

    def test_commitment_reconciliation_permission_is_mapped_only_to_admin(self):
        permission = "estoque.commitment.reconcile_admin"
        self.assertIn(permission, PERMISSION_DEFINITIONS)
        self.assertIn(permission, ROLE_PERMISSION_MAP["ADMIN"])
        for role_code, permissions in ROLE_PERMISSION_MAP.items():
            if role_code != "ADMIN":
                self.assertNotIn(permission, permissions, role_code)

    def test_legacy_commitment_reconciliation_is_admin_only(self):
        permission = "estoque.commitment.reconcile_admin"
        with patch.dict(os.environ, {"ERP_SHARED_RBAC_ENABLED": "false"}):
            for role_code in (
                "OPERADOR",
                "COMPRADOR",
                "FINANCEIRO",
                "PCP",
                "ENGENHARIA",
            ):
                user = User(
                    username=f"legacy-{role_code.lower()}",
                    password_hash="hash",
                    role=role_code,
                    active=True,
                )
                self.assertFalse(can(user, permission), role_code)
            admin = User(
                username="legacy-admin",
                password_hash="hash",
                role="ADM",
                active=True,
            )
            self.assertTrue(can(admin, permission))

    def test_startup_seeds_commitment_reconciliation_only_for_admin(self):
        permission = "estoque.commitment.reconcile_admin"
        with patch("auth.SessionLocal", self.Session):
            ensure_initial_data()

        self.db.expire_all()
        self.assertIsNotNone(self.db.get(ErpPermission, permission))
        role_codes = {
            role_code
            for (role_code,) in self.db.query(ErpRolePermission.role_code)
            .filter(ErpRolePermission.permission_code == permission)
            .all()
        }
        self.assertEqual({"ADMIN"}, role_codes)

    def test_user_override_can_deny_and_allow(self):
        self.db.add_all(
            [
                ErpUserPermissionOverride(
                    user_id=self.user.id,
                    permission_code="estoque.entry.create",
                    allowed=False,
                    reason="Restricao individual",
                ),
                ErpUserPermissionOverride(
                    user_id=self.user.id,
                    permission_code="suprimentos.purchase.create",
                    allowed=True,
                    reason="Confirmacao individual",
                ),
            ]
        )
        self.db.commit()

        self.assertFalse(can(self.user, "estoque.entry.create", self.db))
        self.assertTrue(can(self.user, "suprimentos.purchase.create", self.db))

    def test_inactive_user_has_no_permissions(self):
        self.user.active = False
        self.db.commit()

        self.assertEqual(set(), effective_permissions(self.user, self.db))
        self.assertFalse(can(self.user, "estoque.entry.create", self.db))

    def test_explicit_shared_mode_does_not_fallback_when_schema_is_unavailable(self):
        with patch("auth.rbac_schema_ready", return_value=False):
            self.assertEqual(set(), effective_roles(self.user, self.db))
            self.assertEqual(set(), effective_permissions(self.user, self.db))
            self.assertFalse(can(self.user, "estoque.entry.create", self.db))

    def test_legacy_role_is_used_only_while_shared_flag_is_disabled(self):
        with patch.dict(os.environ, {"ERP_SHARED_RBAC_ENABLED": "false"}):
            self.assertEqual({"OPERADOR"}, effective_roles(self.user, self.db))
            self.assertTrue(can(self.user, "estoque.entry.create", self.db))

    def test_inactive_role_does_not_grant_permissions_or_fallback_to_legacy(self):
        inactive_role = ErpRole(
            code="FINANCEIRO",
            name="Financeiro",
            description="",
            active=False,
        )
        permission = ErpPermission(
            code="suprimentos.purchase.financial_close",
            module="SUPRIMENTOS",
            description="Conclusao financeira",
        )
        user = User(
            username="perfil-inativo",
            password_hash="hash",
            role="FINANCEIRO",
            active=True,
        )
        self.db.add_all([inactive_role, permission, user])
        self.db.flush()
        self.db.add_all(
            [
                ErpRolePermission(
                    role_code="FINANCEIRO",
                    permission_code=permission.code,
                ),
                ErpUserRole(user_id=user.id, role_code="FINANCEIRO"),
            ]
        )
        self.db.commit()

        self.assertEqual(set(), effective_roles(user, self.db))
        self.assertEqual(set(), effective_permissions(user, self.db))
        self.assertFalse(can(user, permission.code, self.db))

    def test_role_sync_preserves_admin_membership_that_remains_selected(self):
        admin = User(
            username="admin-delta",
            password_hash="hash",
            role="ADM",
            active=True,
        )
        self.db.add(admin)
        self.db.add(ErpRole(code="ADMIN", name="Administrador", description=""))
        self.db.flush()
        membership = ErpUserRole(user_id=admin.id, role_code="ADMIN")
        self.db.add(membership)
        self.db.commit()

        sync_user_roles(
            self.db,
            admin.id,
            {"ADMIN", "OPERADOR"},
            assigned_by=admin.id,
        )
        self.db.commit()

        preserved = self.db.get(
            ErpUserRole,
            {"user_id": admin.id, "role_code": "ADMIN"},
        )
        self.assertIs(membership, preserved)
        self.assertEqual(
            {"ADMIN", "OPERADOR"},
            {
                role
                for (role,) in self.db.query(ErpUserRole.role_code)
                .filter(ErpUserRole.user_id == admin.id)
                .all()
            },
        )

    def test_auth_audit_records_only_safe_change_metadata(self):
        event = record_auth_audit(
            self.db,
            user_id=self.user.id,
            actor_user_id=self.user.id,
            action="USER_UPDATED",
            before_data={
                "username": "antes",
                "roles": ["OPERADOR"],
                "password_changed": False,
            },
            after_data={
                "username": "depois",
                "roles": ["OPERADOR", "COMPRADOR"],
                "password_changed": True,
            },
        )
        self.db.commit()

        stored = self.db.get(ErpAuthAuditEvent, event.id)
        self.assertTrue(stored.after_data["password_changed"])
        self.assertNotIn("password", stored.after_data)
        self.assertNotIn("password_hash", stored.after_data)

    def test_startup_does_not_reactivate_or_repromote_existing_default_user(self):
        default_user = User(
            username=Config.DEFAULT_ADMIN_USERNAME,
            password_hash="senha-operacional-preservada",
            role="OPERADOR",
            active=False,
        )
        active_admin = User(
            username="admin-ativo",
            password_hash="hash",
            role="ADM",
            active=True,
        )
        self.db.add_all([default_user, active_admin])
        if self.db.get(ErpRole, "ADMIN") is None:
            self.db.add(ErpRole(code="ADMIN", name="Administrador", description=""))
        self.db.flush()
        self.db.add(ErpUserRole(user_id=active_admin.id, role_code="ADMIN"))
        self.db.commit()

        with patch("auth.SessionLocal", self.Session):
            ensure_initial_data()

        self.db.expire_all()
        preserved = self.db.get(User, default_user.id)
        self.assertFalse(preserved.active)
        self.assertEqual("OPERADOR", preserved.role)
        self.assertEqual("senha-operacional-preservada", preserved.password_hash)
        self.assertIsNone(
            self.db.get(
                ErpUserRole,
                {"user_id": preserved.id, "role_code": "ADMIN"},
            )
        )

    def test_startup_preserves_an_inactive_role(self):
        role = self.db.get(ErpRole, "OPERADOR")
        role.active = False
        self.db.commit()

        with patch("auth.SessionLocal", self.Session):
            ensure_initial_data()

        self.db.expire_all()
        self.assertFalse(self.db.get(ErpRole, "OPERADOR").active)


if __name__ == "__main__":
    unittest.main()
