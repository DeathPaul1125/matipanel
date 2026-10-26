"""Autenticación PAM + 2FA TOTP opcional."""
import grp
import pwd
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import UserMixin, login_user, logout_user, login_required, current_user
from . import totp
from .util import RateLimiter

auth_bp = Blueprint("auth", __name__)
ALLOWED_GROUPS = ("matipanel", "sudo", "wheel", "admin")
limiter = RateLimiter(max_fails=5, window=300)


class User(UserMixin):
    def __init__(self, username):
        self.id = username
        self.username = username


def load_user(user_id):
    # Revalida en cada request: sacar al usuario del grupo revoca su sesión.
    return User(user_id) if _user_is_allowed(user_id) else None


def _user_is_allowed(username):
    try:
        user_info = pwd.getpwnam(username)
    except KeyError:
        return False
    user_groups = [g.gr_name for g in grp.getgrall() if username in g.gr_mem]
    try:
        primary = grp.getgrgid(user_info.pw_gid).gr_name
        user_groups.append(primary)
    except KeyError:
        pass
    return any(g in ALLOWED_GROUPS for g in user_groups)


def _authenticate_pam(username, password):
    try:
        import pam
    except ImportError:
        return False
    # pam.pam() localiza libpam con un subprocess (ldconfig): debe crearse en el hilo
    # principal; gevent no permite subprocesos desde su threadpool.
    p = pam.pam()

    def check():
        return p.authenticate(username, password, service="matipanel")
    # PAM bloquea ~2 s al fallar; en un hilo real para no congelar el worker gevent.
    try:
        from gevent import get_hub, monkey
        if monkey.is_module_patched("socket"):
            return get_hub().threadpool.apply(check)
    except ImportError:
        pass
    return check()


@auth_bp.errorhandler(totp.StoreError)
def _tfa_store_error(e):
    # Falla cerrado: si 2fa.json existe pero no se puede leer, nadie entra.
    flash(str(e), "error")
    if current_user.is_authenticated:
        return render_template("security.html", tfa_enabled=False,
                               has_pyotp=totp.HAS_PYOTP, tfa_error=True), 500
    session.pop("pending_2fa_user", None)
    return render_template("login.html"), 500


def _is_recovery_code(code):
    return len(code) == 8 and all(c in "0123456789abcdef" for c in code.lower())


def _check_second_factor(username, code):
    """Valida un código TOTP o de recuperación (este último se consume)."""
    is_recovery = _is_recovery_code(code)
    ok = totp.consume_recovery(username, code) if is_recovery else totp.verify(username, code)
    return ok, is_recovery


def _too_many_attempts():
    if limiter.blocked(request.remote_addr):
        flash("Demasiados intentos fallidos. Espera unos minutos.", "error")
        return True
    return False


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if _too_many_attempts():
            return render_template("login.html"), 429

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Ingresa usuario y contraseña.", "error")
            return render_template("login.html"), 400

        # Mismo mensaje en ambos casos para no revelar qué usuarios existen.
        if not _user_is_allowed(username) or not _authenticate_pam(username, password):
            limiter.fail(request.remote_addr)
            flash("Usuario o contraseña incorrectos, o sin permiso para el panel.", "error")
            return render_template("login.html"), 401

        limiter.reset(request.remote_addr)
        if totp.is_enabled(username):
            session["pending_2fa_user"] = username
            return redirect(url_for("auth.login_2fa"))

        session.permanent = True  # aplica PERMANENT_SESSION_LIFETIME
        login_user(User(username), remember=False)
        return redirect(url_for("dashboard.index"))

    return render_template("login.html")


@auth_bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    username = session.get("pending_2fa_user")
    if not username:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        if _too_many_attempts():
            return render_template("login_2fa.html"), 429

        code = request.form.get("code", "").strip().replace(" ", "")
        ok, is_recovery = _check_second_factor(username, code)

        if ok:
            limiter.reset(request.remote_addr)
            session.pop("pending_2fa_user", None)
            session.permanent = True  # aplica PERMANENT_SESSION_LIFETIME
            login_user(User(username), remember=False)
            if is_recovery:
                left = totp.recovery_remaining(username)
                flash(f"Usaste un código de recuperación; te quedan {left}. "
                      "Para obtener códigos nuevos, desactiva y vuelve a activar 2FA en Seguridad.",
                      "info" if left > 2 else "error")
            return redirect(url_for("dashboard.index"))
        limiter.fail(request.remote_addr)
        flash("Código inválido.", "error")
        return render_template("login_2fa.html"), 401

    return render_template("login_2fa.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/security")
@login_required
def security():
    return render_template("security.html",
                           tfa_enabled=totp.is_enabled(current_user.username),
                           has_pyotp=totp.HAS_PYOTP)


@auth_bp.route("/security/2fa/setup")
@login_required
def tfa_setup():
    if not totp.HAS_PYOTP:
        flash("Instala pyotp y qrcode para habilitar 2FA.", "error")
        return redirect(url_for("auth.security"))
    # Sin esto, cualquier link a esta URL sobreescribiría (y desactivaría) el 2FA activo.
    if totp.is_enabled(current_user.username):
        flash("2FA ya está activo. Desactívalo primero para reconfigurarlo.", "info")
        return redirect(url_for("auth.security"))
    secret, codes = totp.generate_secret(current_user.username)
    return _render_setup(secret, codes)


def _render_setup(secret, codes, error=None):
    uri = totp.provisioning_uri(current_user.username, secret)
    return render_template("tfa_setup.html", secret=secret, codes=codes,
                           qr_svg=_qr_svg(uri), error=error)


def _qr_svg(data):
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return ""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10)
    return img.to_string(encoding="unicode")


@auth_bp.route("/security/2fa/activate", methods=["POST"])
@login_required
def tfa_activate():
    code = request.form.get("code", "").strip().replace(" ", "")
    if totp.activate(current_user.username, code):
        flash("2FA activado correctamente. Guarda tus códigos de recuperación.", "success")
        return redirect(url_for("auth.security"))
    # Reintento con el MISMO secret: el QR ya escaneado sigue siendo válido.
    secret = totp.pending_secret(current_user.username)
    if not secret:
        flash("No hay una configuración de 2FA pendiente. Empieza de nuevo.", "error")
        return redirect(url_for("auth.security"))
    codes = totp.valid_recovery_codes(current_user.username, request.form.getlist("codes"))
    return _render_setup(secret, codes,
                         error="Código inválido. Revisa la hora del teléfono y vuelve a intentarlo."), 400


@auth_bp.route("/security/2fa/disable", methods=["POST"])
@login_required
def tfa_disable():
    if _too_many_attempts():
        return redirect(url_for("auth.security"))
    code = request.form.get("code", "").strip().replace(" ", "")
    if not totp.is_enabled(current_user.username):
        return redirect(url_for("auth.security"))
    ok, _ = _check_second_factor(current_user.username, code) if code else (False, False)
    if not ok:
        limiter.fail(request.remote_addr)
        flash("Código inválido: para desactivar 2FA ingresa el código actual de tu app "
              "o un código de recuperación.", "error")
        return redirect(url_for("auth.security"))
    limiter.reset(request.remote_addr)
    totp.disable(current_user.username)
    flash("2FA desactivado.", "info")
    return redirect(url_for("auth.security"))
