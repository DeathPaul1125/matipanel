"""Información de discos, particiones y puntos de montaje."""
import psutil
from flask import Blueprint, render_template, jsonify
from flask_login import login_required
from ..util import bytes_to_human as _bytes_to_human

disk_bp = Blueprint("disk", __name__)


@disk_bp.route("/")
@login_required
def index():
    return render_template("disk.html")


@disk_bp.route("/api/partitions")
@login_required
def partitions():
    parts = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            parts.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "opts": part.opts,
                "total_h": _bytes_to_human(usage.total),
                "used_h": _bytes_to_human(usage.used),
                "free_h": _bytes_to_human(usage.free),
                "percent": usage.percent,
            })
        except (PermissionError, OSError):
            continue
    return jsonify({"partitions": parts})
