"""Ayuda: documentación de usuario (app/docs/*.md) con índice y búsqueda."""
import html as htmllib
import os
import re
from functools import lru_cache
import markdown
from flask import Blueprint, render_template, jsonify, abort, url_for
from flask_login import login_required

help_bp = Blueprint("help", __name__)

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
SLUG_RE = re.compile(r"^\d{2}-[a-z0-9-]+$")

# Grupos del índice según el número del archivo (mismo orden que el menú del panel)
GROUPS = [(0, "Comenzar"), (10, "Sistema y acceso"), (20, "Red y servidores"), (30, "Cuenta"),
          (50, "Servicios avanzados"), (90, "Ayuda")]

# Pantalla del panel → página de ayuda (botón "?" de la barra superior)
CONTEXT = {
    "dashboard": "10-dashboard", "system": "11-sistema", "processes": "12-procesos",
    "services": "13-servicios", "cron": "14-cron", "disk": "15-discos", "backups": "16-respaldos",
    "users": "17-usuarios", "network": "18-red", "firewall": "20-firewall",
    "webserver": "21-servidor-web", "ssl": "22-ssl", "php": "23-php", "databases": "24-bases-de-datos",
    "docker": "25-docker", "files": "26-archivos", "packages": "27-paquetes", "logs": "28-logs",
    "terminal": "29-terminal", "auth": "30-seguridad-2fa", "appearance": "31-apariencia",
    "remote": "54-multiservidor", "mail": "50-correo", "shares": "52-compartidos", "storage": "53-almacenamiento", "dns": "51-dns",
}


def _slugs():
    return sorted(f[:-3] for f in os.listdir(DOCS_DIR) if f.endswith(".md") and SLUG_RE.match(f[:-3]))


@lru_cache(maxsize=64)
def _render(slug, mtime):
    """HTML + títulos de una página. La caché se invalida sola si el archivo cambia (mtime)."""
    with open(os.path.join(DOCS_DIR, slug + ".md"), encoding="utf-8") as f:
        text = f.read()
    md = markdown.Markdown(extensions=["toc", "tables", "fenced_code", "sane_lists"],
                           extension_configs={"toc": {"toc_depth": "2-3"}})
    html = md.convert(text)
    # Enlaces entre páginas: "13-servicios.md#algo" → /ayuda/13-servicios#algo
    html = re.sub(r'href="(\d{2}-[a-z0-9-]+)\.md(#[^"]*)?"',
                  lambda m: f'href="{url_for("help.page", slug=m.group(1))}{m.group(2) or ""}"', html)
    # Título: la primera línea "# …" (el índice de toc_tokens empieza en ##)
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), slug)
    sections = [{"id": t["id"], "title": t["name"], "sub": [{"id": c["id"], "title": c["name"]} for c in t["children"]]}
                for t in md.toc_tokens]
    return {"slug": slug, "title": title, "html": html, "sections": sections}


def _doc(slug):
    path = os.path.join(DOCS_DIR, slug + ".md")
    if not SLUG_RE.match(slug) or not os.path.isfile(path):
        abort(404)
    return _render(slug, os.path.getmtime(path))


def _index():
    groups = []
    for slug in _slugs():
        num = int(slug[:2])
        name = [g for n, g in GROUPS if num >= n][-1]
        if not groups or groups[-1]["name"] != name:
            groups.append({"name": name, "docs": []})
        groups[-1]["docs"].append({"slug": slug, "title": _doc(slug)["title"]})
    return groups


@help_bp.route("/")
@help_bp.route("/<slug>")
@login_required
def page(slug=None):
    slugs = _slugs()
    if not slugs:
        abort(404)
    slug = slug or slugs[0]
    doc = _doc(slug)
    i = slugs.index(slug)
    nav = {"prev": _doc(slugs[i - 1]) if i > 0 else None, "next": _doc(slugs[i + 1]) if i + 1 < len(slugs) else None}
    return render_template("help.html", doc=doc, groups=_index(), nav=nav)


@help_bp.route("/buscar.json")
@login_required
def search_index():
    """Texto plano por sección (##) de todas las páginas; la búsqueda se hace en el navegador."""
    items = []
    for slug in _slugs():
        doc = _doc(slug)
        # Cortar el HTML en cada <h2> y quitar etiquetas
        parts = re.split(r'(<h2 id="[^"]+">.*?</h2>)', doc["html"])
        current = {"id": "", "title": doc["title"]}
        for part in parts:
            m = re.match(r'<h2 id="([^"]+)">(.*?)</h2>', part)
            if m:
                current = {"id": m.group(1), "title": htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(2)))}
                continue
            text = htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", part)).strip())
            if text:
                items.append({"slug": slug, "doc": doc["title"], "id": current["id"],
                              "section": current["title"], "text": text})
    return jsonify(items)
