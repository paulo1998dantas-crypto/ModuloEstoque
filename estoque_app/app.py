import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

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
from sqlalchemy import or_

from auth import (
    current_user,
    ensure_initial_data,
    hash_password,
    login_required,
    roles_required,
    verify_password,
)
from config import APP_ROOT, BASE_DIR, Config, EXPORTS_DIR, LOGS_DIR
from database import SessionLocal, init_db
from models import InventoryCount, LabelPrintJob, Movement, SKU, StockBalance, User, now_utc
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
    export_stock_report,
    import_label_jobs_from_excel,
    import_skus_from_excel,
    label_queue_summary,
)
from services.estoque_service import (
    adjust_balance_to_count,
    close_inventory_and_adjust,
    dashboard_movement_cache,
    decimal_to_str,
    get_active_inventory_session,
    get_setting,
    get_setting_bool,
    get_sku_by_code,
    inventory_stats,
    normalize_sku,
    open_inventory_session,
    optional_decimal_to_str,
    register_movement,
    reset_operational_data,
    save_inventory_count,
    set_setting,
    to_decimal,
    to_optional_decimal,
    create_or_update_sku,
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


@app.teardown_appcontext
def remove_session(exception=None):
    SessionLocal.remove()


@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "fmt_qty": decimal_to_str,
        "fmt_min": optional_decimal_to_str,
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


def user_can_export(database, user):
    if not user:
        return False
    return user.role == "ADM" or get_setting_bool(database, "operator_can_export", True)


def can_print_sku(database, sku, user):
    if sku.active:
        return True
    return bool(user and user.role == "ADM" and get_setting_bool(database, "admin_can_print_inactive_sku", False))


def can_access_label_job(job, user):
    return bool(user and (user.role == "ADM" or job.usuario_id == user.id))


def is_loopback_request():
    remote_addr = request.remote_addr or ""
    return remote_addr in {"127.0.0.1", "::1", "localhost"}


def stock_rows(database, filters):
    query = database.query(SKU).outerjoin(StockBalance)
    if filters.get("sku"):
        query = query.filter(SKU.sku.ilike(f"%{filters['sku']}%"))
    if filters.get("descricao"):
        query = query.filter(SKU.descricao.ilike(f"%{filters['descricao']}%"))
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
    return query.order_by(SKU.sku).all()


@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        database = db()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = database.query(User).filter_by(username=username).one_or_none()
        if user and user.active and verify_password(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            flash("Login realizado com sucesso.", "success")
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Usuario ou senha invalidos.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sessao encerrada.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
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
@roles_required("ADM")
def users():
    database = db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "OPERADOR")
        if not username or not password:
            flash("Informe usuario e senha.", "danger")
        elif database.query(User).filter_by(username=username).one_or_none():
            flash("Usuario ja existe.", "danger")
        else:
            database.add(User(username=username, password_hash=hash_password(password), role=role, active=True))
            database.commit()
            flash("Usuario criado.", "success")
        return redirect(url_for("users"))
    return render_template("users.html", users=database.query(User).order_by(User.username).all())


@app.route("/usuarios/<int:user_id>/toggle", methods=["POST"])
@login_required
@roles_required("ADM")
def toggle_user(user_id):
    database = db()
    user = database.get(User, user_id)
    if user and user.id != session.get("user_id"):
        user.active = not user.active
        database.commit()
        flash("Status do usuario atualizado.", "success")
    return redirect(url_for("users"))


@app.route("/configuracoes", methods=["GET", "POST"])
@login_required
@roles_required("ADM")
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
        "default_printer_name": get_setting(database, "default_printer_name", ""),
    }
    return render_template("settings.html", values=values)


@app.route("/skus")
@login_required
@roles_required("ADM")
def skus():
    database = db()
    term = request.args.get("q", "").strip()
    query = database.query(SKU)
    if term:
        query = query.filter(or_(SKU.sku.ilike(f"%{term}%"), SKU.descricao.ilike(f"%{term}%")))
    return render_template("skus.html", skus=query.order_by(SKU.sku).limit(500).all(), q=term)


@app.route("/skus/novo", methods=["GET", "POST"])
@app.route("/skus/<int:sku_id>/editar", methods=["GET", "POST"])
@login_required
@roles_required("ADM")
def sku_form(sku_id=None):
    database = db()
    sku = database.get(SKU, sku_id) if sku_id else None
    if request.method == "POST":
        try:
            data = {
                "sku": request.form.get("sku"),
                "descricao": request.form.get("descricao"),
                "unidade": request.form.get("unidade"),
                "categoria": request.form.get("categoria"),
                "localizacao": request.form.get("localizacao"),
                "estoque_minimo": request.form.get("estoque_minimo"),
                "active": bool(request.form.get("active")),
            }
            if sku:
                existing = get_sku_by_code(database, data["sku"])
                if existing and existing.id != sku.id:
                    raise ValueError("SKU ja cadastrado.")
                sku.sku = normalize_sku(data["sku"])
                sku.descricao = data["descricao"].strip()
                sku.unidade = data["unidade"].strip() or None
                sku.categoria = data["categoria"].strip() or None
                sku.localizacao = data["localizacao"].strip() or None
                sku.estoque_minimo = to_optional_decimal(data["estoque_minimo"])
                sku.active = data["active"]
                database.commit()
            else:
                create_or_update_sku(database, data)
            flash("SKU salvo com sucesso.", "success")
            return redirect(url_for("skus"))
        except Exception as exc:
            database.rollback()
            flash(str(exc), "danger")
    return render_template("sku_form.html", sku=sku)


@app.route("/skus/importar", methods=["GET", "POST"])
@login_required
@roles_required("ADM")
def import_skus():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename.lower().endswith(".xlsx"):
            flash("Envie um arquivo .xlsx.", "danger")
            return redirect(url_for("import_skus"))
        database = db()
        result = import_skus_from_excel(database, file)
        flash(f"Importacao concluida: {result['created']} criados, {result['updated']} atualizados.", "success")
        for error in result["errors"][:10]:
            flash(error, "warning")
        if len(result["errors"]) > 10:
            flash(f"Mais {len(result['errors']) - 10} erros ocultos.", "warning")
        return redirect(url_for("import_skus"))
    return render_template("import_skus.html")


@app.route("/estoque")
@login_required
def stock():
    database = db()
    filters = {
        "sku": request.args.get("sku", "").strip(),
        "descricao": request.args.get("descricao", "").strip(),
        "categoria": request.args.get("categoria", "").strip(),
        "localizacao": request.args.get("localizacao", "").strip(),
        "saldo_baixo": request.args.get("saldo_baixo", ""),
        "active": request.args.get("active", "1"),
    }
    return render_template("stock.html", rows=stock_rows(database, filters), filters=filters, can_export=user_can_export(database, current_user()))


@app.route("/estoque/exportar")
@login_required
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
def entrada():
    database = db()
    sku = None
    sku_code = request.args.get("sku", "").strip()
    if request.method == "POST":
        try:
            sku = get_sku_by_code(database, request.form.get("sku"), active_only=True)
            if not sku:
                raise ValueError("SKU nao cadastrado ou inativo. Entrada bloqueada.")
            register_movement(
                database,
                sku,
                "ENTRADA",
                request.form.get("quantidade"),
                session["user_id"],
                documento=request.form.get("documento", ""),
                observacao=request.form.get("observacao", ""),
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
            flash("SKU nao cadastrado ou inativo. Entrada bloqueada.", "danger")
    return render_template("movement_form.html", mode="entrada", sku=sku, sku_code=sku_code)


@app.route("/saida", methods=["GET", "POST"])
@login_required
def saida():
    database = db()
    sku = None
    sku_code = request.args.get("sku", "").strip()
    if request.method == "POST":
        try:
            sku = get_sku_by_code(database, request.form.get("sku"), active_only=True)
            if not sku:
                raise ValueError("SKU nao cadastrado ou inativo. Saida bloqueada.")
            register_movement(
                database,
                sku,
                "SAIDA",
                request.form.get("quantidade"),
                session["user_id"],
                documento=request.form.get("documento", ""),
                observacao=request.form.get("observacao", ""),
                allow_negative=get_setting_bool(database, "allow_negative_stock", False),
            )
            flash("Saida registrada com sucesso.", "success")
            return redirect(url_for("saida"))
        except Exception as exc:
            database.rollback()
            flash(str(exc), "danger")
            sku_code = request.form.get("sku", "")
    if sku_code:
        sku = get_sku_by_code(database, sku_code, active_only=True)
        if not sku:
            flash("SKU nao cadastrado ou inativo. Saida bloqueada.", "danger")
    return render_template("movement_form.html", mode="saida", sku=sku, sku_code=sku_code)


@app.route("/inventario-mobile")
@login_required
def inventory_mobile_legacy():
    return redirect(url_for("inventory_mobile"))


@app.route("/inventario", methods=["GET", "POST"])
@login_required
def inventory_mobile():
    database = db()
    user = current_user()
    sku = None
    sku_code = request.args.get("sku", "").strip()

    if request.method == "POST":
        try:
            sku = get_sku_by_code(database, request.form.get("sku"), active_only=True)
            if not sku:
                raise ValueError("SKU nao cadastrado ou inativo.")
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
            flash("SKU nao cadastrado ou inativo.", "danger")

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
def print_label():
    database = db()
    user = current_user()
    sku = None
    sku_code = request.values.get("sku", "").strip()
    if sku_code:
        sku = get_sku_by_code(database, sku_code)
        if not sku:
            flash("SKU nao cadastrado.", "danger")
        elif not can_print_sku(database, sku, user):
            flash("SKU inativo. Impressao bloqueada.", "danger")
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
                print_label_job(database, job, printer_name=get_setting(database, "default_printer_name", ""))
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
def api_label_zpl():
    database = db()
    user = current_user()
    payload = request.get_json(silent=True) or request.form
    sku = get_sku_by_code(database, payload.get("sku"))
    if not sku:
        return jsonify({"ok": False, "error": "SKU nao cadastrado."}), 404
    if not can_print_sku(database, sku, user):
        return jsonify({"ok": False, "error": "SKU inativo. Impressao bloqueada."}), 400
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
def movements():
    database = db()
    user = current_user()
    tipo = request.args.get("tipo", "")
    query = database.query(Movement)
    if user.role != "ADM":
        query = query.filter(Movement.tipo.in_(["ENTRADA", "SAIDA"]))
    if tipo:
        query = query.filter(Movement.tipo == tipo)
    rows = query.order_by(Movement.created_at.desc()).limit(500).all()
    return render_template("movements.html", movements=rows, tipo=tipo, can_export=user_can_export(database, user))


@app.route("/relatorios")
@login_required
def reports():
    database = db()
    return render_template("reports.html", can_export=user_can_export(database, current_user()))


@app.route("/relatorios/exportar/<tipo>")
@login_required
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
    elif tipo == "saidas":
        path = export_movements_report(database, user, "SAIDA")
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
@roles_required("ADM")
def backup():
    path = create_backup()
    flash(f"Backup gerado: {path}", "success")
    return redirect(url_for("settings"))


@app.route("/resetar-dados", methods=["POST"])
@login_required
@roles_required("ADM")
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
@roles_required("ADM")
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
                    raise ValueError("SKU nao cadastrado.")
                if not can_print_sku(database, sku, user):
                    raise ValueError("SKU inativo. Impressao bloqueada.")
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
                flash(f"{created} SKU(s) adicionados a fila.", "success")

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
                    raise ValueError("Envie uma planilha .xlsx com SKU e QUANTIDADE.")
                result = import_label_jobs_from_excel(database, file, user.id, active_session.id if active_session else None)
                flash(f"Importacao da fila concluida: {result['created']} registros criados.", "success")
                for error in result["errors"][:10]:
                    flash(error, "warning")

            elif action == "save_all_zpl":
                jobs = (
                    database.query(LabelPrintJob)
                    .filter(LabelPrintJob.status.in_(["PENDENTE", "ERRO"]))
                    .order_by(LabelPrintJob.created_at)
                    .all()
                )
                if active_session:
                    jobs = [job for job in jobs if job.inventory_session_id == active_session.id]
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
                    raise ValueError("SKU nao cadastrado ou inativo.")
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
                if active_session:
                    query = query.filter(LabelPrintJob.inventory_session_id == active_session.id)
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
    if active_session:
        jobs_query = jobs_query.filter(LabelPrintJob.inventory_session_id == active_session.id)
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
        queue_summary=label_queue_summary(database, active_session.id if active_session else None),
        skus_for_selection=skus_for_selection,
    )


@app.route("/inventario/exportar-previa")
@login_required
@roles_required("ADM")
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
        printer_name = (payload.get("printer_name") or "").strip() or get_setting(database, "default_printer_name", "")
        target_printer = print_zpl(zpl, printer_name=printer_name)
        response = jsonify({"ok": True, "printer": target_printer})
        return add_bridge_cors_headers(response)
    except Exception as exc:
        response = jsonify({"ok": False, "error": str(exc)})
        return add_bridge_cors_headers(response), 500


@app.route("/api/label-jobs/<int:job_id>/zpl", methods=["POST"])
@login_required
def api_label_job_zpl(job_id):
    database = db()
    user = current_user()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    if not can_access_label_job(job, user):
        return jsonify({"ok": False, "error": "Acesso restrito para este job."}), 403
    if not can_print_sku(database, job.sku, user):
        return jsonify({"ok": False, "error": "SKU inativo. Impressao bloqueada."}), 400
    path = prepare_label_job_file(database, job)
    zpl = Path(path).read_text(encoding="utf-8")
    return jsonify({"ok": True, "job_id": job.id, "zpl": zpl, "path": str(path)})


@app.route("/api/label-jobs/<int:job_id>/local-result", methods=["POST"])
@login_required
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
    else:
        job.status = "ERRO"
        job.erro = payload.get("error") or local_bridge_unavailable_message()
    database.commit()
    return jsonify({"ok": True, "status": job.status, "printed_at": job.printed_at.strftime("%d/%m/%Y %H:%M:%S") if job.printed_at else ""})


@app.route("/api/label-jobs/<int:job_id>/print", methods=["POST"])
@login_required
@roles_required("ADM")
def api_print_label_job(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    if not can_print_sku(database, job.sku, current_user()):
        return jsonify({"ok": False, "error": "SKU inativo. Impressao bloqueada."}), 400
    if request_print_mode() != "server":
        job.status = "ERRO"
        job.erro = direct_print_unavailable_message()
        database.commit()
        return jsonify({"ok": False, "status": "ERRO", "error": direct_print_unavailable_message()}), 400
    try:
        print_label_job(database, job, printer_name=get_setting(database, "default_printer_name", ""))
        return jsonify({"ok": True, "status": job.status, "printed_at": job.printed_at.strftime("%d/%m/%Y %H:%M:%S")})
    except Exception as exc:
        return jsonify({"ok": False, "status": "ERRO", "error": str(exc)}), 500


@app.route("/api/label-jobs/<int:job_id>/save-zpl", methods=["POST"])
@login_required
@roles_required("ADM")
def api_save_label_job(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    path = prepare_label_job_file(database, job)
    return jsonify({"ok": True, "path": str(path), "download_url": url_for("download_label_job_zpl", job_id=job.id)})


@app.route("/api/label-jobs/<int:job_id>/download-zpl")
@login_required
@roles_required("ADM")
def download_label_job_zpl(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        flash("Job nao encontrado.", "danger")
        return redirect(url_for("inventory_labels"))
    path = prepare_label_job_file(database, job)
    return send_file(path, as_attachment=True)


@app.route("/api/label-jobs/<int:job_id>/mark-printed", methods=["POST"])
@login_required
@roles_required("ADM")
def api_mark_printed(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
    job.status = "IMPRESSO"
    job.erro = None
    job.printed_at = now_utc()
    database.commit()
    return jsonify({"ok": True, "status": job.status})


@app.route("/api/label-jobs/<int:job_id>/cancel", methods=["POST"])
@login_required
@roles_required("ADM")
def api_cancel_job(job_id):
    database = db()
    job = database.get(LabelPrintJob, job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job nao encontrado."}), 404
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
    }
    if name not in allowed:
        flash("Template invalido.", "danger")
        return redirect(url_for("dashboard"))
    path = BASE_DIR / allowed[name]
    if not path.exists():
        create_template_files(BASE_DIR)
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    create_template_files(BASE_DIR)
    app.run(host="127.0.0.1", port=5000, debug=False)
