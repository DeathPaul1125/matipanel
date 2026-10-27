"""Pantalla del registro de auditoría (los eventos los anota app/audit.py)."""
import io
import time
from datetime import datetime

from flask import Blueprint, render_template, jsonify, request, Response
from flask_login import login_required

from .. import audit
from .dbexplorer import csv_line

audit_bp = Blueprint("audit", __name__)
DAYS = (1, 7, 30, 90, 365)


def _filters():
    days = request.args.get("days", 7, type=int)
    days = days if days in DAYS else 7
    return dict(since=time.time() - days * 86400, user=request.args.get("user", "")[:64],
                module=request.args.get("module", "")[:40], result=request.args.get("result", ""),
                q=request.args.get("q", "").strip()[:100])


@audit_bp.route("/")
@login_required
def index():
    users, modules = audit.users_and_modules()
    return render_template("audit.html", users=users, modules=modules, days=DAYS)


@audit_bp.route("/api/events")
@login_required
def events():
    rows = audit.query(**_filters())
    return jsonify({"events": rows, "limit": 2000})


@audit_bp.route("/api/export")
@login_required
def export():
    rows = audit.query(**_filters(), limit=100000)
    out = io.StringIO()
    out.write("﻿")  # Excel abre el CSV en UTF-8
    out.write(csv_line(["Fecha", "Usuario", "IP", "Módulo", "Acción", "Objetivo", "Resultado", "HTTP", "Detalle"],
                       excel_safe=True))
    for e in rows:
        detail = "; ".join(f"{k}={v}" for k, v in e["detail"].items())
        out.write(csv_line([datetime.fromtimestamp(e["t"]).strftime("%Y-%m-%d %H:%M:%S"), e["user"], e["ip"],
                            e["module_label"], e["action"], e["target"], "correcto" if e["ok"] else "falló",
                            str(e["status"]), detail], excel_safe=True))
    name = f"auditoria-{datetime.now():%Y%m%d-%H%M}.csv"
    return Response(out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
