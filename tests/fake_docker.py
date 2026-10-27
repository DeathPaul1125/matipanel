"""docker falso para las pruebas de respaldos: exec de mariadb-dump / mariadb."""
import sys, os
args = sys.argv[1:]
log = os.environ.get("FAKE_DOCKER_LOG", "fake_docker.log")
if args[:1] == ["exec"]:
    joined = " ".join(args)
    if "mariadb-dump" in joined:
        sys.stdout.write("-- dump de prueba\nCREATE DATABASE laravel;\n" + "INSERT INTO t VALUES (1);\n" * 5000)
        sys.exit(0)
    if "exec mariadb -uroot" in joined:
        data = sys.stdin.buffer.read()
        open(log, "ab").write(b"RESTORE:" + str(len(data)).encode() + b":" + data[:40] + b"\n")
        sys.exit(0)
sys.stderr.write("fake docker: no soportado " + " ".join(args))
sys.exit(1)
