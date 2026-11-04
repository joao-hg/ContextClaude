"""Acesso a dados do app desktop: conversas, mensagens e movimentações."""
import os
from datetime import datetime

_TITLE_MAX = 48


def _ts(now):
    return now().isoformat(timespec="seconds")


def title_from(text: str) -> str:
    line = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    line = " ".join(line.split())
    if not line:
        return "Nova conversa"
    if len(line) <= _TITLE_MAX:
        return line
    cut = line[: _TITLE_MAX - 1]
    if line[_TITLE_MAX - 1] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def create_conversation(conn, first_text: str, now=datetime.now) -> int:
    cur = conn.execute(
        "INSERT INTO conversations(title, created_at) VALUES(?, ?)",
        (title_from(first_text), _ts(now)),
    )
    conn.commit()
    return cur.lastrowid


def add_message(conn, conversation_id: int, role: str, content: str, now=datetime.now) -> int:
    cur = conn.execute(
        "INSERT INTO messages(conversation_id, role, content, created_at) VALUES(?,?,?,?)",
        (conversation_id, role, content, _ts(now)),
    )
    conn.commit()
    return cur.lastrowid


def update_message(conn, message_id: int, content: str) -> None:
    conn.execute("UPDATE messages SET content=? WHERE id=?", (content, message_id))
    conn.commit()


def get_message(conn, message_id: int):
    r = conn.execute(
        "SELECT id, role, content, created_at FROM messages WHERE id=?", (message_id,)
    ).fetchone()
    return dict(r) if r else None


def get_messages(conn, conversation_id: int) -> list:
    rows = conn.execute(
        "SELECT id, role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id",
        (conversation_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_conversations(conn, query: str = "", limit: int = 50) -> list:
    sql = (
        "SELECT c.id, c.title, c.created_at, "
        "COALESCE(MAX(m.created_at), c.created_at) AS updated_at, "
        "COALESCE(MAX(m.id), 0) AS last_msg "
        "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
    )
    params = []
    q = (query or "").strip()
    if q:
        conn.create_function("ulower", 1, lambda v: v.casefold() if v is not None else None, deterministic=True)
        like = "%" + q.casefold().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        sql += (
            "WHERE ulower(c.title) LIKE ? ESCAPE '!' OR c.id IN "
            "(SELECT conversation_id FROM messages WHERE ulower(content) LIKE ? ESCAPE '!') "
        )
        params += [like, like]
    sql += "GROUP BY c.id ORDER BY updated_at DESC, last_msg DESC, c.id DESC LIMIT ?"
    params.append(limit)
    return [
        {"id": r["id"], "title": r["title"], "created_at": r["created_at"], "updated_at": r["updated_at"]}
        for r in conn.execute(sql, params)
    ]


def record_move(conn, batch_id: str, src: str, dst: str, status: str, now=datetime.now) -> int:
    cur = conn.execute(
        "INSERT INTO moves(batch_id, src, dst, status, created_at) VALUES(?,?,?,?,?)",
        (batch_id, src, dst, status, _ts(now)),
    )
    conn.commit()
    return cur.lastrowid


def set_move_status(conn, move_id: int, status: str) -> None:
    conn.execute("UPDATE moves SET status=? WHERE id=?", (status, move_id))
    conn.commit()


def batch_moves(conn, batch_id: str) -> list:
    rows = conn.execute(
        "SELECT id, src, dst, status, created_at FROM moves WHERE batch_id=? ORDER BY id",
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_batches(conn, limit: int = 30) -> list:
    rows = conn.execute(
        "SELECT batch_id, MIN(id) AS first_id, MIN(created_at) AS created_at, "
        "SUM(status='moved') AS moved, SUM(status='undone') AS undone, "
        "SUM(status IN ('failed','undo_failed')) AS failed "
        "FROM moves GROUP BY batch_id ORDER BY first_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        src = conn.execute("SELECT src FROM moves WHERE id=?", (r["first_id"],)).fetchone()["src"]
        out.append({
            "batch_id": r["batch_id"],
            "folder": os.path.dirname(src),
            "created_at": r["created_at"],
            "moved": r["moved"] or 0,
            "undone": r["undone"] or 0,
            "failed": r["failed"] or 0,
        })
    return out
