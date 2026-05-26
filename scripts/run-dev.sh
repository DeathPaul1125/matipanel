#!/usr/bin/env bash
# run-dev.sh — Levanta MatiPanel en modo desarrollo (sin systemd, sin HTTPS)
# Solo escucha en 127.0.0.1:8080 — usa SSH tunnel si estás en remoto:
#   ssh -L 8080:localhost:8080 usuario@servidor
set -euo pipefail

cd "$(dirname "$0")/.."

# Crear venv si no existe
if [[ ! -d venv ]]; then
    python3 -m venv venv
    venv/bin/pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
fi

export FLASK_APP=wsgi.py
export FLASK_ENV=development
echo "MatiPanel dev → http://127.0.0.1:8080"
exec venv/bin/python wsgi.py
