"""Registro de todos los módulos."""
from .dashboard import dashboard_bp
from .system import system_bp
from .processes import processes_bp
from .services import services_bp
from .cron import cron_bp
from .disk import disk_bp
from .backups import backups_bp
from .users import users_bp
from .network import network_bp
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
        (users_bp, "/users"), (network_bp, "/network"), (firewall_bp, "/firewall"),
        (webserver_bp, "/webserver"), (ssl_bp, "/ssl"), (php_bp, "/php"),
        (databases_bp, "/databases"), (docker_bp, "/docker"), (files_bp, "/files"),
        (packages_bp, "/packages"), (logs_bp, "/logs"), (terminal_bp, "/terminal"),
        (appearance_bp, "/apariencia"), (help_bp, "/ayuda"),
        (mail_bp, "/correo"), (shares_bp, "/compartidos"),
        (remote_bp, "/servidores"),  # ruta fija: los paneles se consultan entre sí en /servidores/api/remote/summary
    ]:
        app.register_blueprint(bp, url_prefix=prefix)

    app.jinja_env.globals["HELP_CONTEXT"] = HELP_CONTEXT  # botón "?" de cada pantalla
    register_terminal_ws(sock)
    register_logs_ws(sock)
