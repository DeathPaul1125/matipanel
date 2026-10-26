"""Servidor web: Apache (layout Debian) y Nginx — sitios, módulos, vhosts."""
import glob
import os
import re
import shutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import run

webserver_bp = Blueprint("webserver", __name__)

SERVERS = {
    "apache": {
        "label": "Apache", "unit": "apache2", "bin": "apache2ctl", "etc": "/etc/apache2",
        "test": ["apache2ctl", "configtest"], "version": ["apache2ctl", "-v"],
        "logs": {"error": "/var/log/apache2/error.log", "access": "/var/log/apache2/access.log"},
        "defaults": {"000-default.conf", "default-ssl.conf"},
    },
    "nginx": {
        "label": "Nginx", "unit": "nginx", "bin": "nginx", "etc": "/etc/nginx",
        "test": ["nginx", "-t"], "version": ["nginx", "-v"],
        "logs": {"error": "/var/log/nginx/error.log", "access": "/var/log/nginx/access.log"},
        "defaults": {"default"},
    },
}

HOST_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9-]{1,63}\.)*[a-zA-Z0-9-]{1,63}$")
PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
FILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,200}$")
MOD_RE = re.compile(r"^[a-z0-9_]+$")
PHP_RE = re.compile(r"^\d+\.\d+$")
# ponytail: docroots limitados a estas raíces para no chownear/crear en rutas del sistema
DOCROOT_BASES = ("/var/www/", "/srv/", "/home/")


# Respaldos de editores y temporales que no son sitios reales (nginx los incluiría igual).
JUNK_SUFFIXES = (".bak", "~", ".dpkg-old", ".dpkg-dist", ".swp")  # .matipanel.bak cae en .bak


def _is_junk(name):
    return name.startswith(".") or name.endswith(JUNK_SUFFIXES)


def _installed(key):
    s = SERVERS[key]
    return os.path.isdir(s["etc"]) and shutil.which(s["bin"]) is not None


def _srv(key):
    return SERVERS[key] if key in SERVERS and _installed(key) else None


def _dirs(s):
    return os.path.join(s["etc"], "sites-available"), os.path.join(s["etc"], "sites-enabled")


def _err(msg, code=400, **extra):
    return jsonify({"ok": False, "error": msg, **extra}), code


def _test(s):
    code, out, err = run(s["test"], timeout=20)
    return code == 0, (out + err).strip()


def _apply(s):
    """Prueba la config y, si es válida y el servicio está activo, recarga."""
    ok, out = _test(s)
    if not ok:
        return False, out
    if run(["systemctl", "is-active", "--quiet", s["unit"]])[0] != 0:
        return True, out + "\n(servicio inactivo: no se recargó)"
    code, o, e = run(["systemctl", "reload", s["unit"]], timeout=30)
    return code == 0, (out + "\n" + o + e).strip()


def valid_docroot(p):
    return (bool(p) and len(p) < 256 and PATH_RE.fullmatch(p) and p.startswith(DOCROOT_BASES)
            and ".." not in p.split("/") and os.path.normpath(p) == p)


def php_versions():
    found = (re.search(r"php(\d+\.\d+)-fpm\.sock$", f) for f in glob.glob("/run/php/php*-fpm.sock"))
    return sorted({m.group(1) for m in found if m})


@webserver_bp.route("/")
@login_required
def index():
    servers = [{"key": k, "label": s["label"]} for k, s in SERVERS.items() if _installed(k)]
    return render_template("webserver.html", servers=servers)


@webserver_bp.route("/api/php")
@login_required
def php():
    return jsonify({"versions": php_versions()})


@webserver_bp.route("/api/<srv>/status")
@login_required
def status(srv):
    s = _srv(srv)
    if not s:
        return _err("Servidor no instalado", 404)
    _, active, _ = run(["systemctl", "is-active", s["unit"]])
    _, out, err = run(s["version"])
    m = re.search(r"(Apache|nginx)/\S+", out + err)
    return jsonify({"active": active.strip() or "unknown", "version": m.group(0) if m else (out + err).strip()})


@webserver_bp.route("/api/<srv>/service", methods=["POST"])
@login_required
def service(srv):
    s = _srv(srv)
    if not s:
        return _err("Servidor no instalado", 404)
    act = (request.get_json(silent=True) or {}).get("action", "")
    if act not in ("reload", "restart", "test"):
        return _err("Acción no permitida")
    ok, out = _test(s)
    if not ok:
        return _err("La configuración tiene errores; no se aplicó", 409, output=out)
    if act == "test":
        return jsonify({"ok": True, "output": out})
    code, o, e = run(["systemctl", act, s["unit"]], timeout=60)
    if code != 0:
        return _err((e or o).strip() or "Fallo desconocido", 500, output=out)
    return jsonify({"ok": True, "output": out})


def _parse_site(srv, text):
    if srv == "apache":
        names = re.findall(r"(?im)^\s*ServerName\s+(\S+)", text)
        aliases = [a for line in re.findall(r"(?im)^\s*ServerAlias\s+(.+)$", text) for a in line.split()]
        roots = re.findall(r'(?im)^\s*DocumentRoot\s+"?([^"\s]+)', text)
        ports = re.findall(r"(?im)<VirtualHost\s+[^>]*:(\d+)", text)
    else:
        names, aliases = [], []
        for line in re.findall(r"(?m)^\s*server_name\s+([^;]+);", text):
            parts = line.split()
            names.append(parts[0])
            aliases += parts[1:]
        roots = re.findall(r"(?m)^\s*root\s+([^;\s]+)\s*;", text)
        ports = []
        for line in re.findall(r"(?m)^\s*listen\s+([^;]+);", text):
            m = re.search(r"(?:^|:)(\d+)(?:\s|$)", line.split()[0] + " ")
            ports.append((m.group(1) if m else line.split()[0]) + (" ssl" if " ssl" in line else ""))
    return {k: list(dict.fromkeys(v)) for k, v in
            (("server_name", names), ("aliases", aliases), ("docroot", roots), ("ports", ports))}


@webserver_bp.route("/api/<srv>/sites")
@login_required
def sites(srv):
    s = _srv(srv)
    if not s:
        return _err("Servidor no instalado", 404)
    avail, enabled = _dirs(s)
    names = set()
    for d in (avail, enabled):
        if os.path.isdir(d):
            names.update(f for f in os.listdir(d) if not _is_junk(f))
    items = []
    for name in sorted(names):
        in_avail = os.path.isfile(os.path.join(avail, name))
        in_enabled = os.path.exists(os.path.join(enabled, name))
        if srv == "apache" and not name.endswith(".conf"):
            continue
        path = os.path.join(avail if in_avail else enabled, name)
        try:
            with open(path, errors="replace") as f:
                info = _parse_site(srv, f.read(512 * 1024))
        except OSError:
            info = {"server_name": [], "aliases": [], "docroot": [], "ports": []}
        items.append({"name": name, "path": path, "enabled": in_enabled, "available": in_avail,
                      "default": name in s["defaults"], **info})
    return jsonify({"sites": items})


def _enable(srv, s, name):
    if srv == "apache":
        code, o, e = run(["a2ensite", "-q", name])
        return code == 0, (o + e).strip()
    avail, enabled = _dirs(s)
    link = os.path.join(enabled, name)
    try:
        if not os.path.lexists(link):
            os.symlink(os.path.join(avail, name), link)
    except OSError as e:
        return False, str(e)
    return True, ""


def _disable(srv, s, name):
    if srv == "apache":
        code, o, e = run(["a2dissite", "-q", name])
        return code == 0, (o + e).strip()
    link = os.path.join(_dirs(s)[1], name)
    if os.path.lexists(link):
        if not os.path.islink(link):
            return False, "Es un archivo real en sites-enabled, no un enlace; edítalo o muévelo a mano"
        try:
            os.unlink(link)
        except OSError as e:
            return False, str(e)
    return True, ""


@webserver_bp.route("/api/<srv>/site", methods=["POST"])
@login_required
def site_action(srv):
    s = _srv(srv)
    if not s:
        return _err("Servidor no instalado", 404)
    data = request.get_json(silent=True) or {}
    name, act = data.get("name", ""), data.get("action", "")
    if not FILE_RE.fullmatch(name):
        return _err("Nombre de sitio inválido")
    avail, enabled = _dirs(s)
    path = os.path.join(avail, name)

    if act == "enable":
        if not os.path.isfile(path):
            return _err("El sitio no existe en sites-available", 404)
        ok, out = _enable(srv, s, name)
        if not ok:
            return _err(out or "No se pudo habilitar", 500)
        ok, out = _apply(s)
        if not ok:
            _disable(srv, s, name)
            return _err("La configuración falló; se deshizo la habilitación", 409, output=out)
        return jsonify({"ok": True, "output": out})

    if act == "disable":
        ok, out = _disable(srv, s, name)
        if not ok:
            return _err(out or "No se pudo deshabilitar", 500)
        ok, out = _apply(s)
        return jsonify({"ok": ok, "output": out, "error": "" if ok else "Deshabilitado, pero la prueba de config falló"})

    if act == "delete":
        if not os.path.isfile(path):
            return _err("Solo se pueden eliminar sitios de sites-available", 404)
        if name in s["defaults"] and not data.get("force"):
            return _err("Es un sitio por defecto; confirma para eliminarlo", 409)
        was_ok = _test(s)[0]  # si ya estaba rota, no se puede culpar al borrado
        was_enabled = os.path.lexists(os.path.join(enabled, name))
        with open(path, "rb") as f:
            saved, mode = f.read(), os.stat(path).st_mode & 0o7777
        if was_enabled:
            ok, out = _disable(srv, s, name)
            if not ok:
                return _err(out or "No se pudo deshabilitar", 500)
        os.remove(path)
        ok, out = _apply(s)
        if not ok and was_ok:
            # Otro sitio dependía de este (p. ej. un include): se restaura tal cual estaba
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as f:
                f.write(saved)
            if was_enabled:
                _enable(srv, s, name)
            return _err("Sin este sitio la configuración deja de ser válida; se restauró", 409, output=out)
        return jsonify({"ok": ok, "output": out, "error": "" if ok else "Eliminado, pero la prueba de config falló"})

    return _err("Acción no permitida")


def _apache_vhost(domain, aliases, docroot, php):
    alias = f"    ServerAlias {' '.join(aliases)}\n" if aliases else ""
    fpm = (f"    <FilesMatch \\.php$>\n"
           f"        SetHandler \"proxy:unix:/run/php/php{php}-fpm.sock|fcgi://localhost\"\n"
           f"    </FilesMatch>\n") if php else ""
    return (f"<VirtualHost *:80>\n"
            f"    ServerName {domain}\n{alias}"
            f"    DocumentRoot {docroot}\n"
            f"    <Directory {docroot}>\n"
            f"        Options -Indexes +FollowSymLinks\n"
            f"        AllowOverride All\n"
            f"        Require all granted\n"
            f"    </Directory>\n{fpm}"
            f"    ErrorLog ${{APACHE_LOG_DIR}}/{domain}-error.log\n"
            f"    CustomLog ${{APACHE_LOG_DIR}}/{domain}-access.log combined\n"
            f"</VirtualHost>\n")


def _nginx_vhost(domain, aliases, docroot, php):
    fpm = (f"    location ~ \\.php$ {{\n"
           f"        include snippets/fastcgi-php.conf;\n"
           f"        fastcgi_pass unix:/run/php/php{php}-fpm.sock;\n"
           f"    }}\n\n") if php else ""
    index = "index.php index.html index.htm" if php else "index.html index.htm"
    fallback = "/index.php?$query_string" if php else "=404"
    return (f"server {{\n"
            f"    listen 80;\n"
            f"    listen [::]:80;\n"
            f"    server_name {' '.join([domain, *aliases])};\n"
            f"    root {docroot};\n"
            f"    index {index};\n\n"
            f"    access_log /var/log/nginx/{domain}.access.log;\n"
            f"    error_log /var/log/nginx/{domain}.error.log;\n\n"
            f"    location / {{\n"
            f"        try_files $uri $uri/ {fallback};\n"
            f"    }}\n\n{fpm}"
            f"    location ~ /\\.ht {{\n"
            f"        deny all;\n"
            f"    }}\n"
            f"}}\n")


def _real_ok(p):
    """La ruta real (resolviendo symlinks) también debe quedar bajo DOCROOT_BASES: en /home un
    usuario podría dejar un enlace hacia /etc y el panel (root) crearía y chownearía ahí."""
    real = os.path.realpath(p)
    return real.startswith(DOCROOT_BASES)


def _make_docroot(docroot, domain):
    """Crea el docroot y un index.html; devuelve lo que creó (para poder revertirlo)."""
    if not _real_ok(docroot):
        raise OSError("la ruta real (siguiendo enlaces simbólicos) sale de " + ", ".join(DOCROOT_BASES))
    missing, p = [], docroot
    while p and p != "/" and not os.path.lexists(p):
        missing.append(p)
        p = os.path.dirname(p)
    os.makedirs(docroot, exist_ok=True)
    if not _real_ok(docroot) or not os.path.isdir(docroot):  # carrera: un enlace apareció mientras tanto
        _undo_docroot(missing)
        raise OSError("la ruta cambió durante la creación")
    index = os.path.join(docroot, "index.html")
    if not any(os.path.lexists(os.path.join(docroot, f)) for f in ("index.html", "index.php")):
        # O_NOFOLLOW|O_EXCL: nunca escribir a través de un symlink plantado en el docroot
        fd = os.open(index, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(f"<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>{domain}</title></head>\n"
                    f"<body><h1>{domain}</h1><p>Sitio creado con MatiPanel.</p></body></html>\n")
        missing.append(index)
    import pwd, grp  # noqa: E401 — solo existen en POSIX
    uid, gid = pwd.getpwnam("www-data").pw_uid, grp.getgrnam("www-data").gr_gid
    for path in missing:
        os.lchown(path, uid, gid)  # lchown: no seguir enlaces
    return missing


def _undo_docroot(created):
    # Lo más profundo primero (index.html, luego carpetas); rmdir no borra si ya tiene algo.
    for path in sorted(created, key=len, reverse=True):
        try:
            os.rmdir(path) if os.path.isdir(path) and not os.path.islink(path) else os.unlink(path)
        except OSError:
            pass


@webserver_bp.route("/api/<srv>/create", methods=["POST"])
@login_required
def create(srv):
    s = _srv(srv)
    if not s:
        return _err("Servidor no instalado", 404)
    data = request.get_json(silent=True) or {}
    domain = str(data.get("domain", "")).strip().lower()
    aliases = [a.strip().lower() for a in str(data.get("aliases", "")).replace(",", " ").split() if a.strip()]
    docroot = str(data.get("docroot", "")).strip().rstrip("/") or f"/var/www/{domain}/public_html"
    php = str(data.get("php", "")).strip()

    if not HOST_RE.fullmatch(domain) or domain.startswith("-"):
        return _err("Dominio inválido")
    bad = [a for a in aliases if not HOST_RE.fullmatch(a) or a.startswith("-")]
    if bad:
        return _err("Alias inválido: " + bad[0])
    if not valid_docroot(docroot):
        return _err("Document root inválido (ruta absoluta bajo " + ", ".join(DOCROOT_BASES) + ")")
    if php and (not PHP_RE.fullmatch(php) or php not in php_versions()):
        return _err("Versión de PHP-FPM no disponible")

    avail, enabled = _dirs(s)
    name = f"{domain}.conf"
    path = os.path.join(avail, name)
    if os.path.lexists(path) or os.path.lexists(os.path.join(enabled, name)):
        return _err("Ya existe un sitio con ese nombre", 409)

    try:
        created = _make_docroot(docroot, domain)
    except (OSError, LookupError) as e:
        return _err(f"No se pudo crear el document root: {e}", 500)

    log = []
    if srv == "apache" and php:
        code, o, e = run(["a2enmod", "-q", "proxy_fcgi", "setenvif"])
        log.append((o + e).strip())
    conf = (_apache_vhost if srv == "apache" else _nginx_vhost)(domain, aliases, docroot, php)
    with open(path, "w") as f:
        f.write(conf)
    ok, out = _enable(srv, s, name)
    if ok:
        ok, out = _apply(s)
    log.append(out)
    if not ok:
        _disable(srv, s, name)
        if os.path.exists(path):
            os.remove(path)
        _undo_docroot(created)  # solo lo que creó este asistente en este mismo intento
        return _err("La prueba de configuración falló; se revirtió el sitio", 409,
                    output="\n".join(filter(None, log)))
    return jsonify({"ok": True, "path": path, "output": "\n".join(filter(None, log))})


@webserver_bp.route("/api/apache/mods")
@login_required
def mods():
    s = _srv("apache")
    if not s:
        return _err("Apache no instalado", 404)
    avail = os.path.join(s["etc"], "mods-available")
    enabled = os.path.join(s["etc"], "mods-enabled")
    names = sorted(f[:-5] for f in os.listdir(avail) if f.endswith(".load")) if os.path.isdir(avail) else []
    return jsonify({"mods": [{"name": n, "enabled": os.path.exists(os.path.join(enabled, n + ".load"))}
                             for n in names]})


@webserver_bp.route("/api/apache/mod", methods=["POST"])
@login_required
def mod_action():
    s = _srv("apache")
    if not s:
        return _err("Apache no instalado", 404)
    data = request.get_json(silent=True) or {}
    name, act = data.get("name", ""), data.get("action", "")
    if act not in ("enable", "disable"):
        return _err("Acción no permitida")
    if not MOD_RE.fullmatch(name):
        return _err("Nombre de módulo inválido")
    code, o, e = run(["a2enmod" if act == "enable" else "a2dismod", "-q", name])
    if code != 0:
        return _err((e or o).strip() or "Fallo desconocido", 500)
    ok, out = _test(s)
    return jsonify({"ok": True, "output": ((o + e).strip() + "\n" + out).strip(), "test_ok": ok})


@webserver_bp.route("/api/<srv>/log")
@login_required
def log(srv):
    s = _srv(srv)
    if not s:
        return _err("Servidor no instalado", 404)
    path = s["logs"].get(request.args.get("kind", "error"))
    if not path:
        return _err("Log inválido")
    if not os.path.exists(path):
        return jsonify({"log": "", "path": path})
    # -c: líneas gigantes (URLs de un atacante en access.log) no inflan la respuesta
    code, out, err = run(["tail", "-c", "262144", "--", path], timeout=10)
    if code != 0:
        return _err(err.strip() or "No se pudo leer el log", 500)
    return jsonify({"log": "\n".join(out.splitlines()[-200:]), "path": path})
