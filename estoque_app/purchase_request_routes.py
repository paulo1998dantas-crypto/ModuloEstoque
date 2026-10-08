"""Stock entry + authenticated internal interface consumed by Suprimentos."""
import secrets
import hmac
from flask import Blueprint, jsonify, request, session, render_template, current_app, send_file
from purchase_request_export import build_workbook, XLSX_MIME
from sqlalchemy import or_
from services import purchase_requests as workflow
from services.erp_service import active_work_orders
from models import SKU

def register(app, get_db, get_user, internal_allowed, internal_user, feature_required, login_required):
    bp = Blueprint("purchase_requests", __name__)

    def token():
        if not session.get("purchase_requests_csrf"):
            session["purchase_requests_csrf"] = secrets.token_urlsafe(32)
        return session["purchase_requests_csrf"]

    def csrf():
        supplied = str(request.headers.get("X-CSRF-Token") or "")
        expected = session.get("purchase_requests_csrf", "")
        if not expected or not hmac.compare_digest(supplied, expected):
            raise PermissionError("Sessão de solicitação expirada. Recarregue a página.")

    def access():
        internal = request.path.startswith("/api/erp/internal/")
        if internal:
            if not internal_allowed():
                raise PermissionError("Serviço não autorizado.")
            user = internal_user(get_db(), request.headers.get("X-ERP-Actor", ""))
        else:
            user = get_user()
        if not user or not user.active:
            raise PermissionError("Autenticação obrigatória.")
        return user

    def execute(callback, write=False, commit=False, download=False):
        database = get_db()
        try:
            user = access()
            if write and not request.path.startswith("/api/erp/internal/"): csrf()
            result = callback(database, user)
            if write or commit: database.commit()
            if download:
                response = send_file(result, as_attachment=True, mimetype=XLSX_MIME,
                                     download_name="Solicitacoes_de_compra.xlsx", max_age=0)
                response.headers["Cache-Control"] = "no-store"
                return response
            return jsonify(ok=True, **result)
        except PermissionError as exc:
            database.rollback()
            return jsonify(ok=False, error=str(exc)), 403
        except ValueError as exc:
            database.rollback()
            return jsonify(ok=False, error=str(exc)), 503 if "migra" in str(exc).lower() else 400
        except Exception:
            database.rollback()
            current_app.logger.exception("Falha no workflow de solicitações")
            return jsonify(ok=False, error="Workflow temporariamente indisponível; nenhuma conclusão foi confirmada."), 503

    @bp.route("/solicitacoes")
    @login_required
    @feature_required
    def screen():
        user = get_user()
        can_submit = False
        try:
            workflow.require_origin(get_db(), user, "ESTOQUE")
            can_submit = True
        except PermissionError: can_submit = False
        return render_template("purchase_requests.html", request_config={
            "api": "/api/erp/purchase-requests", "origin": "ESTOQUE",
            "can_submit": can_submit, "can_edit_origin": can_submit,
            "can_manage": False, "user_id": user.id,
            "csrf": token(),
            "purchases_url": (app.config.get("ERP_SUPRIMENTOS_URL", "") or
                __import__("config").Config.ERP_SUPRIMENTOS_URL).rstrip("/") + "/erp/solicitacoes"})

    @bp.route("/api/erp/purchase-requests", methods=["GET", "POST"])
    @bp.route("/api/erp/internal/purchase-requests", methods=["GET", "POST"])
    @feature_required
    def collection():
        if request.method == "GET":
            return execute(lambda db, user: workflow.listing(db, request.args, user), commit=True)
        origin = "PCP" if request.path.startswith("/api/erp/internal/") else "ESTOQUE"
        return execute(lambda db, user: workflow.create(db, request.get_json(silent=True) or {}, user, origin), True)

    @bp.route("/api/erp/purchase-requests/export.xlsx")
    @bp.route("/api/erp/internal/purchase-requests/export.xlsx")
    @feature_required
    def export_excel():
        return execute(lambda db, user: build_workbook(
            workflow.export_data(db, request.args, user), request.args, user.username),
            commit=True, download=True)

    @bp.route("/api/erp/purchase-requests/options")
    @bp.route("/api/erp/internal/purchase-requests/options")
    @feature_required
    def options():
        def lookup(db, user):
            q = str(request.args.get("q") or "").strip()[:150]
            rows = db.query(SKU).filter(SKU.active.is_(True))
            if q: rows = rows.filter(or_(SKU.sku.ilike("%"+q+"%"), SKU.descricao.ilike("%"+q+"%")))
            return {"items": [{"sku_codigo": r.sku, "descricao": r.descricao, "unidade": r.unidade or "UN"}
                              for r in rows.order_by(SKU.sku).limit(40)]}
        return execute(lookup)

    @bp.route("/api/erp/purchase-requests/work-orders")
    @bp.route("/api/erp/internal/purchase-requests/work-orders")
    @feature_required
    def work_order_options():
        def lookup(db, user):
            if not workflow.buyer_allowed(db, user):
                allowed = False
                for origin in ("ESTOQUE", "PCP"):
                    try:
                        workflow.require_origin(db, user, origin)
                        allowed = True
                        break
                    except PermissionError:
                        pass
                if not allowed:
                    raise PermissionError("Seu perfil não pode consultar O.S. para solicitações.")
            rows = active_work_orders(db, request.args.get("q") or "", limit=50)
            return {"items": [{"id": str(row["work_order_id"]),
                               "numero_os": row.get("numero_os"),
                               "item_number": row.get("item_number"),
                               "label": row.get("label")}
                              for row in rows]}
        return execute(lookup)

    @bp.route("/api/erp/purchase-requests/notifications")
    @bp.route("/api/erp/internal/purchase-requests/notifications")
    @feature_required
    def notifications():
        return execute(lambda db, user: workflow.notifications(db))

    @bp.route("/api/erp/internal/purchase-requests/prepare", methods=["POST"])
    @feature_required
    def prepare():
        return execute(lambda db, user: workflow.prepare(db, (request.get_json(silent=True) or {}).get("ids"), user), True)

    @bp.route("/api/erp/purchase-requests/<request_id>/history")
    @bp.route("/api/erp/internal/purchase-requests/<request_id>/history")
    @feature_required
    def history(request_id):
        return execute(lambda db, user: workflow.history(db, request_id))

    @bp.route("/api/erp/purchase-requests/<request_id>/orders")
    @bp.route("/api/erp/internal/purchase-requests/<request_id>/orders")
    @feature_required
    def order_options(request_id):
        return execute(lambda db, user: workflow.existing_order_options(db, request_id, user))

    @bp.route("/api/erp/internal/purchase-requests/<request_id>/action", methods=["POST"])
    @feature_required
    def action(request_id):
        return execute(lambda db, user: workflow.transition(db, request_id, request.get_json(silent=True) or {}, user), True)

    @bp.route("/api/erp/purchase-requests/<request_id>/action", methods=["POST"])
    @login_required
    @feature_required
    def user_action(request_id):
        return execute(lambda db, user: workflow.transition(db, request_id, request.get_json(silent=True) or {}, user), True)

    app.register_blueprint(bp)

