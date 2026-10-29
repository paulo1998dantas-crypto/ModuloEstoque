"""Server-to-server interface; no public unauthenticated ledger access."""
from flask import Blueprint, current_app, jsonify, request
from auth import can, effective_roles
from services import external_stock


def register(app, get_db, internal_allowed, internal_user, feature_required):
    bp = Blueprint('external_stock', __name__)

    @bp.route('/api/erp/internal/external-stock', methods=['GET', 'POST'])
    @feature_required
    def ledger():
        if not internal_allowed():
            return jsonify(ok=False, error='Serviço não autorizado.'), 401
        database = get_db()
        try:
            user = internal_user(database, request.headers.get('X-ERP-Actor', ''))
            if not user or not user.active:
                raise PermissionError('Usuário operacional não encontrado.')
            roles = effective_roles(user, database)
            if not roles & {'ADMIN', 'PCP'} or not can(user, 'suprimentos.work_order.view', database):
                raise PermissionError('Baixa externa disponível somente para PCP e ADMIN.')
            if request.method == 'GET':
                return jsonify(ok=True, **external_stock.summary(database,
                    sku=request.args.get('sku', '').strip(), page=request.args.get('page', 1)))
            if not can(user, 'suprimentos.work_order.manage', database):
                raise PermissionError('Usuário sem permissão para movimentar estoque externo.')
            result = external_stock.manual_movement(database, request.get_json(silent=True) or {}, user.username, user.id)
            database.commit()
            return jsonify(ok=True, **result)
        except PermissionError as exc:
            database.rollback()
            return jsonify(ok=False, error=str(exc)), 403
        except (ValueError, TypeError) as exc:
            database.rollback()
            return jsonify(ok=False, error=str(exc)), 400
        except Exception:
            database.rollback()
            current_app.logger.exception('Falha no estoque externo')
            return jsonify(ok=False, error='Estoque externo indisponível; nenhum lançamento foi confirmado.'), 503

    app.register_blueprint(bp)
