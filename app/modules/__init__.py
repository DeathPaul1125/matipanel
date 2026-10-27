"""Registro de todos los módulos."""
import os
from .dashboard import dashboard_bp
from .system import system_bp
from .processes import processes_bp
from .services import services_bp
from .cron import cron_bp
from .disk import disk_bp
from .backups import backups_bp
from .users import users_bp
from .network import network_bp
from .traffic import traffic_bp
from .auditlog import audit_bp
from .notifications import notify_bp
from .appbackups import appbackup_bp
from .roles_page import roles_bp
from .stacks import stacks_bp
from .hardening import hardening_bp
from .monitor import monitor_bp
from .firewall import firewall_bp
from .webserver import webserver_bp
from .ssl import ssl_bp
from .php import php_bp
from .databases import databases_bp
from .docker import docker_bp
from .appearance import appearance_bp
from .remote import remote_bp
from .mail import mail_bp
from .shares import shares_bp
from .storage import storage_bp
from .dns import dns_bp
from .dbexplorer import dbexplorer_bp
from .deploy import deploy_bp
from .help import help_bp, CONTEXT as HELP_CONTEXT
from .files import files_bp
from .packages import packages_bp
from .logs import logs_bp
from .terminal import terminal_bp, register_websockets as register_terminal_ws
from .logs_live import register_websockets as register_logs_ws


def register_modules(app, sock):
    app.register_blueprint(dashboard_bp)
    for bp, prefix in [
        (system_bp, "/system"), (processes_bp, "/processes"), (services_bp, "/services"),
        (cron_bp, "/cron"), (disk_bp, "/disk"), (backups_bp, "/backups"),
        (users_bp, "/users"), (network_bp, "/network"), (traffic_bp, "/trafico"), (firewall_bp, "/firewall"),
        (webserver_bp, "/webserver"), (ssl_bp, "/ssl"), (php_bp, "/php"),
        (databases_bp, "/databases"), (docker_bp, "/docker"), (files_bp, "/files"),
        (packages_bp, "/packages"), (logs_bp, "/logs"), (terminal_bp, "/terminal"),
        (appearance_bp, "/apariencia"), (help_bp, "/ayuda"),
        (mail_bp, "/correo"), (dns_bp, "/dns"), (shares_bp, "/compartidos"),
        (storage_bp, "/almacenamiento"), (dbexplorer_bp, "/bd"), (deploy_bp, "/despliegues"),
        (audit_bp, "/auditoria"), (notify_bp, "/notificaciones"), (appbackup_bp, "/respaldos-apps"), (roles_bp, "/permisos"), (stacks_bp, "/stacks"), (hardening_bp, "/seguridad-servidor"), (monitor_bp, "/monitor"),
        (remote_bp, "/servidores"),  # ruta fija: los paneles se consultan entre sí en /servidores/api/remote/summary
    ]:
        app.register_blueprint(bp, url_prefix=prefix)

    from .. import jobs, audit, roles
    jobs.init_app(app)
    audit.init_app(app)  # registro de auditoría de toda acción que cambia algo
    roles.init_app(app)  # permisos por usuario (antes de cada vista)
    from .. import version
    app.jinja_env.globals["PANEL_VERSION"] = version.__version__
    if os.name == "posix" or os.environ.get("MATIPANEL_VERSION_HISTORY"):
        try:
            version.record_startup()  # historial de actualizaciones (Acerca de) + aviso por correo
        except OSError:
            pass  # salida en vivo de los botones (ventana de terminal)
    app.jinja_env.globals["HELP_CONTEXT"] = HELP_CONTEXT  # botón "?" de cada pantalla
    app.jinja_env.globals["VIEW_FUNCS"] = app.view_functions  # el menú oculta módulos no registrados
    register_terminal_ws(sock)
    register_logs_ws(sock)
