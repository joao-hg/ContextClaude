"""Registro curto do que o worker está fazendo (data/atividade.jsonl), para o app mostrar."""
import json
from datetime import datetime

from . import config

MAX_LINES = 400
KEEP_LINES = 200


def record(texto: str, tipo: str = "info") -> None:
    """Acrescenta uma linha; passando de MAX_LINES mantém as últimas KEEP_LINES. Nunca levanta."""
    try:
        p = config.activity_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "texto": texto, "tipo": tipo},
                          ensure_ascii=False)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > MAX_LINES:
            p.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8", newline="\n")
    except Exception:
        pass


def recent(limit: int = 30) -> list[dict]:
    """Registros mais novos primeiro; linhas inválidas são ignoradas."""
    try:
        lines = config.activity_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    out = []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and "texto" in r:
            out.append(r)
    return out
