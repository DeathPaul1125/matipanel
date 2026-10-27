"""Corre todas las pruebas de MatiPanel.

    python tests/run.py            # todas
    python tests/run.py roles fm   # solo las que contienen esos textos

Cada prueba corre en su propia carpeta temporal con la app de prueba (sin tocar /var/lib/matipanel).
Además se ejecutan las auto-pruebas (`if __name__ == "__main__"`) de los módulos que las tienen.

No la ejecutes como root: algunas pruebas llaman comandos reales del sistema y, sin privilegios,
no pueden cambiar nada aunque se corran en el servidor de producción. En el servidor:
    cd /tmp/matipanel && /opt/matipanel/venv/bin/python tests/run.py
"""
import glob
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELFCHECKS = ["app.metrics", "app.audit", "app.notify", "app.roles", "app.sshkeys", "app.appbackup", "app.deployer",
              "app.modules.traffic", "app.monitor"]
# En Windows (desarrollo) faltan módulos de Linux: se simulan para poder importar la app
STUBS = ("import sys, types\nfor n in ('pwd', 'grp', 'pty', 'termios', 'fcntl', 'pam'):\n"
         "    sys.modules.setdefault(n, types.ModuleType(n))\n") if os.name == "nt" else ""


def main(filters):
    sys.stdout.reconfigure(errors="replace")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        sys.exit("No ejecutes las pruebas como root (usa tu usuario normal).")
    tests = sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    jobs = [("prueba", os.path.basename(t)[5:-3], [sys.executable, t, ROOT]) for t in tests]
    for mod in SELFCHECKS:
        if os.path.exists(os.path.join(ROOT, *mod.split(".")) + ".py"):
            code = STUBS + f"import runpy, sys\nsys.path.insert(0, {ROOT!r})\nrunpy.run_module({mod!r}, run_name='__main__')\n"
            jobs.append(("auto-prueba", mod, [sys.executable, "-c", code]))
    if filters:
        jobs = [j for j in jobs if any(f in j[1] for f in filters)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "MATIPANEL_NOTIFY_WORKER": "0", "MATIPANEL_APPBACKUP_WORKER": "0",
           "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_MONITOR_WORKER": "0"}
    failed = []
    t0 = time.time()
    for kind, name, cmd in jobs:
        with tempfile.TemporaryDirectory(prefix="mp-test-") as tmp:
            try:
                r = subprocess.run(cmd, cwd=tmp, env={**env, "MP_TMP": tmp}, capture_output=True, text=True,
                                   errors="replace", timeout=600)
                ok, out = r.returncode == 0, (r.stdout + r.stderr).strip()
            except subprocess.TimeoutExpired:
                ok, out = False, "tiempo agotado (10 min)"
        last = out.splitlines()[-1] if out else ""
        print(f"{'OK   ' if ok else 'FALLÓ'} {kind:11} {name:22} {last[:90]}")
        if not ok:
            failed.append((name, out[-3000:]))
    print(f"\n{len(jobs) - len(failed)}/{len(jobs)} correctas en {time.time() - t0:.0f} s")
    for name, out in failed:
        print(f"\n── {name} ──\n{out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
