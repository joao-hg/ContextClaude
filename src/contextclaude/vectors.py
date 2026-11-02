"""Índice semântico (ChromaDB) derivado do SQLite. Pode ser apagado e reconstruído com rebuild()."""
import urllib.error
from concurrent.futures import ThreadPoolExecutor

from . import config, embed
from .logutil import log

COLLECTION = "memoria"
BATCH = 32
_client = None


def _get_client():
    global _client
    if _client is None:
        import chromadb
        from chromadb.config import Settings
        config.chroma_path().mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(config.chroma_path()), settings=Settings(anonymized_telemetry=False))
    return _client


def close() -> None:
    """Solta o client (testes e reconstrução)."""
    global _client
    _client = None
    try:
        from chromadb.api.shared_system_client import SharedSystemClient
        SharedSystemClient.clear_system_cache()
    except Exception:
        pass


def _collection():
    return _get_client().get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def index_pending(conn, embed_fn=embed.embed, limit: int = 500) -> int:
    """Indexa fatos com indexed=0. Falha de embedding propaga e deixa o restante pendente."""
    rows = conn.execute(
        "SELECT id, session_id, project, kind, text, created_at FROM facts WHERE indexed=0 ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return 0
    col = _collection()
    done = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        try:
            _embed_upsert(col, batch, embed_fn)
            ok = batch
        except Exception as e:
            if _is_down(e):
                raise
            ok = []  # item "venenoso" (ex.: NaN no embedding): isola em vez de travar a fila toda
            for r in batch:
                try:
                    _embed_upsert(col, [r], embed_fn)
                    ok.append(r)
                except Exception as e2:
                    if _is_down(e2):
                        raise
                    log(f"fato {r['id']} não indexável: {e2!r}")
                    conn.execute("UPDATE facts SET indexed=2 WHERE id=?", (r["id"],))
                    conn.commit()
        conn.executemany("UPDATE facts SET indexed=1 WHERE id=?", [(r["id"],) for r in ok])
        conn.commit()
        done += len(ok)
    return done


def _is_down(e: Exception) -> bool:
    """Ollama fora/ocupado: tenta de novo depois. Qualquer outro erro é do item."""
    if isinstance(e, urllib.error.HTTPError):
        return False
    return isinstance(e, (ConnectionError, TimeoutError, urllib.error.URLError))


def _embed_upsert(col, batch, embed_fn) -> None:
    vecs = embed_fn([r["text"] for r in batch])
    if len(vecs) != len(batch):
        raise ValueError(f"{len(vecs)} vetores para {len(batch)} textos")
    col.upsert(
        ids=[f"fato-{r['id']}" for r in batch],
        embeddings=vecs,
        documents=[r["text"] for r in batch],
        metadatas=[{"projeto": r["project"] or "", "tipo": r["kind"] or "",
                    "sessao": r["session_id"] or "", "data": r["created_at"] or ""} for r in batch],
    )


def rebuild(conn, embed_fn=embed.embed) -> int:
    try:
        _get_client().delete_collection(COLLECTION)
    except Exception:
        pass
    conn.execute("UPDATE facts SET indexed=0")
    conn.commit()
    return index_pending(conn, embed_fn, limit=-1)


def search(query: str, projeto=None, n: int = 5, embed_fn=embed.embed, timeout: float = 60, kind: str = None) -> list[dict]:
    if not config.chroma_path().exists():
        return []
    # o embedding (Ollama) e o import do chromadb são lentos e independentes: rodam em paralelo
    pool = ThreadPoolExecutor(max_workers=1)
    pending = pool.submit(embed_fn, [query], timeout=timeout)
    try:
        try:
            col = _get_client().get_collection(COLLECTION)
        except Exception:
            return []
        total = col.count()
        if not total:
            return []
        vec = pending.result()[0]
    finally:
        pool.shutdown(wait=False)
    conds = [{"projeto": projeto}] if projeto else []
    if kind:
        conds.append({"tipo": kind})
    where = conds[0] if len(conds) == 1 else ({"$and": conds} if conds else None)
    res = col.query(
        query_embeddings=[vec], n_results=min(n, total),
        where=where,
    )
    out = []
    for id_, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
        out.append({"id": int(id_.split("-", 1)[1]), "text": doc, "projeto": meta.get("projeto", ""),
                    "tipo": meta.get("tipo", ""), "data": meta.get("data", ""), "distance": dist})
    return sorted(out, key=lambda r: r["distance"])
