import hashlib
import os

from . import config


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def project_name(conn, cwd: str) -> str:
    key = _norm(cwd)
    if key == _norm(config.home()):
        return "_geral"
    row = conn.execute("SELECT name FROM projects WHERE cwd=?", (key,)).fetchone()
    if row:
        return row["name"]
    name = os.path.basename(os.path.normpath(cwd)) or "_raiz"
    if conn.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone():
        name = f"{name}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:6]}"
    conn.execute("INSERT INTO projects(cwd, name) VALUES(?,?)", (key, name))
    conn.commit()
    return name
