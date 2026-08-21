import os
import time
from functools import wraps

from flask import flash, g, has_request_context, jsonify, redirect, request, session, url_for
from sqlalchemy import inspect
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from database import SessionLocal
from models import (
    AppSetting,
    ErpAuthAuditEvent,
    ErpPermission,
    ErpRole,
    ErpRolePermission,
    ErpUserPermissionOverride,
    ErpUserRole,
    User,
)
from portal_sso import enabled as portal_sso_enabled, portal_login_url


DEFAULT_SETTINGS = {
    "allow_negative_stock": "false",
    "operator_can_export": "true",
    "default_printer_name": Config.DEFAULT_PRINTER_NAME,
    "admin_can_print_inactive_sku": "false",
}

ADMIN_BOOTSTRAP_FLAG = "admin_default_password_seeded"

# A checagem do contrato RBAC usa o inspector do SQLAlchemy e consulta vários
# metadados do PostgreSQL. O esquema só muda durante migrations controladas;
# manter o resultado por poucos segundos evita repetir esse custo em cada menu,
# sem armazenar dados operacionais ou permissões de usuários.
_RBAC_SCHEMA_CACHE = {"checked_at": 0.0, "value": False}
_RBAC_SCHEMA_CACHE_TTL_SECONDS = max(
    1, int(os.environ.get("ERP_RBAC_SCHEMA_CACHE_TTL_SECONDS", "30"))
)

ROLE_DEFINITIONS = {
    "ADMIN": "Administrador",
    "OPERADOR": "Operador",
    "COMPRADOR": "Comprador",
    "FINANCEIRO": "Financeiro",
    "PCP": "PCP",
    "ENGENHARIA": "Engenharia",
    "PRODUCAO": "Produção",
}

PERMISSION_DEFINITIONS = {
    "estoque.dashboard.view": ("estoque", "Consultar dashboard do estoque"),
    "estoque.entry.create": ("estoque", "Registrar entrada manual"),
    "estoque.inspection.receive": ("estoque", "Inspecionar recebimento de O.C."),
    "estoque.commitment.create": ("estoque", "Criar empenhos"),
    "estoque.commitment.reconcile_admin": (
        "estoque",
        "Baixar por ID ou corrigir empenhos",
    ),
    "estoque.consumption.create": ("estoque", "Registrar baixas"),
    "estoque.labels.use": ("estoque", "Gerar e imprimir etiquetas"),
    "estoque.stock.view": ("estoque", "Consultar estoque"),
    "estoque.movement.view": ("estoque", "Consultar movimentacoes"),
    "estoque.movement.cancel_own": ("estoque", "Cancelar movimentacoes proprias"),
    "estoque.movement.cancel_any": ("estoque", "Cancelar qualquer movimentacao"),
    "estoque.reports.view": ("estoque", "Consultar e exportar relatorios"),
    "estoque.skus.view": ("estoque", "Consultar CODs"),
    "estoque.skus.manage": ("estoque", "Administrar CODs"),
    "estoque.inventory.manage": ("estoque", "Administrar inventario"),
    "estoque.import": ("estoque", "Executar importacoes operacionais"),
    "estoque.settings.manage": ("estoque", "Administrar configuracoes"),
    "estoque.users.manage": ("estoque", "Administrar usuarios e acessos"),
    "suprimentos.dashboard.view": ("suprimentos", "Consultar dashboard de suprimentos"),
    "suprimentos.purchase.view": ("suprimentos", "Consultar compras"),
    "suprimentos.purchase.create": ("suprimentos", "Criar ordens de compra"),
    "suprimentos.purchase.edit": ("suprimentos", "Editar ordens de compra"),
    "suprimentos.purchase.cancel": ("suprimentos", "Cancelar ordens de compra"),
    "suprimentos.purchase.technical_close": ("suprimentos", "Concluir compra tecnicamente"),
    "suprimentos.purchase.financial_close": ("suprimentos", "Concluir compra financeiramente"),
    "suprimentos.purchase.export": ("suprimentos", "Exportar relatorios de compras"),
    "suprimentos.purchase.bulk_manage": ("suprimentos", "Executar operacoes em lote"),
    "suprimentos.work_order.view": ("suprimentos", "Consultar O.S."),
    "suprimentos.work_order.manage": ("suprimentos", "Administrar O.S."),
    "suprimentos.work_order.schedule": ("suprimentos", "Programar e reprogramar O.S."),
    "suprimentos.work_order.technical_close": ("suprimentos", "Concluir ou reabrir O.S."),
    "suprimentos.work_order.import": ("suprimentos", "Importar e reconciliar O.S."),
    "suprimentos.production_order.view": ("suprimentos", "Consultar ordens de producao"),
    "suprimentos.production_order.manage": ("suprimentos", "Criar e administrar ordens de producao"),
    "suprimentos.production_order.execute": ("suprimentos", "Empenhar e concluir ordens de producao"),
    "suprimentos.master_data.manage": ("suprimentos", "Administrar dados mestres auxiliares"),
    "suprimentos.system.admin": ("suprimentos", "Administrar o modulo"),
    "mes.dashboard.read": ("mes", "Consultar visoes do MES"),
    "mes.stage.write": ("mes", "Apontar etapas do MES"),
    "mes.exports.read": ("mes", "Exportar controle, logs e tempos"),
    "mes.work_orders.manage": ("mes", "Administrar O.S. no MES"),
    "mes.vehicle_entries.create": ("mes", "Registrar entrada de veiculo"),
    "mes.schedule.manage": ("mes", "Programar e reprogramar producao"),
    "mes.finalize": ("mes", "Finalizar e entregar O.S."),
    "mes.legacy.import": ("mes", "Importar dados legados no MES"),
    "mes.users.manage": ("mes", "Administrar usuarios no MES"),
    "cadastro.access": ("cadastro", "Acessar modulo Cadastro"),
}

_STOCK_READ = {
    "estoque.dashboard.view",
    "estoque.stock.view",
    "estoque.movement.view",
    "estoque.reports.view",
    "estoque.skus.view",
}
_MES_OPERATOR = {
    "mes.dashboard.read",
    "mes.stage.write",
    "mes.exports.read",
}
ROLE_PERMISSION_MAP = {
    "PRODUCAO": {
        "mes.dashboard.read",
        "mes.stage.write",
    },
    "OPERADOR": _STOCK_READ
    | {
        "estoque.entry.create",
        "estoque.inspection.receive",
        "estoque.commitment.create",
        "estoque.consumption.create",
        "estoque.labels.use",
        "estoque.movement.cancel_own",
        "suprimentos.dashboard.view",
        "suprimentos.purchase.view",
        "suprimentos.work_order.view",
        "suprimentos.production_order.view",
        "suprimentos.production_order.execute",
    }
    | _MES_OPERATOR,
    "COMPRADOR": _STOCK_READ
    | {
        "suprimentos.dashboard.view",
        "suprimentos.purchase.view",
        "suprimentos.purchase.create",
        "suprimentos.purchase.edit",
        "suprimentos.purchase.cancel",
        "suprimentos.purchase.export",
        "suprimentos.work_order.view",
        "suprimentos.production_order.view",
    }
    | _MES_OPERATOR,
    "FINANCEIRO": _STOCK_READ
    | {
        "suprimentos.dashboard.view",
        "suprimentos.purchase.view",
        "suprimentos.purchase.financial_close",
        "suprimentos.purchase.export",
        "suprimentos.work_order.view",
        "suprimentos.production_order.view",
    },
    "PCP": set(PERMISSION_DEFINITIONS)
    - {
        "estoque.users.manage",
        "estoque.settings.manage",
        "estoque.commitment.reconcile_admin",
        "mes.legacy.import",
        "mes.users.manage",
        "suprimentos.master_data.manage",
        "suprimentos.system.admin",
    },
    "ENGENHARIA": (
        set(PERMISSION_DEFINITIONS)
        - {
            "estoque.users.manage",
            "estoque.settings.manage",
            "estoque.commitment.reconcile_admin",
            "mes.legacy.import",
            "mes.users.manage",
            "suprimentos.master_data.manage",
            "suprimentos.system.admin",
        }
    )
    | {"cadastro.access"},
    "ADMIN": set(PERMISSION_DEFINITIONS),
}
LEGACY_ADMIN_ONLY_PERMISSIONS = {
    "estoque.commitment.reconcile_admin",
    "estoque.movement.cancel_any",
    "estoque.skus.manage",
    "estoque.inventory.manage",
    "estoque.import",
    "estoque.settings.manage",
    "estoque.users.manage",
    "suprimentos.purchase.cancel",
    "suprimentos.purchase.technical_close",
}


def shared_rbac_enabled():
    return os.environ.get("ERP_SHARED_RBAC_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "sim",
        "on",
    }


def canonical_role(role):
    value = str(role or "").strip().upper()
    return "ADMIN" if value == "ADM" else value


def _rbac_tables_available(db):
    return shared_rbac_enabled() and rbac_schema_ready(db)


def rbac_schema_ready(db=None):
    """Return whether the complete shared-RBAC contract is installed.

    This helper deliberately does not inspect the feature flag.  User
    administration uses it during the deployment window so every newly-created
    account receives a membership as soon as the additive schema exists, even
    while authorization is still running in legacy mode.
    """
    now = time.monotonic()
    cached = _RBAC_SCHEMA_CACHE
    if now - cached["checked_at"] < _RBAC_SCHEMA_CACHE_TTL_SECONDS:
        return bool(cached["value"])

    database = db or SessionLocal()
    close_database = db is None
    try:
        available = _rbac_tables_exist(database)
        _RBAC_SCHEMA_CACHE.update(checked_at=now, value=available)
        return available
    finally:
        if close_database:
            database.close()


def _rbac_tables_exist(db):
    inspector = inspect(db.get_bind())
    required_columns = {
        "users": {"id", "role", "active", "auth_version"},
        "erp_roles": {"code", "active"},
        "erp_permissions": {"code"},
        "erp_role_permissions": {"role_code", "permission_code"},
        "erp_user_roles": {"user_id", "role_code"},
        "erp_user_permission_overrides": {
            "user_id",
            "permission_code",
            "allowed",
        },
    }
    for table_name, columns in required_columns.items():
        if not inspector.has_table(table_name):
            return False
        available = {
            column["name"]
            for column in inspector.get_columns(table_name)
        }
        if not columns.issubset(available):
            return False
    return True


def sync_user_roles(db, user_id, role_codes, assigned_by=None):
    """Apply only the membership delta, preserving roles that remain selected.

    Besides reducing writes, this is required by the database guard that prevents
    the last active ADMIN membership from being deleted, even momentarily.
    """
    if not rbac_schema_ready(db):
        raise RuntimeError(
            "Schema RBAC compartilhado incompleto; os perfis nao foram salvos."
        )
    requested = {
        canonical_role(role_code)
        for role_code in role_codes
        if canonical_role(role_code) in ROLE_DEFINITIONS
    }
    existing_rows = {
        row.role_code: row
        for row in db.query(ErpUserRole)
        .filter(ErpUserRole.user_id == user_id)
        .all()
    }
    for role_code, row in existing_rows.items():
        if role_code not in requested:
            db.delete(row)
    for role_code in requested - set(existing_rows):
        db.add(
            ErpUserRole(
                user_id=user_id,
                role_code=role_code,
                assigned_by=assigned_by,
            )
        )
    db.flush()


def record_auth_audit(
    db,
    *,
    user_id,
    actor_user_id,
    action,
    before_data=None,
    after_data=None,
    reason="",
):
    """Record access-management changes without persisting credentials."""
    event = ErpAuthAuditEvent(
        user_id=user_id,
        actor_user_id=actor_user_id,
        action=str(action or "").strip().upper(),
        before_data=dict(before_data or {}),
        after_data=dict(after_data or {}),
        reason=str(reason or "").strip(),
        origin_app="ESTOQUE",
    )
    db.add(event)
    db.flush()
    return event


def effective_roles(user, db=None):
    if not user:
        return set()
    cache_key = f"erp_effective_roles_{user.id}"
    if has_request_context() and hasattr(g, cache_key):
        return getattr(g, cache_key)
    database = db or SessionLocal()
    if shared_rbac_enabled():
        # Shared mode is intentionally fail-closed.  The legacy users.role
        # column is only authoritative while the feature flag is disabled.
        if not rbac_schema_ready(database):
            roles = set()
            if has_request_context():
                setattr(g, cache_key, roles)
            return roles
        roles = {
            canonical_role(role_code)
            for (role_code,) in database.query(ErpUserRole.role_code)
            .join(ErpRole, ErpRole.code == ErpUserRole.role_code)
            .filter(ErpUserRole.user_id == user.id)
            .filter(ErpRole.active.is_(True))
            .all()
        }
        if has_request_context():
            setattr(g, cache_key, roles)
        return roles
    legacy_role = canonical_role(user.role)
    roles = {legacy_role} if legacy_role else set()
    if has_request_context():
        setattr(g, cache_key, roles)
    return roles


def effective_permissions(user, db=None):
    if not user or not user.active:
        return set()
    cache_key = f"erp_effective_permissions_{user.id}"
    if has_request_context() and hasattr(g, cache_key):
        return getattr(g, cache_key)
    database = db or SessionLocal()
    roles = effective_roles(user, database)
    if "ADMIN" in roles:
        permissions = set(PERMISSION_DEFINITIONS)
        if has_request_context():
            setattr(g, cache_key, permissions)
        return permissions

    if shared_rbac_enabled():
        if not rbac_schema_ready(database):
            permissions = set()
            if has_request_context():
                setattr(g, cache_key, permissions)
            return permissions
        role_permissions = {
            permission_code
            for (permission_code,) in database.query(ErpRolePermission.permission_code)
            .filter(ErpRolePermission.role_code.in_(roles))
            .all()
        }
        overrides = (
            database.query(
                ErpUserPermissionOverride.permission_code,
                ErpUserPermissionOverride.allowed,
            )
            .filter(ErpUserPermissionOverride.user_id == user.id)
            .all()
        )
        for permission_code, allowed in overrides:
            if allowed:
                role_permissions.add(permission_code)
            else:
                role_permissions.discard(permission_code)
        if has_request_context():
            setattr(g, cache_key, role_permissions)
        return role_permissions

    permissions = set()
    for role in roles:
        permissions.update(ROLE_PERMISSION_MAP.get(role, set()))
    if has_request_context():
        setattr(g, cache_key, permissions)
    return permissions


def can(user, permission, db=None):
    if not user or not user.active:
        return False
    if not shared_rbac_enabled():
        if canonical_role(user.role) == "ADMIN":
            return True
        return permission not in LEGACY_ADMIN_ONLY_PERMISSIONS
    return permission in effective_permissions(user, db)


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def current_user():
    if has_request_context() and hasattr(g, "erp_current_user"):
        return g.erp_current_user
    user_id = session.get("user_id")
    if not user_id:
        if has_request_context():
            g.erp_current_user = None
        return None
    db = SessionLocal()
    user = db.get(User, user_id)
    if not user or not user.active:
        session.clear()
        if has_request_context():
            g.erp_current_user = None
        return None
    if shared_rbac_enabled():
        session_version = session.get("auth_version")
        if session_version is None or int(session_version) != int(user.auth_version or 1):
            session.clear()
            if has_request_context():
                g.erp_current_user = None
            return None
    if has_request_context():
        g.erp_current_user = user
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            if portal_sso_enabled():
                target = request.full_path if request.query_string else request.path
                return redirect(portal_login_url("ESTOQUE", target))
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def permission_required(permission):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Autenticacao obrigatoria."}), 401
                if portal_sso_enabled():
                    target = request.full_path if request.query_string else request.path
                    return redirect(portal_login_url("ESTOQUE", target))
                return redirect(url_for("login", next=request.path))
            if shared_rbac_enabled() and not rbac_schema_ready():
                message = "Autorizacao compartilhada temporariamente indisponivel."
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": message}), 503
                return message, 503
            if not can(user, permission):
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Acesso nao autorizado."}), 403
                flash("Acesso restrito para este perfil.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def roles_required(*roles):
    canonical_roles = {canonical_role(role) for role in roles}

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or not (effective_roles(user) & canonical_roles):
                flash("Acesso restrito para este perfil.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def ensure_initial_data():
    db = SessionLocal()
    try:
        rbac_tables_exist = rbac_schema_ready(db)
        admin = db.query(User).filter_by(username=Config.DEFAULT_ADMIN_USERNAME).one_or_none()
        active_admin_exists = (
            db.query(User.id)
            .filter(
                User.active.is_(True),
                User.role.in_(("ADM", "ADMIN")),
            )
            .first()
            is not None
        )
        if rbac_tables_exist:
            active_admin_exists = active_admin_exists or (
                db.query(User.id)
                .join(ErpUserRole, ErpUserRole.user_id == User.id)
                .filter(
                    User.active.is_(True),
                    ErpUserRole.role_code == "ADMIN",
                )
                .first()
                is not None
            )

        created_admin = admin is None and not active_admin_exists
        if created_admin:
            admin = User(
                username=Config.DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(Config.DEFAULT_ADMIN_PASSWORD),
                role="ADM",
                active=True,
            )
            db.add(admin)

        bootstrap_flag = db.query(AppSetting).filter_by(key=ADMIN_BOOTSTRAP_FLAG).one_or_none()
        if bootstrap_flag is None:
            # Existing credentials are operational data: startup never resets them.
            db.add(AppSetting(key=ADMIN_BOOTSTRAP_FLAG, value="true"))

        for key, value in DEFAULT_SETTINGS.items():
            existing = db.query(AppSetting).filter_by(key=key).one_or_none()
            if existing is None:
                db.add(AppSetting(key=key, value=value))

        db.flush()
        if rbac_tables_exist:
            for code, name in ROLE_DEFINITIONS.items():
                role = db.get(ErpRole, code)
                if role is None:
                    db.add(ErpRole(code=code, name=name, description=name, active=True))
                else:
                    role.name = name
            for code, (module, description) in PERMISSION_DEFINITIONS.items():
                permission = db.get(ErpPermission, code)
                if permission is None:
                    db.add(
                        ErpPermission(
                            code=code,
                            module=module,
                            description=description,
                        )
                    )
            db.flush()
            for role_code, permissions in ROLE_PERMISSION_MAP.items():
                for permission_code in permissions:
                    existing = db.get(
                        ErpRolePermission,
                        {
                            "role_code": role_code,
                            "permission_code": permission_code,
                        },
                    )
                    if existing is None:
                        db.add(
                            ErpRolePermission(
                                role_code=role_code,
                                permission_code=permission_code,
                            )
                        )
            db.flush()
            if created_admin:
                db.add(ErpUserRole(user_id=admin.id, role_code="ADMIN"))

        db.commit()
    finally:
        db.close()
