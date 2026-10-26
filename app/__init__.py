"""MatiPanel - Administrador de servidores Linux."""
from flask import Flask, request, abort
from flask_login import LoginManager
from flask_sock import Sock
from .auth import auth_bp, load_user
from .modules import register_modules
from .util import same_origin
import os
import secrets


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    secret_path = os.path.expanduser("~/.matipanel_secret")
    if os.path.exists(secret_path):
        with open(secret_path, "r") as f:
            app.config["SECRET_KEY"] = f.read().strip()
    else:
        key = secrets.token_hex(32)
        try:
            with open(secret_path, "w") as f:
                f.write(key)
            os.chmod(secret_path, 0o600)
        except OSError:
            pass
        app.config["SECRET_KEY"] = key

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("MATIPANEL_SECURE_COOKIE") == "1"
    app.config["PERMANENT_SESSION_LIFETIME"] = 3600
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # subidas y restauraciones: 1 GB
    app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 25}

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Por favor inicia sesión para continuar."

    @login_manager.user_loader
    def _load_user(user_id):
        return load_user(user_id)

    @app.before_request
    def _csrf_origin_check():
        # Los navegadores mandan Origin en todo POST; si no coincide con el host, es CSRF.
        if request.method in ("POST", "PUT", "DELETE") and request.headers.get("Origin")                 and not same_origin():
            abort(403)

    sock = Sock(app)

    app.register_blueprint(auth_bp)
    register_modules(app, sock)

    return app
