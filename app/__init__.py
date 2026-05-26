"""MatiPanel - Administrador de servidores Linux."""
from flask import Flask
from flask_login import LoginManager
from flask_sock import Sock
from .auth import auth_bp, User, load_user
from .modules import register_modules
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
    app.config["PERMANENT_SESSION_LIFETIME"] = 3600
    app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 25}

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Por favor inicia sesión para continuar."

    @login_manager.user_loader
    def _load_user(user_id):
        return load_user(user_id)

    sock = Sock(app)

    app.register_blueprint(auth_bp)
    register_modules(app, sock)

    return app
