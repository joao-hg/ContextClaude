import math
import re

from . import config
from .logutil import log

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[\w\-]+\.[\w\-]+\.[\w\-]+"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(r"(?<=://)[^/\s:@]+:[^@\s]+(?=@)"),
    re.compile(r"(?i)\bsenha\s+(?:é|e|eh)\s+\S+"),
    re.compile(
        r"""(?i)[\w.\-]{0,40}(?:password|passwd|senha|secret|token|api[_\-]?key|private[_\-]?key)[\w.\-]*["']?\s*[=:]\s*("[^"]*"|'[^']*'|\S+)"""
    ),
]


_TOKEN_RE = re.compile(r"[A-Za-z0-9+/_=\-]{20,}")
_ENTROPY_THRESHOLD = 4.3  # bits/char; pega tokens base64-like (charset amplo/alta entropia) sem
# falsos positivos em prosa comum. NÃO pega tokens só-hex: entropia máxima de hex é log2(16)=4.0,
# abaixo do limiar — limitação conhecida e aceita (baixá-lo geraria falsos positivos em identificadores).
_MIN_SECRET_LEN = 20


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {}
    for c in s:
        counts[c] = counts.get(c, 0) + 1
    length = len(s)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _looks_like_secret(token: str) -> bool:
    return len(token) >= _MIN_SECRET_LEN and _shannon_entropy(token) >= _ENTROPY_THRESHOLD


_extra_cache: dict[str, tuple[float, list]] = {}


def _extra_patterns() -> list:
    path = config.redact_patterns_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    key = str(path)
    cached = _extra_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        log(f"redact: arquivo de padrões ilegível, ignorando ({path!r}): {e!r}")
        # cacheado por mtime: se o arquivo virar legível sem o mtime mudar (ex. chmod), só
        # repopula depois de uma edição real que avance o mtime.
        _extra_cache[key] = (mtime, [])
        return []
    patterns = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line))
        except (re.error, OverflowError, RecursionError) as e:
            log(f"redact: padrão inválido ignorado ({line!r}): {e!r}")
    _extra_cache[key] = (mtime, patterns)
    return patterns


def redact(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub("[REDACTED]", text)
    text = _TOKEN_RE.sub(lambda m: "[REDACTED]" if _looks_like_secret(m.group()) else m.group(), text)
    for p in _extra_patterns():
        text = p.sub("[REDACTED]", text)
    return text


def redact_deep(value):
    """Aplica redact a todas as strings de listas/dicts aninhados."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_deep(v) for k, v in value.items()}
    return value
