"""Registro de todos los módulos."""
from .dashboard import dashboard_bp
from .processes import processes_bp
from .services import services_bp
from .disk import disk_bp
from .users import users_bp
from .network import network_bp
from .firewall import firewall_bp
from .files import files_bp
from .packages import packages_bp
from .logs import logs_bp
from .docker import docker_bp
from .terminal import terminal_bp, register_websockets as register_terminal_ws
from .logs_live import register_websockets as register_logs_ws


def register_modules(app, sock):
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(processes_bp, url_prefix="/processes")
    app.register_blueprint(services_bp, url_prefix="/services")
    app.register_blueprint(disk_bp, url_prefix="/disk")
    app.register_blueprint(users_bp, url_prefix="/users")
    app.register_blueprint(network_bp, url_prefix="/network")
    app.register_blueprint(firewall_bp, url_prefix="/firewall")
    app.register_blueprint(files_bp, url_prefix="/files")
    app.register_blueprint(packages_bp, url_prefix="/packages")
    app.register_blueprint(logs_bp, url_prefix="/logs")
    app.register_blueprint(docker_bp, url_prefix="/docker")
    app.register_blueprint(terminal_bp, url_prefix="/terminal")

    register_terminal_ws(sock)
    register_logs_ws(sock)
