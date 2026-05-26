"""
Autenticación contra PAM del sistema (usa los mismos usuarios de Linux).
Solo usuarios del grupo 'matipanel' o 'sudo' pueden acceder.
"""
import grp
import pwd
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import UserMixin, login_user, logout_user, login_required

auth_bp = Blueprint("auth", __name__)

# Grupos permitidos para acceder al panel
ALLOWED_GROUPS = ("matipanel", "sudo", "wheel", "admin")


class User(UserMixin):
    def __init__(self, username):
        self.id = username
        self.username = username


def load_user(user_id):
    """Carga el usuario si todavía existe en el sistema."""
    try:
        pwd.getpwnam(user_id)
        return User(user_id)
    except KeyError:
        return None


def _user_is_allowed(username):
    """Verifica que el usuario pertenezca a alguno de los grupos permitidos."""
    try:
        user_info = pwd.getpwnam(username)
    except KeyError:
        return False

    user_groups = [g.gr_name for g in grp.getgrall() if username in g.gr_mem]
    # Agregar el grupo primario
    try:
        primary = grp.getgrgid(user_info.pw_gid).gr_name
        user_groups.append(primary)
    except KeyError:
        pass

    return any(g in ALLOWED_GROUPS for g in user_groups)


def _authenticate_pam(username, password):
    """Autentica contra PAM. Devuelve True si las credenciales son válidas."""
    try:
        import pam
    except ImportError:
        # Fallback: si python-pam no está instalado, rechaza todo
        return False
    p = pam.pam()
    return p.authenticate(username, password, service="login")


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

        if _authenticate_pam(username, password):
            user = User(username)
            login_user(user, remember=False)
            return redirect(url_for("dashboard.index"))
        else:
            flash("Usuario o contraseña incorrectos.", "error")
            return render_template("login.html"), 401

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
