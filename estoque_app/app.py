import hmac
import logging
import os
import sys
from datetime import datetime
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import func, or_
from werkzeug.exceptions import NotFound

from auth import (
    ROLE_DEFINITIONS,
    can,
    current_user,
    effective_roles,
    ensure_initial_data,
    hash_password,
    login_required,
    permission_required,
    rbac_schema_ready,
    record_auth_audit,
    roles_required,
    shared_rbac_enabled,
    sync_user_roles,
    verify_password,
)
from config import APP_ROOT, BASE_DIR, Config, EXPORTS_DIR, LOGS_DIR
from database import SessionLocal, init_db
from models import (
    ErpUserRole,
    InventoryCount,
    LabelPrintJob,
    Movement,
    SKU,
    StockBalance,
    User,
    now_utc,
)
from purchase_report import build_purchase_inspection_report
from services.backup_service import create_backup
from services.etiqueta_service import (
    create_label_job,
    prepare_label_job_file,
    print_label_job,
    print_zpl,
    render_label_zpl,
    save_zpl_file,
    zpl_for_quantity,
)
from services.excel_service import (
    create_template_files,
    export_inventory_preview,
    export_inventory_report,
    export_movements_report,
    export_pending_commitments_report,
    export_stock_report,
    import_commitments_from_excel,
    import_inventory_balance_additions_from_excel,
    import_inventory_counts_from_excel,
    import_label_jobs_from_excel,
    import_consumption_from_excel,
    import_mass_material_movements,
    import_pending_commitment_consumptions,
    label_queue_summary,
    mass_material_rows_from_form,
    parse_mass_materials_from_excel,
    parse_pending_commitment_consumptions_from_excel,
    skip_preparation_rows_for_consumption,
)
from services.cadastro_supabase_service import (
    status as cadastro_supabase_status,
    sync_catalog_from_cadastro,
    sync_skus_from_cadastro,
)
from services.estoque_service import (
    adjust_balance_to_count,
    append_manual_entry_exception,
    build_backflush_preview,
    close_inventory_and_adjust,
    dashboard_movement_cache,
    delete_movement,
    cancel_movement,
    decimal_to_str,
    get_active_inventory_session,
    get_setting,
    get_setting_bool,
    get_sku_by_code,
    inventory_stats,
    normalize_sku,
    open_inventory_session,
    optional_decimal_to_str,
    movement_available_snapshots,
    pending_commitment_for_movement,
    pending_commitments_by_sku,
    parse_backflush_rows,
    register_consumption_from_commitment,
    register_movement,
    register_entry_with_backflush,
    resolve_movement_context,
    reset_operational_data,
    save_inventory_count,
    set_setting,
    to_decimal,
    to_optional_decimal,
    create_or_update_sku,
)
from services.erp_service import (
    active_work_orders,
    cancel_purchase_order,
    cancel_purchase_order_by_idempotency_key,
    close_purchase_order_by_idempotency_key,
    close_purchase_order_financial,
    close_purchase_order_technical,
    confirm_receipt,
    create_purchase_order,
    pending_purchase_orders,
    pending_purchase_order_lines_by_sku,
    purchase_order_financial_detail,
    purchase_orders_dashboard,
    register_purchase_order_financial_entry,
    reverse_receipt,
    sync_legacy_purchase_order,
    work_order_materials,
)


app = Flask(
    __name__,
    template_folder=str(APP_ROOT / "templates"),
    static_folder=str(APP_ROOT / "static"),
)
app.config.from_object(Config)


def configure_logging():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOGS_DIR / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


configure_logging()
init_db()
ensure_initial_data()


def erp_feature_enabled():
    """The new cross-module flow remains opt-in until the cutover."""
    return os.environ.get("ERP_FEATURE_FLAG", "false").strip().lower() in {"1", "true", "yes", "sim", "on"}


def feature_enabled(name, default=False):
    value = os.environ.get(name, "true" if default else "false")
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def po_suggestion_enabled():
    return feature_enabled("ERP_PO_SUGGESTION_ENABLED", False)


def movement_context_feature_enabled():
    return feature_enabled("ERP_MOVEMENT_CONTEXT_ENABLED", False)


def erp_feature_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if erp_feature_enabled():
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Integração ERP desativada pela feature flag."}), 404
        return "Integração ERP desativada pela feature flag.", 404
    return wrapped


@app.teardown_appcontext
def remove_session(exception=None):
    SessionLocal.remove()


@app.context_processor
def inject_globals():
    user = current_user()
    return {
        "current_user": user,
        "can": lambda permission: can(user, permission),
        "effective_roles": sorted(effective_roles(user)) if user else [],
        "shared_rbac_enabled": shared_rbac_enabled(),
        "erp_feature_enabled": erp_feature_enabled(),
        "movement_context_enabled": movement_context_feature_enabled(),
        "po_suggestion_enabled": po_suggestion_enabled(),
        "fmt_qty": decimal_to_str,
        "fmt_min": optional_decimal_to_str,
        "movement_label": movement_label,
        "direct_print_available": direct_print_available(),
        "print_mode": request_print_mode(),
        "database_label": "SQLite local" if Config.SQLALCHEMY_DATABASE_URI.startswith("sqlite") else "Supabase Postgres",
        "deployment_label": "Sistema local" if Config.SQLALCHEMY_DATABASE_URI.startswith("sqlite") else "Sistema online mobile",
    }


@app.template_filter("dt")
def format_datetime(value):
    if not value:
        return ""
    return value.strftime("%d/%m/%Y %H:%M")


@app.template_filter("qty")
def format_qty(value):
    return decimal_to_str(value or 0)


@app.errorhandler(Exception)
def handle_exception(exc):
    app.logger.exception("Erro nao tratado: %s", exc)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": str(exc)}), 500
    flash(f"Erro inesperado: {exc}", "danger")
    return redirect(url_for("dashboard") if session.get("user_id") else url_for("login"))


@app.errorhandler(NotFound)
def handle_not_found(exc):
    if request.path == "/favicon.ico":
        return "", 204
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Rota nao encontrada."}), 404
    flash("Pagina nao encontrada. Voltei para a tela inicial.", "warning")
    return redirect(url_for("dashboard") if session.get("user_id") else url_for("login"))


def db():
    return SessionLocal()


def direct_print_available():
    return sys.platform.startswith("win")


def is_mobile_request():
    user_agent = request.headers.get("User-Agent", "").lower()
    mobile_tokens = ("android", "iphone", "ipad", "ipod", "mobile", "windows phone")
    return any(token in user_agent for token in mobile_tokens)


def request_print_mode():
    if is_mobile_request():
        return "none"
    if direct_print_available():
        return "server"
    return "bridge"


def direct_print_unavailable_message():
    return (
        "Impressao direta Zebra so funciona no Windows local com a impressora instalada. "
        "No Render pelo desktop, mantenha o app local aberto para usar a ponte de impressao."
    )


def local_bridge_unavailable_message():
    return (
        "Ponte local nao encontrada. Abra o app local ou o .exe no computador conectado a Zebra "
        "e tente imprimir novamente pelo desktop."
    )


def bridge_origin_allowed(origin):
    if not origin:
        return True
    parsed = urlsplit(origin)
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost"}:
        return True
    if host.endswith(".onrender.com"):
        return True
    configured = [item.strip().lower().rstrip("/") for item in os.environ.get("ESTOQUE_PRINT_BRIDGE_ORIGINS", "").split(",") if item.strip()]
    return origin.lower().rstrip("/") in configured


def add_bridge_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if bridge_origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin or "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Requested-With"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


def configured_printer_name(database):
    return get_setting(database, "default_printer_name", Config.DEFAULT_PRINTER_NAME) or Config.DEFAULT_PRINTER_NAME


def user_can_export(database, user):
    if not user:
        return False
    if not can(user, "estoque.reports.view"):
        return False
    return can(user, "estoque.settings.manage") or get_setting_bool(
        database,
        "operator_can_export",
        True,
    )


def can_print_sku(database, sku, user):
    if sku.active:
        return True
    return bool(
        user
        and can(user, "estoque.settings.manage")
        and get_setting_bool(database, "admin_can_print_inactive_sku", False)
    )


def flash_local_update_result(label, result, success_category="success"):
    if result.get("skipped") and result.get("ok"):
        flash(f"{label}: cadastro online sem alteracoes recentes para sincronizar.", "info")
        return
    if result.get("errors"):
        flash(f"{label}: atualizacao cancelada.", "danger")
        for error in result["errors"][:10]:
            flash(error, "warning")
        if len(result["errors"]) > 10:
            flash(f"Mais {len(result['errors']) - 10} erros ocultos.", "warning")
        return
    if label == "CODs":
        flash(
            "CODs sincronizados pelo Cadastro Supabase: "
            f"{result.get('created', 0)} criados, "
            f"{result.get('updated', 0)} atualizados"
            f"{', ' + str(result.get('status_updated')) + ' status revisado(s)' if result.get('status_updated') else ''}"
            f"{' e ' + str(result.get('duplicates_skipped')) + ' duplicado(s) ignorado(s)' if result.get('duplicates_skipped') else ''}.",
            success_category,
        )
    else:
        flash(
            "B.O.M sincronizada pelo Cadastro Supabase: "
            f"{result.get('processed', 0)} componente(s), "
            f"{result.get('items', 0)} item(ns), "
            f"{result.get('deleted', 0)} linha(s) antiga(s) substituida(s).",
            success_category,
        )
    if result.get("warnings"):
        flash(f"{len(result['warnings'])} linha(s)/arquivo(s) ignorado(s) na atualizacao.", "warning")
        for warning in result["warnings"][:10]:
            flash(warning, "warning")
        if len(result["warnings"]) > 10:
            flash(f"Mais {len(result['warnings']) - 10} aviso(s) oculto(s).", "warning")


def refresh_local_sources_quietly(database, include_bom=False):
    try:
        sync_catalog_from_cadastro(database, include_bom=include_bom, force=False)
    except Exception as exc:
        database.rollback()
        app.logger.warning("Sincronizacao automatica com Cadastro Supabase ignorada: %s", exc)


def can_access_label_job(job, user):
    return bool(
        user
        and (
            can(user, "estoque.settings.manage")
            or job.usuario_id == user.id
        )
    )


def movement_label(tipo):
    labels = {
        "ENTRADA": "ENTRADA",
        "SAIDA": "EMPENHO",
        "EMPENHO": "EMPENHO",
        "BAIXA": "BAIXA",
        "INVENTARIO": "INVENTARIO",
        "AJUSTE": "AJUSTE",
    }
    return labels.get(tipo, tipo or "")


def is_loopback_request():
    remote_addr = request.remote_addr or ""
    return remote_addr in {"127.0.0.1", "::1", "localhost"}


def stock_rows(database, filters):
    query = database.query(SKU).outerjoin(StockBalance)
    if filters.get("sku"):
        query = query.filter(SKU.sku.ilike(f"%{filters['sku']}%"))
    if filters.get("descricao"):
        query = query.filter(SKU.descricao.ilike(f"%{filters['descricao']}%"))
    if filters.get("grupo"):
        query = query.filter(SKU.grupo.ilike(f"%{filters['grupo']}%"))
    if filters.get("categoria"):
        query = query.filter(SKU.categoria.ilike(f"%{filters['categoria']}%"))
    if filters.get("localizacao"):
        query = query.filter(SKU.localizacao.ilike(f"%{filters['localizacao']}%"))
    if filters.get("active") == "1":
        query = query.filter(SKU.active.is_(True))
    elif filters.get("active") == "0":
        query = query.filter(SKU.active.is_(False))
    if filters.get("saldo_baixo") == "1":
        query = query.filter(SKU.estoque_minimo.isnot(None))
        query = query.filter(or_(StockBalance.saldo_atual <= SKU.estoque_minimo, StockBalance.saldo_atual.is_(None)))
    rows = query.order_by(SKU.sku).all()
    pending_by_sku = pending_commitments_by_sku(database, [sku.id for sku in rows])
    for sku in rows:
        saldo_atual = to_decimal(sku.balance.saldo_atual if sku.balance else 0)
        saldo_empenhado = pending_by_sku.get(sku.id, to_decimal(0))
        sku.saldo_empenhado = saldo_empenhado
        sku.saldo_disponivel = saldo_atual - saldo_empenhado
    return rows


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    rbac_ready = rbac_schema_ready(db())
    payload = {
        "ok": not shared_rbac_enabled() or rbac_ready,
        "shared_rbac_enabled": shared_rbac_enabled(),
        "shared_rbac_ready": rbac_ready,
    }
    return jsonify(payload), (200 if payload["ok"] else 503)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        database = db()
        if shared_rbac_enabled() and not rbac_schema_ready(database):
            flash(
                "Autorizacao compartilhada indisponivel. Aguarde a conclusao da migration.",
                "danger",
            )
            return render_template("login.html"), 503
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = database.query(User).filter_by(username=username).one_or_none()
        if user and user.active and verify_password(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            session["auth_version"] = int(user.auth_version or 1)
            flash("Login realizado com sucesso.", "success")
            next_path = request.args.get("next", "")
            if not next_path.startswith("/") or next_path.startswith("//"):
                next_path = url_for("dashboard")
            return redirect(next_path)
        flash("Usuario ou senha invalidos.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sessao encerrada.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
@permission_required("estoque.dashboard.view")
def dashboard():
    database = db()
    total_active = database.query(SKU).filter(SKU.active.is_(True)).count()
    low_stock = (
        database.query(SKU)
        .outerjoin(StockBalance)
        .filter(SKU.active.is_(True))
        .filter(SKU.estoque_minimo.isnot(None))
        .filter(or_(StockBalance.saldo_atual <= SKU.estoque_minimo, StockBalance.saldo_atual.is_(None)))
        .count()
    )
    last_movements = dashboard_movement_cache(database)
    return render_template(
        "dashboard.html",
        total_active=total_active,
        low_stock=low_stock,
        last_movements=last_movements,
    )


@app.route("/usuarios", methods=["GET", "POST"])
@login_required
@permission_required("estoque.users.manage")
def users():
    database = db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        roles = [
            role
            for role in request.form.getlist("roles")
            if role in ROLE_DEFINITIONS
        ] or [request.form.get("role", "OPERADOR")]
        roles = [role for role in roles if role in ROLE_DEFINITIONS] or ["OPERADOR"]
        if not username or not password:
            flash("Informe usuario e senha.", "danger")
        elif database.query(User).filter_by(username=username).one_or_none():
            flash("Usuario ja existe.", "danger")
        else:
            primary_role = "ADM" if "ADMIN" in roles else roles[0]
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=primary_role,
                active=True,
                auth_version=1,
            )
            database.add(user)
            database.flush()
            if rbac_schema_ready(database):
                sync_user_roles(
                    database,
                    user.id,
                    roles,
                    assigned_by=session.get("user_id"),
                )
                record_auth_audit(
                    database,
                    user_id=user.id,
                    actor_user_id=session.get("user_id"),
                    action="USER_CREATED",
                    before_data={},
                    after_data={
                        "username": username,
                        "active": True,
                        "roles": sorted(set(roles)),
                        "password_changed": True,
                    },
                )
            database.commit()
            flash("Usuario criado.", "success")
        return redirect(url_for("users"))
    rows = database.query(User).order_by(User.username).all()
    user_roles = {
        user.id: sorted(effective_roles(user, database))
        for user in rows
    }
    return render_template(
        "users.html",
        users=rows,
        user_roles=user_roles,
        role_definitions=ROLE_DEFINITIONS,
    )


@app.route("/usuarios/<int:user_id>/editar", methods=["POST"])
@login_required
@permission_required("estoque.users.manage")
def edit_user(user_id):
    database = db()
    user = database.get(User, user_id)
    if not user:
        flash("Usuario nao encontrado.", "danger")
        return redirect(url_for("users"))

    username = request.form.get("username", "").strip()
    roles = [
        role
        for role in request.form.getlist("roles")
        if role in ROLE_DEFINITIONS
    ]
    active = bool(request.form.get("active"))
    password = request.form.get("password", "")
    if not username or not roles:
        flash("Informe usuario e ao menos um perfil.", "danger")
        return redirect(url_for("users"))
    duplicate = (
        database.query(User)
        .filter(User.username == username, User.id != user.id)
        .one_or_none()
    )
    if duplicate:
        flash("Ja existe outro usuario com esse nome.", "danger")
        return redirect(url_for("users"))
    if user.id == session.get("user_id") and not active:
        flash("Voce nao pode inativar o proprio usuario.", "danger")
        return redirect(url_for("users"))

    current_roles = effective_roles(user, database)
    before_data = {
        "username": user.username,
        "active": bool(user.active),
        "roles": sorted(current_roles),
        "password_changed": False,
    }
    if "ADMIN" in current_roles and ("ADMIN" not in roles or not active):
        other_admins = (
            database.query(ErpUserRole.user_id)
            .join(User, User.id == ErpUserRole.user_id)
            .filter(
                ErpUserRole.role_code == "ADMIN",
                ErpUserRole.user_id != user.id,
                User.active.is_(True),
            )
            .count()
            if rbac_schema_ready(database)
            else database.query(User)
            .filter(
                User.id != user.id,
                User.active.is_(True),
                User.role == "ADM",
            )
            .count()
        )
        if not other_admins:
            flash("Mantenha ao menos um ADMIN ativo.", "danger")
            return redirect(url_for("users"))

    user.username = username
    user.active = active
    user.role = "ADM" if "ADMIN" in roles else roles[0]
    if password:
        user.password_hash = hash_password(password)
    user.auth_version = int(user.auth_version or 1) + 1
    if rbac_schema_ready(database):
        sync_user_roles(
            database,
            user.id,
            roles,
            assigned_by=session.get("user_id"),
        )
        record_auth_audit(
            database,
            user_id=user.id,
            actor_user_id=session.get("user_id"),
            action="USER_UPDATED",
            before_data=before_data,
            after_data={
                "username": username,
                "active": active,
                "roles": sorted(set(roles)),
                "password_changed": bool(password),
            },
        )
    database.commit()
    if user.id == session.get("user_id"):
        session["auth_version"] = user.auth_version
    flash("Usuario e acessos atualizados.", "success")
    return redirect(url_for("users"))


@app.route("/usuarios/<int:user_id>/toggle", methods=["POST"])
@login_required
@permission_required("estoque.users.manage")
def toggle_user(user_id):
    database = db()
    user = database.get(User, user_id)
    if user and user.id != session.get("user_id"):
        before_data = {
            "username": user.username,
            "active": bool(user.active),
            "roles": sorted(effective_roles(user, database)),
            "password_changed": False,
        }
        if user.active and "ADMIN" in effective_roles(user, database):
            other_admins = (
                database.query(ErpUserRole.user_id)
                .join(User, User.id == ErpUserRole.user_id)
                .filter(
                    ErpUserRole.role_code == "ADMIN",
                    ErpUserRole.user_id != user.id,
                    User.active.is_(True),
                )
                .count()
                if rbac_schema_ready(database)
                else database.query(User)
                .filter(
                    User.id != user.id,
                    User.active.is_(True),
                    User.role == "ADM",
                )
                .count()
            )
            if not other_admins:
                flash("Mantenha ao menos um ADMIN ativo.", "danger")
                return redirect(url_for("users"))
        user.active = not user.active
        user.auth_version = int(user.auth_version or 1) + 1
        if rbac_schema_ready(database):
            record_auth_audit(
                database,
                user_id=user.id,
                actor_user_id=session.get("user_id"),
                action="USER_STATUS_CHANGED",
                before_data=before_data,
                after_data={
                    **before_data,
                    "active": bool(user.active),
                },
            )
        database.commit()
        flash("Status do usuario atualizado.", "success")
    return redirect(url_for("users"))


@app.route("/configuracoes", methods=["GET", "POST"])
@login_required
@permission_required("estoque.settings.manage")
def settings():
    database = db()
    if request.method == "POST":
        set_setting(database, "allow_negative_stock", "true" if request.form.get("allow_negative_stock") else "false")
        set_setting(database, "operator_can_export", "true" if request.form.get("operator_can_export") else "false")
        set_setting(database, "admin_can_print_inactive_sku", "true" if request.form.get("admin_can_print_inactive_sku") else "false")
        set_setting(database, "default_printer_name", request.form.get("default_printer_name", "").strip())
        flash("Configuracoes salvas.", "success")
        return redirect(url_for("settings"))
    values = {
        "allow_negative_stock": get_setting_bool(database, "allow_negative_stock", False),
        "operator_can_export": get_setting_bool(database, "operator_can_export", True),
        "admin_can_print_inactive_sku": get_setting_bool(database, "admin_can_print_inactive_sku", False),
        "default_printer_name": configured_printer_name(database),
    }
    return render_template("settings.html", values=values, cadastro_status=cadastro_supabase_status())


@app.route("/skus")
@login_required
@permission_required("estoque.skus.view")
def skus():
    database = db()
    refresh_local_sources_quietly(database)
    term = request.args.get("q", "").strip()
    show_inactive = request.args.get("mostrar_inativos") == "1"
    query = database.query(SKU)
    if term:
        query = query.filter(or_(SKU.sku.ilike(f"%{term}%"), SKU.descricao.ilike(f"%{term}%")))
    if not show_inactive:
        query = query.filter(SKU.active.is_(True))
    return render_template(
        "skus.html",
        skus=query.order_by(SKU.sku).limit(500).all(),
        q=term,
        mostrar_inativos="1" if show_inactive else "",
        cadastro_status=cadastro_supabase_status(),
    )


@app.route("/atualizar_skus", methods=["POST"])
@app.route("/skus/atualizar-local", methods=["POST"])
@login_required
@permission_required("estoque.skus.manage")
def atualizar_skus_local():
    database = db()
    result = sync_skus_from_cadastro(database, force=True)
    flash_local_update_result("CODs", result)
    return redirect(request.referrer or url_for("skus"))


@app.route("/skus/novo", methods=["GET", "POST"])
@app.route("/skus/<int:sku_id>/editar", methods=["GET", "POST"])
@login_required
@permission_required("estoque.skus.manage")
def sku_form(sku_id=None):
    database = db()
    sku = database.get(SKU, sku_id) if sku_id else None
    if request.method == "POST":
        try:
            data = {
                "sku": request.form.get("sku"),
                "descricao": request.form.get("descricao"),
                "unidade": request.form.get("unidade"),
                "grupo": request.form.get("grupo"),
                "categoria": request.form.get("categoria"),
                "localizacao": request.form.get("localizacao"),
                "estoque_minimo": request.form.get("estoque_minimo"),
                "active": bool(request.form.get("active")),
            }
            if sku:
                existing = get_sku_by_code(database, data["sku"])
                if existing and existing.id != sku.id:
                    raise ValueError("COD ja cadastrado.")
                sku.sku = normalize_sku(data["sku"])
                sku.descricao = data["descricao"].strip()
                sku.unidade = data["unidade"].strip() or None
                sku.grupo = data["grupo"].strip() or None
                sku.categoria = data["categoria"].strip() or None
                sku.localizacao = data["localizacao"].strip() or None
                sku.estoque_minimo = to_optional_decimal(data["estoque_minimo"])
                sku.active = data["active"]
                database.commit()
            else:
                create_or_update_sku(database, data)
            flash("COD salvo com sucesso.", "success")
            return redirect(url_for("skus"))
        except Exception as exc:
            database.rollback()
            flash(str(exc), "danger")
    return render_template("sku_form.html", sku=sku)


@app.route("/skus/importar", methods=["GET", "POST"])
@login_required
@permission_required("estoque.skus.manage")
def import_skus():
    database = db()
    if request.method == "POST":
        result = sync_skus_from_cadastro(database, force=True)
        flash_local_update_result("CODs", result)
        return redirect(url_for("import_skus"))
    return render_template("import_skus.html", cadastro_status=cadastro_supabase_status())


@app.route("/estoque")
@login_required
@permission_required("estoque.stock.view")
def stock():
    database = db()
    filters = {
        "sku": request.args.get("sku", "").strip(),
        "descricao": request.args.get("descricao", "").strip(),
        "grupo": request.args.get("grupo", "").strip(),
        "categoria": request.args.get("categoria", "").strip(),
        "localizacao": request.args.get("localizacao", "").strip(),
        "saldo_baixo": request.args.get("saldo_baixo", ""),
        "active": request.args.get("active", "1"),
    }
    return render_template("stock.html", rows=stock_rows(database, filters), filters=filters, can_export=user_can_export(database, current_user()))


@app.route("/estoque/exportar")
@login_required
@permission_required("estoque.reports.view")
def export_stock():
    database = db()
    user = current_user()
    if not user_can_export(database, user):
        flash("Exportacao nao permitida para seu perfil.", "danger")
        return redirect(url_for("stock"))
    filters = dict(request.args)
    path = export_stock_report(database, user, filters)
    return send_file(path, as_attachment=True)


@app.route("/entrada", methods=["GET", "POST"])
@login_required
@permission_required("estoque.entry.create")
def entrada():
    database = db()
    refresh_local_sources_quietly(database, include_bom=True)
    sku = None
    sku_code = request.args.get("sku", "").strip()
    backflush = None
    po_prompt = None
    entry_draft = {}
    idempotency_key = (
        str(request.form.get("idempotency_key") or "").strip()
        or f"stock-entry:{uuid4()}"
    )
    if request.method == "POST":
        try:
            sku = get_sku_by_code(database, request.form.get("sku"), active_only=True)
            if not sku:
                raise ValueError("COD nao cadastrado ou inativo. Entrada bloqueada.")
            quantidade = request.form.get("quantidade")
            documento = request.form.get("documento", "")
            observacao = request.form.get("observacao", "")
            entry_origin = str(request.form.get("entry_origin") or "").strip().upper()
            manual_reason = str(request.form.get("manual_reason") or "").strip()
            po_candidates = []
            if po_suggestion_enabled() and erp_feature_enabled():
                po_candidates = pending_purchase_order_lines_by_sku(
                    database,
                    sku_id=sku.id,
                    sku_code=sku.sku,
                )
            if po_candidates:
                if entry_origin not in {"PURCHASE_ORDER", "MANUAL"}:
                    entry_draft = {
                        "sku": sku.sku,
                        "quantidade": quantidade,
                        "documento": documento,
                        "observacao": observacao,
                        "idempotency_key": idempotency_key,
                    }
                    po_prompt = {"candidates": po_candidates}
                    return render_template(
                        "movement_form.html",
                        mode="entrada",
                        sku=sku,
                        sku_code=sku.sku,
                        backflush=None,
                        po_prompt=po_prompt,
                        entry_draft=entry_draft,
                        idempotency_key=idempotency_key,
                    )
                if entry_origin == "PURCHASE_ORDER":
                    selected_line_id = str(
                        request.form.get("purchase_order_line_id") or ""
                    ).strip()
                    selected = next(
                        (
                            row
                            for row in po_candidates
                            if str(row["purchase_order_line_id"]) == selected_line_id
                        ),
                        None,
                    )
                    if not selected:
                        raise ValueError(
                            "Selecione uma linha pendente valida da O.C. sugerida."
                        )
                    flash(
                        "O item possui O.C. pendente. Confirme a entrada pela "
                        "Inspecao de Recebimento para atualizar pedido e saldo uma unica vez.",
                        "warning",
                    )
                    return redirect(
                        url_for(
                            "erp_receipts_screen",
                            order_id=selected["purchase_order_id"],
                            line_id=selected["purchase_order_line_id"],
                            quantity=quantidade,
                            sku=sku.sku,
                            document=documento,
                        )
                    )
                if not manual_reason:
                    raise ValueError(
                        "Informe o motivo para registrar entrada direta sem vincular "
                        "a O.C. pendente."
                    )
                observacao = append_manual_entry_exception(observacao, manual_reason)
            if request.form.get("confirm_backflush") == "1":
                component_rows = parse_backflush_rows(
                    database,
                    request.form.getlist("component_sku"),
                    request.form.getlist("component_quantidade"),
                )
                register_entry_with_backflush(
                    database,
                    sku,
                    quantidade,
                    session["user_id"],
                    component_rows,
                    documento=documento,
                    observacao=observacao,
                    allow_negative=get_setting_bool(database, "allow_negative_stock", False),
                    idempotency_key=idempotency_key,
                )
                total_consumed = sum((row["quantidade"] for row in component_rows), to_decimal(0))
                flash(
                    f"Entrada registrada com backflush: {len(component_rows)} componente(s), "
                    f"total consumido {decimal_to_str(total_consumed)}.",
                    "success",
                )
                return redirect(url_for("entrada"))

            bom_rows = build_backflush_preview(database, sku, quantidade)
            if bom_rows:
                backflush = {
                    "quantidade": quantidade,
                    "documento": documento,
                    "observacao": observacao,
                    "entry_origin": entry_origin,
                    "manual_reason": manual_reason,
                    "idempotency_key": idempotency_key,
                    "components": bom_rows,
                }
                flash("Item com B.O.M cadastrada. Confirme o consumo dos componentes.", "warning")
                return render_template(
                    "movement_form.html",
                    mode="entrada",
                    sku=sku,
                    sku_code=sku.sku,
                    backflush=backflush,
                    po_prompt=None,
                    entry_draft={},
                    idempotency_key=idempotency_key,
                )

            register_movement(
                database,
                sku,
                "ENTRADA",
                quantidade,
                session["user_id"],
                documento=documento,
                observacao=observacao,
                source_type="MANUAL_ENTRY",
                idempotency_key=idempotency_key,
            )
            flash("Entrada registrada com sucesso.", "success")
            return redirect(url_for("entrada"))
        except Exception as exc:
            database.rollback()
            flash(str(exc), "danger")
            sku_code = request.form.get("sku", "")
    if sku_code:
        sku = get_sku_by_code(database, sku_code, active_only=True)
        if not sku:
            flash("COD nao cadastrado ou inativo. Entrada bloqueada.", "danger")
    return render_template(
        "movement_form.html",
        mode="entrada",
        sku=sku,
        sku_code=sku_code,
        backflush=backflush,
        po_prompt=po_prompt,
        entry_draft=entry_draft,
        idempotency_key=idempotency_key,
    )


@app.route("/bom/importar", methods=["GET", "POST"])
@login_required
@permission_required("estoque.import")
def import_bom():
    result = None
    if request.method == "POST":
        database = db()
        try:
            sku_result, result = sync_catalog_from_cadastro(database, include_bom=True, force=True)
        except Exception as exc:
            database.rollback()
            flash(f"Falha ao sincronizar B.O.M: {exc}", "danger")
            return redirect(url_for("import_bom"))
        if sku_result.get("errors"):
            flash_local_update_result("CODs", sku_result)
            return redirect(url_for("import_bom"))
        if result["errors"]:
            flash("Sincronizacao cancelada. Nenhuma estrutura B.O.M foi alterada.", "danger")
        else:
            flash(
                f"B.O.M sincronizada: {result['processed']} componente(s) em "
                f"{result['items']} item(ns). Estruturas antigas substituidas: {result['deleted']}.",
                "success",
            )
            return redirect(url_for("import_bom"))
    return render_template("bom_import.html", result=result, cadastro_status=cadastro_supabase_status())


@app.route("/bom/atualizar-local", methods=["POST"])
@login_required
@permission_required("estoque.import")
def atualizar_bom_local():
    database = db()
    sku_result, result = sync_catalog_from_cadastro(database, include_bom=True, force=True)
    if sku_result.get("errors"):
        flash_local_update_result("CODs", sku_result)
        return redirect(request.referrer or url_for("import_bom"))
    flash_local_update_result("B.O.M", result)
    return redirect(request.referrer or url_for("import_bom"))


@app.route("/saida", methods=["GET", "POST"])
@login_required
@permission_required("estoque.commitment.create")
def saida():
    database = db()
    sku = None
    sku_code = request.args.get("sku", "").strip()
    # O template também é usado pela entrada e sempre espera um rascunho.
    # Inicializá-lo aqui impede que a tela de empenho falhe antes de registrar
    # qualquer movimentação e preserva os campos quando houver validação.
    entry_draft = {}
    idempotency_key = (
        str(request.form.get("idempotency_key") or "").strip()
        or f"stock-commitment:{uuid4()}"
    )
    if request.method == "POST":
        try:
            sku = get_sku_by_code(database, request.form.get("sku"), active_only=True)
            if not sku:
                raise ValueError("COD nao cadastrado ou inativo. Empenho bloqueado.")
            register_movement(
                database,
                sku,
                "EMPENHO",
                request.form.get("quantidade"),
                session["user_id"],
                documento=request.form.get("documento", ""),
                observacao=request.form.get("observacao", ""),
                work_order_id=request.form.get("work_order_id"),
                setor=request.form.get("setor", ""),
                reference_text=request.form.get("reference_text", ""),
                link_updated_by=session["user_id"],
                require_context=True,
                idempotency_key=idempotency_key,
            )
            flash("Empenho registrado com sucesso.", "success")
            return redirect(url_for("saida"))
        except Exception as exc:
            database.rollback()
            flash(str(exc), "danger")
            sku_code = request.form.get("sku", "")
            entry_draft = {
                "sku": sku_code,
                "quantidade": request.form.get("quantidade", ""),
                "documento": request.form.get("documento", ""),
                "observacao": request.form.get("observacao", ""),
            }
    if sku_code:
        sku = get_sku_by_code(database, sku_code, active_only=True)
        if not sku:
            flash("COD nao cadastrado ou inativo. Empenho bloqueado.", "danger")
    return render_template(
        "movement_form.html",
        mode="saida",
        sku=sku,
        sku_code=sku_code,
        idempotency_key=idempotency_key,
        entry_draft=entry_draft,
    )


@app.route("/empenhos/importar", methods=["GET", "POST"])
@login_required
@permission_required("estoque.commitment.create")
def import_commitments():
    database = db()
    result = None
    if request.method == "POST":
        try:
            if request.form.get("action") == "confirm_mass_materials":
                rows = mass_material_rows_from_form(request.form)
            else:
                file = request.files.get("file")
                if not file or not file.filename:
                    flash("Selecione uma planilha Excel para importar.", "danger")
                    return redirect(url_for("import_commitments"))
                preview = parse_mass_materials_from_excel(file)
                if preview["errors"]:
                    result = {"errors": preview["errors"]}
                    flash("A planilha possui erros. Nenhum empenho foi registrado.", "danger")
                    return render_template("commitment_import.html", result=result)
                if preview["missing_quantities"]:
                    return render_template(
                        "commitment_import.html",
                        result=None,
                        preview=preview,
                        documento=request.form.get("documento", ""),
                        observacao=request.form.get("observacao", ""),
                    )
                rows = preview["rows"]
            result = import_mass_material_movements(
                database,
                rows,
                "EMPENHO",
                session["user_id"],
                documento=request.form.get("documento", ""),
                observacao=request.form.get("observacao", ""),
            )
            if result["errors"]:
                flash("A planilha possui erros. Nenhum empenho foi registrado.", "danger")
            else:
                detail = (
                    f" Conjuntos ignorados: {result.get('skipped_assemblies', 0)}."
                    if result.get("skipped_assemblies")
                    else ""
                )
                flash(
                    f"Empenhos importados: {result['processed']} item(ns), total empenhado {result['total_committed']}.{detail}",
                    "success",
                )
                return redirect(url_for("import_commitments"))
        except Exception as exc:
            database.rollback()
            flash(f"Falha ao importar planilha: {exc}", "danger")
    return render_template("commitment_import.html", result=result)


@app.route("/baixa", methods=["GET", "POST"])
@login_required
@permission_required("estoque.consumption.create")
def baixa():
    database = db()
    user = current_user()
    result = None
    sku = None
    sku_code = request.args.get("sku", "").strip()
    idempotency_key = (
        str(request.form.get("idempotency_key") or "").strip()
        or f"stock-consumption:{uuid4()}"
    )
    if request.method == "POST":
        try:
            if request.form.get("action") == "confirm_mass_materials":
                rows = mass_material_rows_from_form(request.form)
            elif request.form.get("action") == "manual_baixa":
                sku = get_sku_by_code(database, request.form.get("sku"), active_only=True)
                if not sku:
                    raise ValueError("COD nao cadastrado ou inativo. Baixa bloqueada.")
                baixa_manual = register_movement(
                    database,
                    sku,
                    "BAIXA",
                    request.form.get("quantidade"),
                    session["user_id"],
                    documento=request.form.get("documento", ""),
                    observacao=request.form.get("observacao", ""),
                    allow_negative=can(user, "estoque.settings.manage") or get_setting_bool(database, "allow_negative_stock", False),
                    work_order_id=request.form.get("work_order_id"),
                    setor=request.form.get("setor", ""),
                    reference_text=request.form.get("reference_text", ""),
                    link_updated_by=session["user_id"],
                    require_context=True,
                    idempotency_key=idempotency_key,
                )
                flash(f"Baixa manual registrada: {decimal_to_str(baixa_manual.quantidade)} de {sku.sku}.", "success")
                return redirect(url_for("baixa", sku=sku.sku))
            else:
                file = request.files.get("file")
                if not file or not file.filename:
                    flash("Selecione uma planilha Excel para importar.", "danger")
                    return redirect(url_for("baixa"))
                pending_preview = parse_pending_commitment_consumptions_from_excel(file)
                if pending_preview is not None:
                    if pending_preview["errors"]:
                        result = {"errors": pending_preview["errors"]}
                        flash("A planilha possui erros. Nenhuma baixa foi registrada.", "danger")
                        return render_template("consumption_import.html", result=result)
                    result = import_pending_commitment_consumptions(
                        database,
                        pending_preview["rows"],
                        session["user_id"],
                        documento=request.form.get("documento", ""),
                        observacao=request.form.get("observacao", ""),
                        allow_negative=can(user, "estoque.settings.manage")
                        or get_setting_bool(database, "allow_negative_stock", False),
                    )
                    if result["errors"]:
                        flash("A planilha possui erros. Nenhuma baixa foi registrada.", "danger")
                    else:
                        flash(
                            f"Baixa de empenhos importada: {result['processed']} item(ns), "
                            f"total baixado {result['total_consumed']}.",
                            "success",
                        )
                        return redirect(url_for("baixa"))
                    return render_template("consumption_import.html", result=result)
                file.stream.seek(0)
                preview = parse_mass_materials_from_excel(file)
                if preview["errors"]:
                    result = {"errors": preview["errors"]}
                    flash("A planilha possui erros. Nenhuma baixa foi registrada.", "danger")
                    return render_template("consumption_import.html", result=result)
                preview = skip_preparation_rows_for_consumption(preview)
                if preview["missing_quantities"]:
                    return render_template(
                        "consumption_import.html",
                        result=None,
                        preview=preview,
                        documento=request.form.get("documento", ""),
                        observacao=request.form.get("observacao", ""),
                    )
                rows = preview["rows"]
            result = import_mass_material_movements(
                database,
                rows,
                "BAIXA",
                session["user_id"],
                documento=request.form.get("documento", ""),
                observacao=request.form.get("observacao", ""),
                allow_negative=can(user, "estoque.settings.manage") or get_setting_bool(database, "allow_negative_stock", False),
            )
            if result["errors"]:
                flash("A planilha possui erros. Nenhuma baixa foi registrada.", "danger")
            else:
                detail = (
                    f" Conjuntos ignorados: {result.get('skipped_assemblies', 0)}."
                    if result.get("skipped_assemblies")
                    else ""
                )
                if result.get("skipped_preparation"):
                    detail = f"{detail} Preparacao ignorada: {result.get('skipped_preparation')} linha(s)."
                flash(
                    f"Baixa importada: {result['processed']} item(ns), total consumido {result['total_consumed']}.{detail}",
                    "success",
                )
                return redirect(url_for("baixa"))
        except Exception as exc:
            database.rollback()
            flash(f"Falha ao registrar baixa: {exc}", "danger")
            sku_code = request.form.get("sku", sku_code)
    if sku_code:
        sku = get_sku_by_code(database, sku_code, active_only=True)
        if not sku:
            flash("COD nao cadastrado ou inativo. Baixa bloqueada.", "danger")
    return render_template(
        "consumption_import.html",
        result=result,
        sku=sku,
        sku_code=sku_code,
        idempotency_key=idempotency_key,
    )


@app.route("/inventario-mobile")
@login_required
def inventory_mobile_legacy():
    return redirect(url_for("inventory_mobile"))


@app.route("/inventario", methods=["GET", "POST"])
@login_required
@permission_required("estoque.inventory.manage")
def inventory_mobile():
    database = db()
    user = current_user()
    sku = None
    sku_code = request.args.get("sku", "").strip()

    if request.method == "POST":
        try:
            sku = get_sku_by_code(database, request.form.get("sku"), active_only=True)
            if not sku:
                raise ValueError("COD nao cadastrado ou inativo.")
            counted_qty = request.form.get("quantidade_contada")
            movement = adjust_balance_to_count(
                database,
                sku,
                counted_qty,
                user.id,
                documento="INVENTARIO",
                observacao=request.form.get("observacao", ""),
            )
            diferenca = to_decimal(movement.saldo_posterior) - to_decimal(movement.saldo_anterior)
            flash(
                f"Inventario salvo: {sku.sku}. Diferenca {decimal_to_str(diferenca)}.",
                "success" if diferenca == 0 else "warning",
            )
            return redirect(url_for("inventory_mobile"))
        except Exception as exc:
            database.rollback()
            flash(str(exc), "danger")
            sku_code = request.form.get("sku", "")

    if sku_code:
        sku = get_sku_by_code(database, sku_code, active_only=True)
        if not sku:
            flash("COD nao cadastrado ou inativo.", "danger")

    last_counts = (
        database.query(Movement)
        .filter(Movement.tipo == "INVENTARIO", Movement.usuario_id == user.id)
        .order_by(Movement.created_at.desc())
        .limit(12)
        .all()
    )

    return render_template(
        "inventory_mobile.html",
        sku=sku,
        sku_code=sku_code,
        last_counts=last_counts,
    )


@app.route("/imprimir-etiqueta", methods=["GET", "POST"])
@login_required
@permission_required("estoque.labels.use")
def print_label():
    database = db()
    user = current_user()
    sku = None
    sku_code = request.values.get("sku", "").strip()
    if sku_code:
        sku = get_sku_by_code(database, sku_code)
        if not sku:
            flash("COD nao cadastrado.", "danger")
        elif not can_print_sku(database, sku, user):
            flash("COD inativo. Impressao bloqueada.", "danger")
            sku = None

    if request.method == "POST" and sku:
        try:
            quantidade = int(request.form.get("quantidade") or 1)
            if quantidade <= 0:
                raise ValueError("Quantidade de etiquetas deve ser maior que zero.")
            job = create_label_job(database, sku, quantidade, "MANUAL", user.id)
            zpl = zpl_for_quantity(sku.sku, sku.descricao, quantidade)
            path = save_zpl_file(zpl, prefix=f"etiqueta_{sku.sku}")
            job.zpl_path = str(path)
            database.commit()
            action = request.form.get("action")
            if action == "print":
                if request_print_mode() != "server":
                    job.status = "ERRO"
                    job.erro = direct_print_unavailable_message()
                    database.commit()
                    flash(direct_print_unavailable_message(), "warning")
                    return send_file(path, as_attachment=True)
                print_label_job(database, job, printer_name=configured_printer_name(database))
                flash(f"{quantidade} etiqueta(s) enviada(s) para impressao.", "success")
            else:
                return send_file(path, as_attachment=True)
            return redirect(url_for("print_label"))
        except Exception as exc:
            database.rollback()
            flash(str(exc), "danger")
    return render_template("print_label.html", sku=sku, sku_code=sku_code)


@app.route("/api/labels/zpl", methods=["POST"])
@login_required
@permission_required("estoque.labels.use")
def api_label_zpl():
    database = db()
    user = current_user()
    payload = request.get_json(silent=True) or request.form
    sku = get_sku_by_code(database, payload.get("sku"))
    if not sku:
        return jsonify({"ok": False, "error": "COD nao cadastrado."}), 404
    if not can_print_sku(database, sku, user):
        return jsonify({"ok": False, "error": "COD inativo. Impressao bloqueada."}), 400
    try:
        quantidade = int(payload.get("quantidade") or 1)
        if quantidade <= 0:
            raise ValueError("Quantidade de etiquetas deve ser maior que zero.")
        job = create_label_job(database, sku, quantidade, "MANUAL", user.id)
        zpl = zpl_for_quantity(sku.sku, sku.descricao, quantidade)
        path = save_zpl_file(zpl, prefix=f"etiqueta_{sku.sku}")
        job.zpl_path = str(path)
        database.commit()
        return jsonify({"ok": True, "job_id": job.id, "zpl": zpl, "path": str(path)})
    except Exception as exc:
        database.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/movimentacoes")
@login_required
@permission_required("estoque.movement.view")
def movements():
    database = db()
    user = current_user()
    tipo = request.args.get("tipo", "")
    query = database.query(Movement)
    if not can(user, "estoque.movement.cancel_any"):
        query = query.filter(Movement.tipo.in_(["ENTRADA", "EMPENHO", "BAIXA", "SAIDA"]))
    if tipo:
        if tipo == "EMPENHO":
            query = query.filter(Movement.tipo.in_(["EMPENHO", "SAIDA"]))
        else:
            query = query.filter(Movement.tipo == tipo)
    rows = query.order_by(Movement.created_at.desc()).limit(500).all()
    operation_ids = {
        str(movement.operation_id)
        for movement in rows
        if movement.operation_id
        and movement.source_type != "MOVEMENT_CANCELLATION"
    }
    operation_sizes = {}
    if operation_ids:
        operation_sizes = {
            str(operation_id): count
            for operation_id, count in (
                database.query(Movement.operation_id, func.count(Movement.id))
                .filter(
                    Movement.operation_id.in_(operation_ids),
                    Movement.source_type != "MOVEMENT_CANCELLATION",
                )
                .group_by(Movement.operation_id)
                .all()
            )
        }
    available_snapshots = movement_available_snapshots(database, rows)
    work_order_cache = {}
    for movement in rows:
        movement.pending_commitment = pending_commitment_for_movement(database, movement)
        movement.consumption_idempotency_key = (
            f"commitment-consumption:{movement.id}:{uuid4()}"
        )
        movement.operation_size = operation_sizes.get(
            str(movement.operation_id),
            0,
        )
        movement.saldo_posterior_disponivel = available_snapshots.get(movement.id, to_decimal(movement.saldo_posterior))
        movement.work_order_label = ""
        if movement.work_order_id:
            key = str(movement.work_order_id)
            if key not in work_order_cache:
                try:
                    context = resolve_movement_context(
                        database,
                        work_order_id=key,
                        active_work_order_only=False,
                    )
                    work_order_cache[key] = context["work_order"] or {}
                except ValueError:
                    work_order_cache[key] = {}
            summary = work_order_cache[key]
            if summary:
                movement.work_order_label = (
                    f"O.S. {summary.get('numero_os') or summary.get('item_number')} "
                    f"· {str(summary.get('chassi') or '')[-8:]}"
                )
    return render_template(
        "movements.html",
        movements=rows,
        tipo=tipo,
        can_export=user_can_export(database, user),
        can_cancel_any=can(user, "estoque.movement.cancel_any"),
        can_cancel_own=can(user, "estoque.movement.cancel_own"),
    )


@app.route("/movimentacoes/<int:movement_id>/baixar-empenho", methods=["POST"])
@login_required
@permission_required("estoque.consumption.create")
def consume_commitment_route(movement_id):
    database = db()
    user = current_user()
    movement = database.get(Movement, movement_id)
    try:
        baixa = register_consumption_from_commitment(
            database,
            movement,
            request.form.get("quantidade", ""),
            session["user_id"],
            documento=request.form.get("documento", ""),
            observacao=request.form.get("observacao", ""),
            allow_negative=can(user, "estoque.settings.manage") or get_setting_bool(database, "allow_negative_stock", False),
            work_order_id=request.form.get("work_order_id"),
            setor=request.form.get("setor", ""),
            reference_text=request.form.get("reference_text", ""),
            correct_context=request.form.get("correct_context") == "1",
            context_reason=request.form.get("context_reason", ""),
            idempotency_key=request.form.get("idempotency_key"),
        )
        flash(
            f"Baixa {baixa.id} gerada a partir do empenho {movement_id}: {decimal_to_str(baixa.quantidade)}.",
            "success",
        )
    except Exception as exc:
        database.rollback()
        flash(f"Falha ao baixar empenho: {exc}", "danger")
    return redirect(url_for("movements", tipo=request.form.get("tipo", "EMPENHO")))


@app.route("/movimentacoes/<int:movement_id>/cancelar", methods=["POST"])
@login_required
@permission_required("estoque.movement.view")
def cancel_movement_route(movement_id):
    database = db()
    user = current_user()
    allow_any = can(user, "estoque.movement.cancel_any")
    if not allow_any and not can(user, "estoque.movement.cancel_own"):
        flash("Seu perfil nao pode cancelar movimentacoes.", "danger")
        return redirect(url_for("movements", tipo=request.form.get("tipo", "")))
    movement = database.get(Movement, movement_id)
    try:
        canceled, reversal, replayed = cancel_movement(
            database,
            movement,
            user.id,
            request.form.get("reason", ""),
            allow_any=allow_any,
            allow_negative=get_setting_bool(database, "allow_negative_stock", False),
        )
        if replayed:
            if canceled.operation_id:
                flash(
                    f"Operacao composta do movimento {movement_id} ja estava "
                    f"cancelada ({getattr(canceled, 'canceled_operation_size', 0)} "
                    "movimentos).",
                    "warning",
                )
            else:
                flash(f"Movimentacao {movement_id} ja estava cancelada.", "warning")
        elif reversal:
            if canceled.operation_id:
                flash(
                    f"Operacao composta cancelada integralmente: "
                    f"{getattr(canceled, 'canceled_operation_size', 0)} movimentos "
                    "e seus ajustes compensatorios foram registrados.",
                    "success",
                )
            else:
                flash(
                    f"Movimentacao {movement_id} cancelada; ajuste compensatorio "
                    f"{reversal.id} registrado.",
                    "success",
                )
        else:
            flash(f"Movimentacao {movement_id} cancelada.", "success")
    except Exception as exc:
        database.rollback()
        flash(f"Falha ao cancelar movimentacao: {exc}", "danger")
    return redirect(url_for("movements", tipo=request.form.get("tipo", "")))


@app.route("/movimentacoes/<int:movement_id>/excluir", methods=["POST"])
@login_required
@roles_required("ADM")
def delete_movement_route(movement_id):
    if shared_rbac_enabled():
        flash(
            "Exclusao fisica desativada. Use Cancelar para preservar o historico "
            "e gerar o ajuste compensatorio.",
            "warning",
        )
        return redirect(url_for("movements", tipo=request.form.get("tipo", "")))
    database = db()
    movement = database.get(Movement, movement_id)
    try:
        sku_code = movement.sku.sku if movement else ""
        saldo_corrigido = delete_movement(
            database,
            movement,
            allow_negative=get_setting_bool(database, "allow_negative_stock", False),
        )
        flash(
            f"Movimentacao {movement_id} excluida. Saldo atual de {sku_code}: {decimal_to_str(saldo_corrigido)}.",
            "success",
        )
    except Exception as exc:
        database.rollback()
        flash(f"Falha ao excluir movimentacao: {exc}", "danger")
    return redirect(url_for("movements", tipo=request.form.get("tipo", "")))


@app.route("/relatorios")
@login_required
@permission_required("estoque.reports.view")
def reports():
    database = db()
    return render_template("reports.html", can_export=user_can_export(database, current_user()))


@app.route("/relatorios/exportar/<tipo>")
@login_required
@permission_required("estoque.reports.view")
def export_report(tipo):
    database = db()
    user = current_user()
    if not user_can_export(database, user):
        flash("Exportacao nao permitida para seu perfil.", "danger")
        return redirect(url_for("reports"))
    if tipo == "estoque":
        path = export_stock_report(database, user, {})
    elif tipo == "entradas":
        path = export_movements_report(database, user, "ENTRADA")
    elif tipo in {"empenhos", "saidas"}:
        path = export_movements_report(database, user, ["EMPENHO", "SAIDA"])
    elif tipo == "empenhos_pendentes":
        path = export_pending_commitments_report(database, user)
    elif tipo == "baixas":
        path = export_movements_report(database, user, "BAIXA")
    elif tipo == "movimentacoes":
        path = export_movements_report(database, user)
    elif tipo == "inventario":
        path = export_inventory_report(database, user)
    else:
        flash("Relatorio invalido.", "danger")
        return redirect(url_for("reports"))
    return send_file(path, as_attachment=True)


@app.route("/backup", methods=["POST"])
@login_required
@permission_required("estoque.settings.manage")
def backup():
    path = create_backup()
    flash(f"Backup gerado: {path}", "success")
    return redirect(url_for("settings"))


@app.route("/resetar-dados", methods=["POST"])
@login_required
@permission_required("estoque.settings.manage")
def reset_data():
    database = db()
    try:
        deleted = reset_operational_data(database)
        flash(
            "Dados operacionais resetados: "
            f"{deleted['movements']} movimentacoes, "
            f"{deleted['inventory_counts']} contagens, "
            f"{deleted['inventory_sessions']} sessoes e "
            f"{deleted['label_print_jobs']} jobs de etiqueta.",
            "success",
        )
    except Exception as exc:
        database.rollback()
        flash(f"Falha ao resetar dados: {exc}", "danger")
    return redirect(url_for("settings"))


@app.route("/inventario-etiquetas", methods=["GET", "POST"])
@login_required
@permission_required("estoque.inventory.manage")
def inventory_labels():
    database = db()
    user = current_user()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            active_session = get_active_inventory_session(database)
            if action == "open_session":
                session_obj, created = open_inventory_session(database, user.id, request.form.get("observacao", ""))
                flash("Sessao de inventario aberta." if created else f"Sessao {session_obj.id} ja estava aberta.", "success")

            elif action == "add_job":
                sku = get_sku_by_code(database, request.form.get("sku"))
                if not sku:
                    raise ValueError("COD nao cadastrado.")
                if not can_print_sku(database, sku, user):
                    raise ValueError("COD inativo. Impressao bloqueada.")
                create_label_job(
                    database,
                    sku,
                    int(request.form.get("quantidade") or 1),
                    "INVENTARIO" if active_session else "MANUAL",
                    user.id,
                    active_session.id if active_session else None,
                )
                flash("Etiqueta adicionada a fila.", "success")

            elif action == "selected_jobs":
                created = 0
                for sku_id in request.form.getlist("selected_skus"):
                    sku = database.get(SKU, int(sku_id))
                    qty = int(request.form.get(f"qty_{sku_id}") or 1)
                    if sku and can_print_sku(database, sku, user):
                        create_label_job(
                            database,
                            sku,
                            qty,
                            "LOTE",
                            user.id,
                            active_session.id if active_session else None,
                        )
                        created += 1
                flash(f"{created} COD(s) adicionados a fila.", "success")

            elif action in {"generate_active", "generate_positive"}:
                qty = int(request.form.get("quantidade_lote") or 1)
                query = database.query(SKU).filter(SKU.active.is_(True))
                if action == "generate_positive":
                    query = query.join(StockBalance).filter(StockBalance.saldo_atual > 0)
                created = 0
                for sku in query.order_by(SKU.sku).all():
                    create_label_job(
                        database,
                        sku,
                        qty,
                        "LOTE" if action == "generate_active" else "INVENTARIO",
                        user.id,
                        active_session.id if active_session else None,
                    )
                    created += 1
                flash(f"{created} etiquetas adicionadas a fila.", "success")

            elif action == "import_jobs":
                file = request.files.get("file")
                if not file or not file.filename.lower().endswith(".xlsx"):
                    raise ValueError("Envie uma planilha .xlsx com COD e QUANTIDADE.")
                result = import_label_jobs_from_excel(database, file, user.id, active_session.id if active_session else None)
                flash(f"Importacao da fila concluida: {result['created']} registros criados.", "success")
                for error in result["errors"][:10]:
                    flash(error, "warning")

            elif action == "import_counts":
                if not active_session:
                    raise ValueError("Abra uma sessao de inventario antes de importar contagens.")
                file = request.files.get("file")
                if not file or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
                    raise ValueError("Envie uma planilha Excel valida com COD e SALDO_CONTADO.")
                result = import_inventory_counts_from_excel(database, file, active_session.id, user.id)
                if result["errors"]:
                    raise ValueError("Importacao cancelada. " + " | ".join(result["errors"][:5]))
                flash(f"Contagem em massa importada: {result['processed']} COD(s) contados.", "success")

            elif action == "import_count_additions":
                if not active_session:
                    raise ValueError("Abra uma sessao de inventario antes de somar saldos.")
                file = request.files.get("file")
                if not file or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
                    raise ValueError("Envie uma planilha Excel valida com COD e SALDO_SOMAR.")
                result = import_inventory_balance_additions_from_excel(database, file, active_session.id, user.id)
                if result["errors"]:
                    raise ValueError("Importacao cancelada. " + " | ".join(result["errors"][:5]))
                flash(
                    f"Saldos somados na contagem: {result['processed']} COD(s), "
                    f"total adicionado {result['total_added']}.",
                    "success",
                )

            elif action == "save_all_zpl":
                jobs = (
                    database.query(LabelPrintJob)
                    .filter(LabelPrintJob.status.in_(["PENDENTE", "ERRO"]))
                    .order_by(LabelPrintJob.created_at)
                    .all()
                )
                chunks = []
                for job in jobs:
                    path = prepare_label_job_file(database, job)
                    chunks.append(Path(path).read_text(encoding="utf-8"))
                combined = "\n".join(chunks)
                saved = save_zpl_file(combined, prefix="fila_etiquetas")
                return send_file(saved, as_attachment=True)

            elif action == "count_sku":
                if not active_session:
                    raise ValueError("Abra uma sessao de inventario antes de contar.")
                sku = get_sku_by_code(database, request.form.get("count_sku"), active_only=True)
                if not sku:
                    raise ValueError("COD nao cadastrado ou inativo.")
                count = save_inventory_count(database, active_session.id, sku, request.form.get("quantidade_contada"), user.id)
                flash(
                    f"Contagem salva: {sku.sku}. Diferenca {decimal_to_str(count.diferenca)}.",
                    "success" if count.diferenca == 0 else "warning",
                )

            elif action == "finalize_inventory":
                if not active_session:
                    raise ValueError("Nao ha sessao aberta.")
                adjusted = close_inventory_and_adjust(database, active_session, user.id)
                flash(f"Inventario finalizado. {adjusted} ajuste(s) gerados.", "success")

            elif action == "cancel_pending":
                query = database.query(LabelPrintJob).filter(LabelPrintJob.status == "PENDENTE")
                updated = query.update({LabelPrintJob.status: "CANCELADO"}, synchronize_session=False)
                database.commit()
                flash(f"{updated} job(s) cancelados.", "success")

        except Exception as exc:
            database.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("inventory_labels"))

    active_session = get_active_inventory_session(database)
    stats = inventory_stats(database, active_session)
    jobs_query = database.query(LabelPrintJob).order_by(LabelPrintJob.created_at.desc())
    jobs = jobs_query.limit(200).all()
    counts = []
    if active_session:
        counts = (
            database.query(InventoryCount)
            .filter_by(session_id=active_session.id)
            .order_by(InventoryCount.counted_at.desc())
            .limit(20)
            .all()
        )
    skus_for_selection = database.query(SKU).filter(SKU.active.is_(True)).order_by(SKU.sku).limit(300).all()
    return render_template(
        "inventory_labels.html",
        active_session=active_session,
        stats=stats,
        jobs=jobs,
        counts=counts,
        queue_summary=label_queue_summary(database),
        skus_for_selection=skus_for_selection,
    )


@app.route("/inventario/exportar-previa")
@login_required
@permission_required("estoque.inventory.manage")
def export_inventory_preview_route():
    database = db()
    active_session = get_active_inventory_session(database)
    if not active_session:
        flash("Nao ha sessao de inventario aberta.", "danger")
        return redirect(url_for("inventory_labels"))
    path = export_inventory_preview(database, current_user(), active_session)
    return send_file(path, as_attachment=True)


@app.route("/api/local-print-status", methods=["GET", "OPTIONS"])
def api_local_print_status():
    if request.method == "OPTIONS":
        return add_bridge_cors_headers(jsonify({"ok": True}))
    if not bridge_origin_allowed(request.headers.get("Origin", "")):
        return jsonify({"ok": False, "error": "Origem nao autorizada para a ponte local."}), 403
    response = jsonify({"ok": direct_print_available() and is_loopback_request(), "windows": direct_print_available()})
    return add_bridge_cors_headers(response)


@app.route("/api/local-print-zpl", methods=["POST", "OPTIONS"])
def api_local_print_zpl():
    if request.method == "OPTIONS":
        return add_bridge_cors_headers(jsonify({"ok": True}))
    if not is_loopback_request():
        response = jsonify({"ok": False, "error": "Ponte local aceita apenas chamadas do proprio computador."})
        return add_bridge_cors_headers(response), 403
    if not bridge_origin_allowed(request.headers.get("Origin", "")):
        response = jsonify({"ok": False, "error": "Origem nao autorizada para a ponte local."})
        return add_bridge_cors_headers(response), 403
    if not direct_print_available():
        response = jsonify({"ok": False, "error": "Esta ponte local precisa rodar no Windows conectado a Zebra."})
        return add_bridge_cors_headers(response), 400

    payload = request.get_json(silent=True) or {}
    zpl = payload.get("zpl")
    if not zpl:
        response = jsonify({"ok": False, "error": "ZPL nao informado."})
        return add_bridge_cors_headers(response), 400

    database = db()
    try:
        printer_name = (payload.get("printer_name") or "").strip() or configured_printer_name(database)
        target_printer = print_zpl(zpl, printer_name=printer_name)
        response = jsonify({"ok": True, "printer": target_printer})
        return add_bridge_cors_headers(response)
    except Exception as exc:
        response = jsonify({"ok": False, "error": str(exc)})
        return add_bridge_cors_headers(response), 500


@app.route("/api/label-jobs/<int:job_id>/zpl", methods=["POST"])
@login_required
@permission_required("estoque.labels.use")
def api_label_job_zpl(job_id):
    database = db()
    user = current_user()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    if not can_access_label_job(job, user):
        return jsonify({"ok": False, "error": "Acesso restrito para este job."}), 403
    if not can_print_sku(database, job.sku, user):
        return jsonify({"ok": False, "error": "COD inativo. Impressao bloqueada."}), 400
    path = prepare_label_job_file(database, job)
    zpl = Path(path).read_text(encoding="utf-8")
    return jsonify({"ok": True, "job_id": job.id, "zpl": zpl, "path": str(path)})


@app.route("/api/label-jobs/<int:job_id>/local-result", methods=["POST"])
@login_required
@permission_required("estoque.labels.use")
def api_label_job_local_result(job_id):
    database = db()
    user = current_user()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    if not can_access_label_job(job, user):
        return jsonify({"ok": False, "error": "Acesso restrito para este job."}), 403

    payload = request.get_json(silent=True) or {}
    if payload.get("ok"):
        job.status = "IMPRESSO"
        job.erro = None
        job.printed_at = now_utc()
    elif payload.get("queue_local"):
        job.status = "PENDENTE"
        job.erro = payload.get("error") or local_bridge_unavailable_message()
    else:
        job.status = "ERRO"
        job.erro = payload.get("error") or local_bridge_unavailable_message()
    database.commit()
    return jsonify({"ok": True, "status": job.status, "printed_at": job.printed_at.strftime("%d/%m/%Y %H:%M:%S") if job.printed_at else ""})


@app.route("/api/label-jobs/<int:job_id>/print", methods=["POST"])
@login_required
@permission_required("estoque.labels.use")
def api_print_label_job(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    if not can_access_label_job(job, current_user()):
        return jsonify({"ok": False, "error": "Acesso restrito para este job."}), 403
    if not can_print_sku(database, job.sku, current_user()):
        return jsonify({"ok": False, "error": "COD inativo. Impressao bloqueada."}), 400
    if request_print_mode() != "server":
        job.status = "ERRO"
        job.erro = direct_print_unavailable_message()
        database.commit()
        return jsonify({"ok": False, "status": "ERRO", "error": direct_print_unavailable_message()}), 400
    try:
        print_label_job(database, job, printer_name=configured_printer_name(database))
        return jsonify({"ok": True, "status": job.status, "printed_at": job.printed_at.strftime("%d/%m/%Y %H:%M:%S")})
    except Exception as exc:
        return jsonify({"ok": False, "status": "ERRO", "error": str(exc)}), 500


@app.route("/api/label-jobs/<int:job_id>/save-zpl", methods=["POST"])
@login_required
@permission_required("estoque.labels.use")
def api_save_label_job(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    if not can_access_label_job(job, current_user()):
        return jsonify({"ok": False, "error": "Acesso restrito para este job."}), 403
    path = prepare_label_job_file(database, job)
    return jsonify({"ok": True, "path": str(path), "download_url": url_for("download_label_job_zpl", job_id=job.id)})


@app.route("/api/label-jobs/<int:job_id>/download-zpl")
@login_required
@permission_required("estoque.labels.use")
def download_label_job_zpl(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        flash("Job nao encontrado.", "danger")
        return redirect(url_for("inventory_labels"))
    if not can_access_label_job(job, current_user()):
        flash("Acesso restrito para este job.", "danger")
        return redirect(url_for("print_label"))
    path = prepare_label_job_file(database, job)
    return send_file(path, as_attachment=True)


@app.route("/api/label-jobs/<int:job_id>/mark-printed", methods=["POST"])
@login_required
@permission_required("estoque.labels.use")
def api_mark_printed(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    if not can_access_label_job(job, current_user()):
        return jsonify({"ok": False, "error": "Acesso restrito para este job."}), 403
    job.status = "IMPRESSO"
    job.erro = None
    job.printed_at = now_utc()
    database.commit()
    return jsonify({"ok": True, "status": job.status})


@app.route("/api/label-jobs/<int:job_id>/cancel", methods=["POST"])
@login_required
@permission_required("estoque.labels.use")
def api_cancel_job(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    if not can_access_label_job(job, current_user()):
        return jsonify({"ok": False, "error": "Acesso restrito para este job."}), 403
    job.status = "CANCELADO"
    database.commit()
    return jsonify({"ok": True, "status": job.status})


@app.route("/templates/download/<name>")
@login_required
def download_template(name):
    allowed = {
        "skus": "template_importacao_skus.xlsx",
        "exemplo": "dados_exemplo.xlsx",
        "etiquetas": "template_etiquetas_lote.xlsx",
        "baixa": "template_baixa_consumo.xlsx",
        "empenhos": "template_empenhos.xlsx",
        "bom": "template_bom.xlsx",
        "contagem_inventario": "template_contagem_inventario.xlsx",
        "somar_saldo_inventario": "template_somar_saldo_inventario.xlsx",
    }
    if name not in allowed:
        flash("Template invalido.", "danger")
        return redirect(url_for("dashboard"))
    required_permission = {
        "skus": "estoque.skus.manage",
        "exemplo": "estoque.skus.manage",
        "etiquetas": "estoque.labels.use",
        "baixa": "estoque.consumption.create",
        "empenhos": "estoque.commitment.create",
        "bom": "estoque.import",
        "contagem_inventario": "estoque.inventory.manage",
        "somar_saldo_inventario": "estoque.inventory.manage",
    }[name]
    if not can(current_user(), required_permission):
        flash("Acesso restrito para este template.", "danger")
        return redirect(url_for("dashboard"))
    path = BASE_DIR / allowed[name]
    if not path.exists():
        create_template_files(BASE_DIR)
    return send_file(path, as_attachment=True)


# Backend contract used by Suprimentos and by the receipt screen.  These routes
# are deliberately server-side only; the browser never creates a stock movement.
@app.route("/api/erp/purchase-orders", methods=["POST"])
@login_required
@permission_required("suprimentos.purchase.create")
@erp_feature_required
def erp_create_purchase_order():
    user = current_user()
    try:
        result = create_purchase_order(db(), request.get_json(silent=True) or {}, user.username)
        return jsonify({"ok": True, **result}), 201 if not result["replayed"] else 200
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/receipts/pending")
@login_required
@permission_required("estoque.inspection.receive")
@erp_feature_required
def erp_pending_receipts():
    return jsonify({"ok": True, "orders": pending_purchase_orders(db())})


@app.route("/api/erp/purchase-orders/pending-by-sku")
@login_required
@permission_required("estoque.entry.create")
@erp_feature_required
def erp_pending_purchase_orders_by_sku():
    if not po_suggestion_enabled():
        return jsonify({"ok": True, "enabled": False, "lines": []})
    sku = get_sku_by_code(db(), request.args.get("sku"), active_only=True)
    if not sku:
        return jsonify({"ok": False, "error": "COD nao cadastrado ou inativo."}), 404
    return jsonify(
        {
            "ok": True,
            "enabled": True,
            "lines": pending_purchase_order_lines_by_sku(
                db(),
                sku_id=sku.id,
                sku_code=sku.sku,
            ),
        }
    )


@app.route("/api/erp/work-orders/active")
@login_required
@permission_required("estoque.movement.view")
@erp_feature_required
def erp_active_work_orders():
    if not movement_context_feature_enabled():
        return jsonify({"ok": True, "enabled": False, "work_orders": []})
    return jsonify(
        {
            "ok": True,
            "enabled": True,
            "work_orders": active_work_orders(
                db(),
                query=request.args.get("q", ""),
                limit=request.args.get("limit", 20),
            ),
        }
    )


@app.route("/erp/recebimentos")
@login_required
@permission_required("estoque.inspection.receive")
@erp_feature_required
def erp_receipts_screen():
    return render_template("erp_recebimentos.html")


@app.route("/api/erp/receipts/confirm", methods=["POST"])
@login_required
@permission_required("estoque.inspection.receive")
@erp_feature_required
def erp_confirm_receipt():
    user = current_user()
    try:
        result = confirm_receipt(db(), request.get_json(silent=True) or {}, user.username, user.id)
        return jsonify({"ok": True, **result}), 201 if not result["replayed"] else 200
    except ValueError as exc:
        db().rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        db().rollback()
        app.logger.exception("Falha ao confirmar recebimento ERP")
        return jsonify({"ok": False, "error": "Falha transacional ao confirmar recebimento."}), 500


@app.route("/api/erp/purchase-orders/<order_id>/cancel", methods=["POST"])
@login_required
@permission_required("suprimentos.purchase.cancel")
@erp_feature_required
def erp_cancel_purchase_order(order_id):
    try:
        cancel_purchase_order(db(), order_id, current_user().username, (request.get_json(silent=True) or {}).get("motivo", ""))
        return jsonify({"ok": True})
    except ValueError as exc: return jsonify({"ok": False,"error":str(exc)}),400


@app.route("/api/erp/purchase-orders/<order_id>/technical-close", methods=["POST"])
@login_required
@permission_required("suprimentos.purchase.technical_close")
@erp_feature_required
def erp_technical_close_purchase_order(order_id):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, **close_purchase_order_technical(
            db(), order_id, current_user().username, payload.get("motivo", "")
        )})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/purchase-orders/<order_id>/financial-close", methods=["POST"])
@login_required
@permission_required("suprimentos.purchase.financial_close")
@erp_feature_required
def erp_financial_close_purchase_order(order_id):
    payload = request.get_json(silent=True) or {}
    try:
        if payload.get("lines"):
            result = register_purchase_order_financial_entry(
                db(), order_id, current_user().username, payload
            )
        else:
            result = close_purchase_order_financial(
                db(), order_id, current_user().username, payload.get("motivo", "")
            )
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/purchase-orders/<order_id>/financial-detail")
@login_required
@permission_required("suprimentos.purchase.financial_close")
@erp_feature_required
def erp_financial_detail_purchase_order(order_id):
    try:
        return jsonify({"ok": True, **purchase_order_financial_detail(db(), order_id)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@app.route("/api/erp/receipts/<receipt_id>/reverse", methods=["POST"])
@login_required
@permission_required("estoque.movement.cancel_any")
@erp_feature_required
def erp_reverse_receipt(receipt_id):
    user=current_user()
    try:
        reverse_receipt(db(), receipt_id, user.username, user.id, (request.get_json(silent=True) or {}).get("motivo", ""))
        return jsonify({"ok": True})
    except ValueError as exc: return jsonify({"ok":False,"error":str(exc)}),400


def _erp_internal_allowed():
    expected = os.environ.get("ERP_BACKEND_TOKEN", "").strip()
    supplied = request.headers.get("X-ERP-Backend-Token", "").strip()
    return bool(expected) and bool(supplied) and hmac.compare_digest(supplied, expected)


def _erp_actor_user(database, actor):
    actor = (actor or "ERP").strip()
    supplied_id = str(request.headers.get("X-ERP-Actor-ID") or "").strip()
    if supplied_id:
        if not supplied_id.isdigit():
            return None
        user = database.get(User, int(supplied_id))
        if not user or not user.active:
            return None
        if actor and actor != "ERP" and user.username != actor:
            return None
        return user
    return (
        database.query(User)
        .filter(User.username == actor, User.active.is_(True))
        .one_or_none()
    )


@app.route("/api/erp/internal/purchase-orders", methods=["POST"])
@erp_feature_required
def erp_internal_create_purchase_order():
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    database = db(); actor = request.headers.get("X-ERP-Actor", "ERP")
    try:
        return jsonify({"ok": True, **create_purchase_order(database, request.get_json(silent=True) or {}, actor)})
    except ValueError as exc: return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/internal/purchase-orders/legacy-sync", methods=["POST"])
@erp_feature_required
def erp_internal_sync_legacy_purchase_order():
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    database = db(); actor = request.headers.get("X-ERP-Actor", "ERP")
    try:
        return jsonify({"ok": True, **sync_legacy_purchase_order(database, request.get_json(silent=True) or {}, actor)})
    except ValueError as exc:
        database.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/internal/purchase-orders/legacy-cancel", methods=["POST"])
@erp_feature_required
def erp_internal_cancel_legacy_purchase_order():
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, **cancel_purchase_order_by_idempotency_key(
            db(), payload.get("idempotency_key"), request.headers.get("X-ERP-Actor", "ERP"), payload.get("motivo", "")
        )})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/internal/purchase-orders/legacy-close", methods=["POST"])
@erp_feature_required
def erp_internal_close_legacy_purchase_order():
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, **close_purchase_order_by_idempotency_key(
            db(), payload.get("idempotency_key"), request.headers.get("X-ERP-Actor", "ERP"), payload.get("motivo", "")
        )})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/internal/purchase-orders/<order_id>/technical-close", methods=["POST"])
@erp_feature_required
def erp_internal_technical_close_purchase_order(order_id):
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, **close_purchase_order_technical(
            db(), order_id, request.headers.get("X-ERP-Actor", "ERP"), payload.get("motivo", "")
        )})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/internal/purchase-orders/<order_id>/financial-close", methods=["POST"])
@erp_feature_required
def erp_internal_financial_close_purchase_order(order_id):
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    payload = request.get_json(silent=True) or {}
    try:
        if payload.get("lines"):
            result = register_purchase_order_financial_entry(
                db(), order_id, request.headers.get("X-ERP-Actor", "ERP"), payload
            )
        else:
            result = close_purchase_order_financial(
                db(), order_id, request.headers.get("X-ERP-Actor", "ERP"), payload.get("motivo", "")
            )
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/erp/internal/purchase-orders/<order_id>/financial-detail")
@erp_feature_required
def erp_internal_financial_detail_purchase_order(order_id):
    if not _erp_internal_allowed():
        return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    try:
        return jsonify({"ok": True, **purchase_order_financial_detail(db(), order_id)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@app.route("/api/erp/internal/receipts/pending")
@erp_feature_required
def erp_internal_pending_receipts():
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    return jsonify({"ok": True, "orders": pending_purchase_orders(db())})


@app.route("/api/erp/internal/dashboard")
@erp_feature_required
def erp_internal_dashboard():
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    return jsonify({"ok": True, **purchase_orders_dashboard(db())})


@app.route("/api/erp/internal/work-orders/<work_order_id>/materials")
@erp_feature_required
def erp_internal_work_order_materials(work_order_id):
    if not _erp_internal_allowed():
        return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    try:
        return jsonify({"ok": True, **work_order_materials(db(), work_order_id)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@app.route("/api/erp/internal/reports/purchases-inspections.xlsx")
@erp_feature_required
def erp_internal_purchase_inspection_report():
    if not _erp_internal_allowed():
        return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    output, _, _ = build_purchase_inspection_report(db())
    return send_file(
        output,
        as_attachment=True,
        download_name="Compras_Bancos_e_Inspecao_Recebimento.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/erp/relatorios/compras-inspecao.xlsx")
@login_required
@permission_required("estoque.reports.view")
@erp_feature_required
def erp_purchase_inspection_report():
    output, _, _ = build_purchase_inspection_report(db())
    return send_file(
        output,
        as_attachment=True,
        download_name="Compras_Bancos_e_Inspecao_Recebimento.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/erp/internal/receipts/confirm", methods=["POST"])
@erp_feature_required
def erp_internal_confirm_receipt():
    if not _erp_internal_allowed(): return jsonify({"ok": False, "error": "Servico nao autorizado."}), 401
    database = db(); actor = request.headers.get("X-ERP-Actor", "ERP"); user = _erp_actor_user(database, actor)
    if not user: return jsonify({"ok": False, "error": "Usuario operacional nao encontrado."}), 409
    try:
        return jsonify({"ok": True, **confirm_receipt(database, request.get_json(silent=True) or {}, actor, user.id)})
    except ValueError as exc:
        database.rollback(); return jsonify({"ok": False, "error": str(exc)}), 400


if __name__ == "__main__":
    create_template_files(BASE_DIR)
    app.run(host="127.0.0.1", port=5000, debug=False)
