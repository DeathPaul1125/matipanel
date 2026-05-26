#!/usr/bin/env bash
# uninstall.sh — Quita MatiPanel completamente
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "Ejecuta como root (sudo ./uninstall.sh)" >&2
   exit 1
fi

echo "→ Deteniendo servicio…"
systemctl stop matipanel.service 2>/dev/null || true
systemctl disable matipanel.service 2>/dev/null || true

echo "→ Borrando archivos…"
rm -f /etc/systemd/system/matipanel.service
rm -f /etc/sudoers.d/matipanel
rm -rf /opt/matipanel /etc/matipanel /var/log/matipanel

echo "→ Quitando usuario y grupo…"
userdel matipanel 2>/dev/null || true
groupdel matipanel 2>/dev/null || true

systemctl daemon-reload

echo "✓ MatiPanel desinstalado."
