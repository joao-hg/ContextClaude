import re

from . import notes
from .logutil import log


def normalize(name: str) -> str:
    """Minúsculas, strip() e espaços internos colapsados em um só."""
    return re.sub(r"\s+", " ", name.strip().lower())


def _load(conn, projeto: str | None):
    """Carrega todas as linhas de `relations` (filtradas por `projeto` se informado).

    Devolve:
    - display: dict chave_normalizada -> primeira grafia original vista (ordem de id ASC).
    - phys: lista de (origem_normalizada, relacao, destino_normalizada) por índice.
    - adj: dict chave_normalizada -> [(índice_da_aresta, é_origem: bool), ...].
    """
    if projeto is None:
        rows = conn.execute(
            "SELECT source, relation, target FROM relations ORDER BY id ASC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT source, relation, target FROM relations WHERE project = ? ORDER BY id ASC",
            (projeto,),
        ).fetchall()

    display: dict[str, str] = {}
    phys: list[tuple[str, str, str]] = []
    adj: dict[str, list[tuple[int, bool]]] = {}

    for row in rows:
        source, relation, target = row["source"], row["relation"], row["target"]
        src_key = normalize(source)
        dst_key = normalize(target)
        display.setdefault(src_key, source)
        display.setdefault(dst_key, target)

        idx = len(phys)
        phys.append((src_key, relation, dst_key))
        adj.setdefault(src_key, []).append((idx, True))
        adj.setdefault(dst_key, []).append((idx, False))

    return display, phys, adj


def neighbors(conn, entity: str, projeto: str | None = None, depth: int = 2, limit: int = 100) -> list[dict]:
    display, phys, adj = _load(conn, projeto)

    start = normalize(entity)
    out: list[dict] = []
    seen_edges: set[int] = set()
    # Dedup por tripla normalizada (src, relação, dst): a mesma relação é reextraída em
    # cada sessão que a toca, então `seen_edges` (índice de linha física) não basta —
    # cada reextração é uma linha física nova, mas é logicamente a mesma aresta. Também
    # normaliza a relação, para unificar variantes de caixa como "usa"/"Usa".
    seen_triples: set[tuple[str, str, str]] = set()
    frontier: list[str] = [start]
    visited_nodes = {start}

    for level in range(1, depth + 1):
        next_frontier: list[str] = []
        for node in frontier:
            for idx, _is_source in adj.get(node, []):
                if idx in seen_edges:
                    continue
                seen_edges.add(idx)
                src_key, relation, dst_key = phys[idx]
                for key in (src_key, dst_key):
                    if key not in visited_nodes:
                        visited_nodes.add(key)
                        next_frontier.append(key)
                triple = (src_key, normalize(relation), dst_key)
                if triple in seen_triples:
                    continue
                seen_triples.add(triple)
                out.append({
                    "origem": display[src_key],
                    "relacao": relation,
                    "destino": display[dst_key],
                    "depth": level,
                })
                if len(out) >= limit:
                    return out
        if not next_frontier:
            break
        frontier = next_frontier

    return out


def display_name(conn, entity: str, projeto: str | None = None) -> str:
    display, _phys, _adj = _load(conn, projeto)
    return display.get(normalize(entity), entity)


def write_entities(conn, session_id: str, project: str) -> None:
    """Para cada entidade (origem ou destino) vista nas relações desta sessão, (re)escreve
    sua página de entidade com o grafo completo (não só desta sessão) ao seu redor."""
    rows = conn.execute(
        "SELECT source, target FROM relations WHERE session_id = ? AND project = ?",
        (session_id, project),
    ).fetchall()

    chaves: set[str] = set()
    for row in rows:
        chaves.add(normalize(row["source"]))
        chaves.add(normalize(row["target"]))

    for chave in sorted(chaves):
        if not chave:
            continue
        try:
            vizinhos = neighbors(conn, chave, projeto=project, depth=1)
            notes.write_entity_note(project, chave, display_name(conn, chave, projeto=project), vizinhos)
        except OSError as exc:
            log(f"grafo: falha ao gravar página da entidade {chave!r} do projeto {project!r}: {exc!r}")
