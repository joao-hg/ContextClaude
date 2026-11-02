import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from . import config

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f#^\[\]]')
_MAX_STEM = 120


def _dir(project: str) -> Path:
    return config.vault() / (_BAD.sub("_", project).strip(" .") or "_sem-nome")


def _stem(name: str) -> str:
    """Normaliza (minúsculas, espaços colapsados) e sanitiza `name` para servir tanto de
    nome de arquivo de entidade quanto de alvo de wikilink — ver `_entity_link` — de modo
    que arquivo e link nunca divirjam. Trunca em `_MAX_STEM` caracteres e, se o resultado
    ficar vazio, cai para um hash determinístico em vez de um arquivo sem nome."""
    norm = re.sub(r"\s+", " ", (name or "").strip().lower())
    stem = _BAD.sub("_", norm).strip(" .")[:_MAX_STEM].strip(" .")
    if not stem:
        stem = "e" + hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:8]
    return stem


def _entity_link(name: str, display: str | None = None) -> str:
    """Wikilink no formato `[[alvo-real|rótulo]]`: o alvo é o mesmo stem usado no nome do
    arquivo da página da entidade (`_stem`), então o link sempre resolve à página certa,
    mesmo quando `name` tem `/ \\ : # ^ [ ] |` ou difere em caixa/espaços."""
    label = display if display is not None else name
    return f"[[{_stem(name)}|{label}]]"


def _bullets(items) -> str:
    out = [f"- {i}" for i in items if isinstance(i, str) and i.strip()]
    return "\n".join(out) if out else "_nada_"


def _rel(r) -> str | None:
    if not isinstance(r, dict) or not all(isinstance(r.get(k), str) and r.get(k) for k in ("origem", "relacao", "destino")):
        return None
    return f"- {_entity_link(r['origem'])} —{r['relacao']}→ {_entity_link(r['destino'])}"


def write_session_note(project: str, session_id: str, when: datetime, data: dict) -> Path:
    d = _dir(project) / "Sessões"
    d.mkdir(parents=True, exist_ok=True)
    base = when.strftime("%Y-%m-%d-%H%M")
    path, n = d / f"{base}.md", 2
    while path.exists():
        path, n = d / f"{base}-{n}.md", n + 1
    rels = "\n".join(x for x in map(_rel, data.get("relacoes", [])) if x) or "_nada_"
    body = (
        f"---\nprojeto: {project}\nsessao: {session_id}\ndata: {when.isoformat(timespec='seconds')}\n---\n\n"
        f"## Resumo\n{data.get('resumo') or '_nada_'}\n\n"
        f"## Decisões\n{_bullets(data.get('decisoes', []))}\n\n"
        f"## Fatos\n{_bullets(data.get('fatos', []))}\n\n"
        f"## Pendências\n{_bullets(data.get('pendencias', []))}\n\n"
        f"## Relações\n{rels}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def read_summary(project: str) -> str | None:
    p = _dir(project) / "_resumo.md"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def write_summary(project: str, text: str) -> Path:
    d = _dir(project)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "_resumo.md"
    p.write_text(text, encoding="utf-8")
    return p


def write_entity_note(projeto: str, key: str, display: str, relations: list[dict]) -> Path:
    d = _dir(projeto) / "Entidades"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_stem(key)}.md"
    rels = "\n".join(x for x in map(_rel, relations) if x) or "_nada_"
    body = (
        f"---\nprojeto: {projeto}\nentidade: {json.dumps(display)}\n---\n\n"
        f"# {display}\n\n"
        f"## Relações\n{rels}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


_KIND_TO_SECTION = {"fato": "fatos", "decisao": "decisoes", "pendencia": "pendencias"}
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SESSAO = re.compile(r"^sessao: *(.*?) *$", re.MULTILINE)
_RESUMO = re.compile(r"^## Resumo\n(.*?)\n\n## (?:Decisões|Fatos|Pendências|Relações)\n", re.S | re.M)
_PLACEHOLDER = "_nada_"


def _index_session_notes(project: str, session_ids: set[str]) -> dict[str, list[tuple[Path, str]]]:
    """Mapeia cada `sessao` de `session_ids` às notas existentes em `Sessões/` e ao texto do
    `## Resumo` de cada uma (vazio quando não há resumo real)."""
    out: dict[str, list[tuple[Path, str]]] = {}
    d = _dir(project) / "Sessões"
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _FRONT.match(text)
        sm = _SESSAO.search(fm.group(1)) if fm else None
        if not sm or sm.group(1) not in session_ids:
            continue
        rm = _RESUMO.search(text)
        resumo = rm.group(1).strip() if rm else ""
        out.setdefault(sm.group(1), []).append((path, "" if resumo == _PLACEHOLDER else resumo))
    return out


def rebuild_project(conn, project: str) -> int:
    """Reconstrói as notas de sessão e páginas de entidade do vault a partir do SQLite
    (fonte da verdade). Retorna o número de sessões reconstruídas.

    Idempotente e sem estado parcial: por sessão, grava a nota nova ANTES de apagar as notas
    antigas dessa sessão (nunca fica sem nota). O `## Resumo` real das notas antigas é
    preservado na nova; apagar uma nota antiga que falhe (ex.: bloqueada) é ignorado.

    Limitações conhecidas: `_resumo.md` NÃO é regenerado — o resumo corrente é produzido por
    merge via LLM sobre o histórico ordenado e não é derivável de `facts`. Sessões sem nota
    antiga com resumo ficam com `## Resumo` = `_nada_`, pois o resumo por sessão vem do LLM
    e não é persistido no SQLite."""
    from . import graph  # import local: graph.py importa notes no topo; evita ciclo

    session_ids = {r["session_id"] for r in conn.execute(
        "SELECT DISTINCT session_id FROM facts WHERE project=?", (project,))}
    session_ids |= {r["session_id"] for r in conn.execute(
        "SELECT DISTINCT session_id FROM relations WHERE project=?", (project,))}
    old = _index_session_notes(project, session_ids)
    count = 0
    for sid in sorted(session_ids):
        stamp = conn.execute(
            "SELECT created_at FROM facts WHERE session_id=? AND project=? ORDER BY created_at LIMIT 1",
            (sid, project),
        ).fetchone()
        when = datetime.fromisoformat(stamp["created_at"]) if stamp and stamp["created_at"] else datetime.now()
        data: dict = {"resumo": "", "decisoes": [], "fatos": [], "pendencias": [], "relacoes": []}
        for f in conn.execute("SELECT kind, text FROM facts WHERE session_id=? AND project=?", (sid, project)):
            section = _KIND_TO_SECTION.get(f["kind"])
            if section:
                data[section].append(f["text"])
        for r in conn.execute("SELECT source, relation, target FROM relations WHERE session_id=? AND project=?",
                              (sid, project)):
            data["relacoes"].append({"origem": r["source"], "relacao": r["relation"], "destino": r["target"]})
        antigas = old.get(sid, [])
        resumos = list(dict.fromkeys(r for _, r in antigas if r))
        data["resumo"] = "\n\n".join(resumos)
        nova = write_session_note(project, sid, when, data)
        graph.write_entities(conn, sid, project)
        for path, _ in antigas:
            if path == nova:
                continue
            try:
                path.unlink()
            except OSError:
                continue
        count += 1
    return count
