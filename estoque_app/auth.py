from functools import wraps

from flask import flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from database import SessionLocal
from models import AppSetting, User


DEFAULT_SETTINGS = {
    "allow_negative_stock": "false",
    "operator_can_export": "true",
    "default_printer_name": Config.DEFAULT_PRINTER_NAME,
    "admin_can_print_inactive_sku": "false",
}


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    db = SessionLocal()
    return db.get(User, user_id)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or user.role not in roles:
                flash("Acesso restrito para este perfil.", "danger")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def ensure_initial_data():
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin = User(
                username=Config.DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(Config.DEFAULT_ADMIN_PASSWORD),
                role="ADM",
                active=True,
            )
            db.add(admin)

        for key, value in DEFAULT_SETTINGS.items():
            existing = db.query(AppSetting).filter_by(key=key).one_or_none()
            if existing is None:
                db.add(AppSetting(key=key, value=value))

        db.commit()
    finally:
        db.close()
