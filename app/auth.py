"""Autenticación PAM + 2FA TOTP opcional."""
import grp
import pwd
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import UserMixin, login_user, logout_user, login_required, current_user
from . import totp

auth_bp = Blueprint("auth", __name__)
ALLOWED_GROUPS = ("matipanel", "sudo", "wheel", "admin")


class User(UserMixin):
    def __init__(self, username):
        self.id = username
        self.username = username


def load_user(user_id):
    try:
        pwd.getpwnam(user_id)
        return User(user_id)
    except KeyError:
        return None


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
    return pam.pam().authenticate(username, password, service="login")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Ingresa usuario y contraseña.", "error")
            return render_template("login.html"), 400

        if not _user_is_allowed(username):
            flash("Este usuario no tiene permisos para acceder al panel.", "error")
            return render_template("login.html"), 403

        if not _authenticate_pam(username, password):
            flash("Usuario o contraseña incorrectos.", "error")
            return render_template("login.html"), 401

        if totp.is_enabled(username):
            session["pending_2fa_user"] = username
            return redirect(url_for("auth.login_2fa"))

        login_user(User(username), remember=False)
        return redirect(url_for("dashboard.index"))

    return render_template("login.html")


@auth_bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    username = session.get("pending_2fa_user")
    if not username:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = request.form.get("code", "").strip().replace(" ", "")
        is_recovery = len(code) == 8 and all(c in "0123456789abcdef" for c in code.lower())

        ok = totp.consume_recovery(username, code) if is_recovery else totp.verify(username, code)

        if ok:
            session.pop("pending_2fa_user", None)
            login_user(User(username), remember=False)
            if is_recovery:
                flash("Usaste un código de recuperación. Genera uno nuevo en Seguridad.", "info")
            return redirect(url_for("dashboard.index"))
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
    secret, codes = totp.generate_secret(current_user.username)
    uri = totp.provisioning_uri(current_user.username, secret)
    return render_template("tfa_setup.html",
                           secret=secret, codes=codes, otpauth_uri=uri)


@auth_bp.route("/security/2fa/activate", methods=["POST"])
@login_required
def tfa_activate():
    code = request.form.get("code", "").strip()
    if totp.activate(current_user.username, code):
        flash("2FA activado correctamente. Guarda tus códigos de recuperación.", "success")
    else:
        flash("Código inválido. Intenta de nuevo.", "error")
    return redirect(url_for("auth.security"))


@auth_bp.route("/security/2fa/disable", methods=["POST"])
@login_required
def tfa_disable():
    totp.disable(current_user.username)
    flash("2FA desactivado.", "info")
    return redirect(url_for("auth.security"))
