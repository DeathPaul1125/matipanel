"""CLI de respaldos programados (lo invoca cron, ver /etc/cron.d/matipanel-backups).

Uso:  cd /opt/matipanel && venv/bin/python -m app.backup_cli JOBNAME
"""
import sys
from datetime import datetime
from .modules.backups import NAME_RE, run_job


def main(argv):
    if len(argv) != 2 or not NAME_RE.match(argv[1]):
        print("Uso: python -m app.backup_cli JOBNAME", file=sys.stderr)
        return 2
    ok, msg = run_job(argv[1], scheduled=True)
    try:
        from . import notify
        notify.event("backup_ok" if ok else "backup_fail",
                     f"Respaldo de carpetas «{argv[1]}» {'correcto' if ok else 'fallido'}",
                     "Respaldo programado de carpetas de este servidor." if ok else
                     "El respaldo programado de carpetas falló. Revisa el trabajo en Respaldos.",
                     key=f"localbackup:{argv[1]}", cooldown=0, sync=True, link="/backups/",
                     sections=[{"rows": [("Trabajo", argv[1])] + ([("Resultado", msg, "ok")] if ok else [])}],
                     pre=None if ok else msg,
                     **({"badge": "Respaldo correcto"} if ok else {}))
    except Exception:
        pass
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{argv[1]}] {'OK' if ok else 'ERROR'}: {msg}",
          file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
