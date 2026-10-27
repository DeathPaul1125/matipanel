"""Claves SSH autorizadas (~/.ssh/authorized_keys) de cada usuario.

El panel corre como root y escribe dentro del home de otro usuario: ese usuario controla ~/.ssh y podría
dejar un enlace simbólico (p. ej. authorized_keys -> /root/.ssh/authorized_keys). Por eso todo se hace
por descriptores (dir_fd) con O_NOFOLLOW, se exige que ~/.ssh sea del propio usuario y la escritura es
atómica (temporal + rename dentro de la misma carpeta abierta).
"""
import base64
import hashlib
import os
import re
import stat
import struct

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
             "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
MAX_FILE = 256 * 1024
COMMENT_RE = re.compile(r"[\x20-\x7e -￿]{0,200}")


class KeyError_(Exception):
    pass


def parse_key(line):
    """«[opciones] tipo base64 [comentario]» → dict o None si la línea no es una clave."""
    parts = line.strip().split()
    if not parts or parts[0].startswith("#"):
        return None
    idx = next((i for i, p in enumerate(parts) if p in KEY_TYPES), None)
    if idx is None or idx + 1 >= len(parts):
        return None
    ktype, b64 = parts[idx], parts[idx + 1]
    try:
        blob = base64.b64decode(b64, validate=True)
    except ValueError:
        return None
    # El blob empieza con el tipo, con su largo de 4 bytes (formato SSH)
    if len(blob) < 4 or blob[4:4 + struct.unpack(">I", blob[:4])[0]].decode(errors="replace") != ktype:
        return None
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    bits = None
    if ktype == "ssh-rsa":  # e, n: el largo de n da los bits
        try:
            off = 4 + len(ktype)
            elen = struct.unpack(">I", blob[off:off + 4])[0]
            off += 4 + elen
            nlen = struct.unpack(">I", blob[off:off + 4])[0]
            n = blob[off + 4:off + 4 + nlen].lstrip(b"\0")
            bits = len(n) * 8
        except struct.error:
            pass
    return {"type": ktype, "fingerprint": fp, "comment": " ".join(parts[idx + 2:])[:200],
            "options": " ".join(parts[:idx])[:300], "bits": bits}


def validate_new(text):
    """Texto pegado por el admin → línea de clave limpia. Lanza KeyError_ si no es válida."""
    lines = [ln for ln in str(text).replace("\r", "").split("\n") if ln.strip()]
    if len(lines) != 1:
        raise KeyError_("Pega una sola clave pública (una línea)")
    line = lines[0].strip()
    k = parse_key(line)
    if not k:
        raise KeyError_("No parece una clave pública SSH (ssh-ed25519, ssh-rsa o ecdsa)")
    if k["bits"] is not None and k["bits"] < 2048:
        raise KeyError_(f"Clave RSA de {k['bits']} bits: demasiado débil (mínimo 2048)")
    if not COMMENT_RE.fullmatch(k["comment"]):
        raise KeyError_("El comentario de la clave tiene caracteres no válidos")
    return line, k


def _open_ssh_dir(pw, create):
    """→ fd de ~/.ssh (propiedad del usuario, sin enlaces) o None si no existe y create=False."""
    try:
        home_fd = os.open(pw.pw_dir, os.O_RDONLY | O_DIRECTORY)
    except FileNotFoundError:
        raise KeyError_(f"El usuario no tiene carpeta personal ({pw.pw_dir} no existe)")
    except OSError as e:
        raise KeyError_(f"No se pudo abrir la carpeta personal {pw.pw_dir}: {e.strerror}")
    try:
        hst = os.fstat(home_fd)
        if hst.st_uid not in (pw.pw_uid, 0):
            raise KeyError_("El home del usuario pertenece a otro usuario; revísalo a mano")
        try:
            fd = os.open(".ssh", os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=home_fd)
        except FileNotFoundError:
            if not create:
                return None
            os.mkdir(".ssh", 0o700, dir_fd=home_fd)
            fd = os.open(".ssh", os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=home_fd)
            os.fchown(fd, pw.pw_uid, pw.pw_gid)
        except OSError as e:
            raise KeyError_(f"~/.ssh no es una carpeta normal ({e.strerror}); revísalo a mano")
        st = os.fstat(fd)
        if st.st_uid != pw.pw_uid:
            os.close(fd)
            raise KeyError_("~/.ssh no pertenece al usuario; revísalo a mano")
        return fd
    finally:
        os.close(home_fd)


def read(pw):
    """→ (texto del archivo, [claves con su número de línea])."""
    fd = _open_ssh_dir(pw, create=False)
    if fd is None:
        return "", []
    try:
        try:
            f = os.open("authorized_keys", os.O_RDONLY | O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            return "", []
        except OSError as e:
            raise KeyError_(f"authorized_keys no es un archivo normal ({e.strerror})")
        with os.fdopen(f, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise KeyError_("authorized_keys no es un archivo normal")
            text = fh.read(MAX_FILE + 1).decode("utf-8", "replace")
        if len(text) > MAX_FILE:
            raise KeyError_("authorized_keys es demasiado grande")
    finally:
        os.close(fd)
    keys = []
    for i, line in enumerate(text.split("\n")):
        k = parse_key(line)
        if k:
            keys.append({"line": i, **k})
    return text, keys


def _write(pw, text):
    fd = _open_ssh_dir(pw, create=True)
    try:
        tmp = f".authorized_keys.matipanel-{os.getpid()}"
        try:
            os.unlink(tmp, dir_fd=fd)
        except FileNotFoundError:
            pass
        f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            os.write(f, text.encode())
            os.fchown(f, pw.pw_uid, pw.pw_gid)
            os.fchmod(f, 0o600)
        finally:
            os.close(f)
        os.replace(tmp, "authorized_keys", src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        os.close(fd)


def add(pw, key_text):
    line, k = validate_new(key_text)
    text, keys = read(pw)
    if any(x["fingerprint"] == k["fingerprint"] for x in keys):
        raise KeyError_("Esa clave ya está autorizada")
    if text and not text.endswith("\n"):
        text += "\n"
    _write(pw, text + line + "\n")
    return k


def remove(pw, line_no, fingerprint):
    """Quita la línea `line_no` si su huella coincide (evita borrar otra si el archivo cambió)."""
    text, keys = read(pw)
    if not any(k["line"] == line_no and k["fingerprint"] == fingerprint for k in keys):
        raise KeyError_("La clave ya no está en el archivo (¿cambió mientras tanto?). Recarga la lista.")
    lines = text.split("\n")
    del lines[line_no]
    _write(pw, "\n".join(lines))


if __name__ == "__main__":
    ed = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl pablo@pc"
    k = parse_key(ed)
    assert k["type"] == "ssh-ed25519" and k["comment"] == "pablo@pc"
    assert k["fingerprint"] == "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU", k["fingerprint"]
    assert parse_key('restrict,from="10.0.0.0/8" ' + ed)["options"].startswith("restrict")
    assert parse_key("# comentario") is None and parse_key("ssh-ed25519 AAAA") is None
    assert parse_key("ssh-rsa " + ed.split()[1]) is None  # el tipo del blob no coincide
    for bad in ("", ed + "\n" + ed, "hola mundo"):
        try:
            validate_new(bad)
            raise AssertionError(bad)
        except KeyError_:
            pass
    assert validate_new("  " + ed + "  ")[0] == ed
    if os.name == "posix":  # escritura real con descriptores (solo Linux)
        import tempfile, types
        home = tempfile.mkdtemp()
        me = types.SimpleNamespace(pw_dir=home, pw_uid=os.getuid(), pw_gid=os.getgid())
        assert read(me) == ("", [])
        add(me, ed)
        text, keys = read(me)
        assert keys[0]["fingerprint"] == k["fingerprint"] and oct(os.stat(home + "/.ssh/authorized_keys").st_mode)[-3:] == "600"
        try:
            add(me, ed)
            raise AssertionError("duplicada")
        except KeyError_:
            pass
        remove(me, keys[0]["line"], keys[0]["fingerprint"])
        assert read(me)[1] == []
        # ataque: authorized_keys como enlace a otro archivo → no se lee ni se escribe a través de él
        victim = tempfile.mktemp()
        open(victim, "w").write("secreto\n")
        os.unlink(home + "/.ssh/authorized_keys")
        os.symlink(victim, home + "/.ssh/authorized_keys")
        for fn in (lambda: read(me), lambda: add(me, ed)):
            try:
                fn()
            except (KeyError_, OSError):
                pass
        assert open(victim).read() == "secreto\n"
        # ataque: ~/.ssh como enlace → rechazado
        import shutil
        shutil.rmtree(home + "/.ssh") if not os.path.islink(home + "/.ssh") else os.unlink(home + "/.ssh")
        os.symlink(tempfile.mkdtemp(), home + "/.ssh")
        try:
            add(me, ed)
            raise AssertionError("siguió el enlace de ~/.ssh")
        except KeyError_:
            pass
    print("ok")
