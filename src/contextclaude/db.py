import sqlite3
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
    session_id TEXT PRIMARY KEY, project TEXT, cwd TEXT, transcript_path TEXT,
    status TEXT NOT NULL DEFAULT 'queued', offset INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT, processed_at TEXT);
CREATE TABLE IF NOT EXISTS facts(
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, project TEXT,
    kind TEXT, text TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS relations(
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, project TEXT,
    source TEXT, relation TEXT, target TEXT);
CREATE TABLE IF NOT EXISTS projects(cwd TEXT PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS injected(
    session_id TEXT, fact_id INTEGER, PRIMARY KEY(session_id, fact_id));
CREATE TABLE IF NOT EXISTS chibi_rounds(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT, duration_s REAL,
    sessoes INTEGER, chars_total INTEGER,
    fatos INTEGER, decisoes INTEGER, pendencias INTEGER,
    status TEXT NOT NULL,
    mensagem TEXT
);
CREATE TABLE IF NOT EXISTS transcript_offsets(
    session_id TEXT, path TEXT, offset INTEGER NOT NULL, PRIMARY KEY(session_id, path));
CREATE TABLE IF NOT EXISTS conversations(
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE TABLE IF NOT EXISTS moves(
    id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
    src TEXT NOT NULL, dst TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_moves_batch ON moves(batch_id);
"""


def connect(timeout: float = 10) -> sqlite3.Connection:
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    _add_indexed_column(conn)
    _add_status_column(conn)
    _add_supersedes_column(conn)
    for col in SESSION_COLUMNS:
        _add_column(conn, "sessions", col, "TEXT")
    return conn


# Registro da sessão para a nota: título/resumo/feitos consolidados pelo LLM; `draft` (JSON)
# acumula o material dos blocos ainda não consolidado (sobrevive a queda do worker).
SESSION_COLUMNS = ("title", "summary", "feitos", "draft", "note_path")


def _add_column(conn, table: str, col: str, decl: str) -> None:
    """Migração idempotente; outro processo pode ter adicionado a coluna entre a checagem e o ALTER."""
    if col in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        conn.commit()
    except sqlite3.OperationalError:
        if col not in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]:
            raise


def _add_indexed_column(conn) -> None:
    """Migração idempotente; outro processo pode ter adicionado a coluna entre a checagem e o ALTER."""
    if "indexed" in [r[1] for r in conn.execute("PRAGMA table_info(facts)")]:
        return
    try:
        conn.execute("ALTER TABLE facts ADD COLUMN indexed INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        if "indexed" not in [r[1] for r in conn.execute("PRAGMA table_info(facts)")]:
            raise


def _add_status_column(conn) -> None:
    """Migração idempotente; outro processo pode ter adicionado a coluna entre a checagem e o ALTER."""
    if "status" in [r[1] for r in conn.execute("PRAGMA table_info(facts)")]:
        return
    try:
        conn.execute("ALTER TABLE facts ADD COLUMN status TEXT NOT NULL DEFAULT 'confirmada'")
        conn.commit()
    except sqlite3.OperationalError:
        if "status" not in [r[1] for r in conn.execute("PRAGMA table_info(facts)")]:
            raise


def _add_supersedes_column(conn) -> None:
    if "supersedes" in [r[1] for r in conn.execute("PRAGMA table_info(facts)")]:
        return
    try:
        conn.execute("ALTER TABLE facts ADD COLUMN supersedes INTEGER")
        conn.commit()
    except sqlite3.OperationalError:
        if "supersedes" not in [r[1] for r in conn.execute("PRAGMA table_info(facts)")]:
            raise


def insert_round(conn, started_at: str, finished_at: str, duration_s: float,
                 sessoes: int, chars_total: int, fatos: int, decisoes: int,
                 pendencias: int, status: str, mensagem: str = None) -> None:
    conn.execute(
        "INSERT INTO chibi_rounds(started_at, finished_at, duration_s, sessoes, chars_total, "
        "fatos, decisoes, pendencias, status, mensagem) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (started_at, finished_at, duration_s, sessoes, chars_total,
         fatos, decisoes, pendencias, status, mensagem),
    )
    _prune_rounds(conn, keep=50)
    conn.commit()


def _prune_rounds(conn, keep: int) -> None:
    conn.execute(
        "DELETE FROM chibi_rounds WHERE id NOT IN "
        "(SELECT id FROM chibi_rounds ORDER BY id DESC LIMIT ?)",
        (keep,),
    )


def round_throughput(conn, n: int = 10) -> float | None:
    """Taxa agregada (chars/s) das últimas n rodadas concluídas; None sem dados válidos."""
    row = conn.execute(
        "SELECT SUM(chars_total), SUM(duration_s) FROM ("
        "SELECT chars_total, duration_s FROM chibi_rounds WHERE status='done' "
        "ORDER BY id DESC LIMIT ?)",
        (n,),
    ).fetchone()
    chars, secs = row[0], row[1]
    if not chars or not secs:
        return None
    return chars / secs


def recent_rounds(conn, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chibi_rounds ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def fact_exists(conn, project: str, kind: str, text: str, status: str = None) -> bool:
    norm = " ".join(text.lower().split())
    if status is None:
        rows = conn.execute(
            "SELECT text FROM facts WHERE project=? AND kind=?", (project, kind)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT text FROM facts WHERE project=? AND kind=? AND status=?", (project, kind, status)
        ).fetchall()
    return any(" ".join(r["text"].lower().split()) == norm for r in rows)


def active_fact_ids(conn, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id FROM facts WHERE id IN ({placeholders}) AND status != 'substituida'", ids
    ).fetchall()
    return {r["id"] for r in rows}


def enqueue(conn, session_id: str, cwd: str, transcript_path: str) -> None:
    """Idempotente: não rebaixa sessão já concluída; atualiza o caminho da transcrição.

    Uma sessão retomada em outra pasta (ex.: worktree) ganha um segundo JSONL com o mesmo
    session_id, e os hooks alternam entre os dois caminhos. O offset de cada arquivo fica
    guardado em transcript_offsets: voltar a um caminho já visto restaura o progresso dele
    (antes, cada troca zerava offset e tentativas, num ciclo que nunca terminava).
    BEGIN IMMEDIATE: leitura e escrita atômicas frente ao checkpoint do worker."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        _enqueue(conn, session_id, cwd, transcript_path)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _enqueue(conn, session_id: str, cwd: str, transcript_path: str) -> None:
    cur = conn.execute(
        "SELECT transcript_path, offset FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if cur is not None and cur["transcript_path"] != transcript_path:
        if cur["transcript_path"]:
            conn.execute(
                "INSERT INTO transcript_offsets(session_id, path, offset) VALUES(?,?,?) "
                "ON CONFLICT(session_id, path) DO UPDATE SET offset=excluded.offset",
                (session_id, cur["transcript_path"], cur["offset"]),
            )
        saved = conn.execute(
            "SELECT offset FROM transcript_offsets WHERE session_id=? AND path=?",
            (session_id, transcript_path),
        ).fetchone()
        if saved is not None:
            # status 'done' com bytes novos é retomado por pipeline._pending; tentativas mantidas
            conn.execute(
                "UPDATE sessions SET transcript_path=?, offset=? WHERE session_id=?",
                (transcript_path, saved["offset"], session_id),
            )
            return
    conn.execute(
        "INSERT INTO sessions(session_id, cwd, transcript_path, status, created_at) "
        "VALUES(?,?,?,'queued',?) "
        "ON CONFLICT(session_id) DO UPDATE SET "
        "offset=CASE WHEN transcript_path IS NOT excluded.transcript_path THEN 0 ELSE offset END, "
        "attempts=CASE WHEN transcript_path IS NOT excluded.transcript_path THEN 0 ELSE attempts END, "
        "status=CASE WHEN transcript_path IS NOT excluded.transcript_path THEN 'queued' ELSE status END, "
        "transcript_path=excluded.transcript_path",
        (session_id, cwd, transcript_path, datetime.now().isoformat(timespec="seconds")),
    )


def reset_session(conn, session_id: str) -> bool:
    """Devolve à fila uma sessão (ex.: em 'error' por tentativas esgotadas), mantendo o offset."""
    cur = conn.execute(
        "UPDATE sessions SET status='queued', attempts=0 WHERE session_id=?", (session_id,)
    )
    conn.commit()
    return cur.rowcount > 0
