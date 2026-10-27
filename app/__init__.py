"""MatiPanel - Administrador de servidores Linux.

Dedicado a Matías Aceituno, la inspiración de este proyecto. 💙
"""
__version__ = "0.6.0"

from flask import Flask, request, abort, send_from_directory
from flask_login import LoginManager, current_user
from flask_sock import Sock
from .auth import auth_bp, load_user
from .modules import register_modules
from .util import same_origin
from . import prefs as user_prefs
import os
from urllib.parse import urlparse
import secrets

try:
    from gevent import Timeout as _Timeout
except ImportError:  # desarrollo sin gevent: sin tiempo máximo
    class _Timeout(Exception):
        def __init__(self, seconds=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False


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
    secure = os.environ.get("MATIPANEL_SECURE_COOKIE") == "1"
    app.config["SESSION_COOKIE_SECURE"] = secure
    # __Host-: el navegador exige HTTPS, Path=/ y sin Domain, así otro sitio o subdominio
    # no puede fijar ni leer esta cookie. En desarrollo (HTTP) se usa un nombre normal.
    app.config["SESSION_COOKIE_NAME"] = "__Host-matipanel" if secure else "matipanel_session"
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

    # El webhook de despliegues lo llama un runner, no un navegador: no trae Origin ni sesión.
    # Se protege con su propia firma HMAC (X-Hub-Signature-256), tope de 1 MB y límite por IP.
    WEBHOOK_PREFIX = "/despliegues/webhook/"

    @app.route("/favicon.ico")
    def favicon():
        # Pedido por los navegadores en cualquier página (también sin sesión y en páginas de error)
        return send_from_directory(app.static_folder, "favicon.ico", mimetype="image/x-icon", max_age=86400)

    @app.before_request
    def _limit_anonymous_body():
        # El límite de 1 GB es para subidas de usuarios ya autenticados; sin sesión
        # (login, 2FA, API remota) basta con 64 KB. El webhook lee su cuerpo con su propio tope.
        # GET/HEAD primero: así no se carga el usuario (pwd/grp) en cada archivo estático.
        if request.method in ("GET", "HEAD", "OPTIONS") or request.path.startswith(WEBHOOK_PREFIX):
            return
        if current_user.is_authenticated:
            return
        if request.headers.get("Transfer-Encoding"):
            abort(411)  # sin Content-Length (chunked) el tope de 64 KB no se podría aplicar
        if (request.content_length or 0) > 64 * 1024:
            abort(413)
        if request.content_length:
            # Leer el cuerpo aquí, con tiempo máximo: un cliente que lo envía byte a byte no puede
            # retener el único worker, y las vistas (login/2FA) ya no ceden el control al leerlo.
            try:
                with _Timeout(15):
                    request.get_data(cache=True)
            except _Timeout:
                abort(408)

    @app.before_request
    def _csrf_origin_check():
        # Toda petición que modifica algo debe venir del propio panel. Señales del navegador,
        # en orden: Sec-Fetch-Site (la pone el navegador y una página ajena no puede falsearla),
        # Origin y, si falta o es "null", Referer. Sin ninguna señal válida se rechaza.
        # GET/HEAD nunca modifican nada en el panel.
        if request.method not in ("POST", "PUT", "PATCH", "DELETE") or request.path.startswith(WEBHOOK_PREFIX):
            return
        site = request.headers.get("Sec-Fetch-Site", "")
        if site in ("cross-site", "same-site"):
            return _csrf_reject(f"Sec-Fetch-Site={site}")
        if site == "same-origin":
            return
        origin = request.headers.get("Origin", "")
        if not origin or origin == "null":
            origin = request.headers.get("Referer", "")
        if not origin or urlparse(origin).netloc != request.host:
            return _csrf_reject(f"Origin/Referer={origin or '(ninguno)'} host={request.host}")

    def _csrf_reject(reason):
        app.logger.warning("Petición rechazada por protección CSRF (%s %s): %s",
                           request.method, request.path, reason)
        abort(403)

    @app.context_processor
    def _inject_prefs():
        # Apariencia del usuario disponible en todas las plantillas
        p = user_prefs.get(current_user.username if current_user.is_authenticated else None)
        return {"prefs": p, "prefs_css": user_prefs.css(p)}

    @app.after_request
    def _security_headers(resp):
        # CSP: los scripts del panel son propios o en línea; se prohíbe cargar código externo,
        # enviar datos a otros orígenes (connect/form) y mostrar el panel dentro de otra página.
        resp.headers.setdefault("Content-Security-Policy", "; ".join([
            "default-src 'self'", "script-src 'self' 'unsafe-inline'", "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:", "font-src 'self'",
            f"connect-src 'self' wss://{request.host} ws://{request.host}", "object-src 'none'",
            "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'"]))
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")  # no-referrer haría que los formularios manden Origin: null
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if resp.mimetype == "application/json":  # datos del servidor: que no queden en caché
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    sock = Sock(app)

    app.register_blueprint(auth_bp)
    register_modules(app, sock)

    return app
