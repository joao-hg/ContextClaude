"""Caminhos e constantes. Funções (não constantes) para permitir raiz temporária nos testes."""
import os
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:4b"
APP_MODEL = "qwen3:4b-instruct-2507-q4_K_M"  # só o app; o worker segue com MODEL (a variante thinking vaza raciocínio)
RECALL_MAX_CHARS = 1500
EMBED_MODEL = "qwen3-embedding:0.6b"
PROMPT_MIN_CHARS = 15
PROMPT_MAX_CHARS = 800
PROMPT_INPUT_MAX = 2000  # trunca o prompt antes de redact/embed (custo e vazamento por blobs colados)
PROMPT_N = 4
PROMPT_MAX_DISTANCE = 0.5  # calibrado 2026-10-29: relevante 0.42-0.46, irrelevante >= 0.56 (amostra pequena)
PROMPT_EMBED_TIMEOUT = 4


def root() -> Path:
    env = os.environ.get("CONTEXTCLAUDE_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def home() -> str:
    return os.environ.get("CONTEXTCLAUDE_HOME") or os.path.expanduser("~")


def vault() -> Path:
    return root() / "vault"


def db_path() -> Path:
    return root() / "data" / "contextclaude.db"


def log_path() -> Path:
    return root() / "data" / "log.txt"


def _ascii_path(p: Path) -> Path:
    """O HNSW nativo do Chroma (1.5.9, Windows) não grava em caminho com caractere não-ASCII
    (ex.: C:\\Users\\João): só sai o index_metadata.pickle, e reabrir falha com
    'Error loading hnsw index' (verificado 2026-11-02). Usa o nome curto 8.3 (JOO~1)."""
    if str(p).isascii() or os.name != "nt":
        return p
    try:
        import ctypes
        p.mkdir(parents=True, exist_ok=True)
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, len(buf))
        if 0 < n < len(buf) and buf.value.isascii():
            return Path(buf.value)
    except Exception:
        pass
    return p


def chroma_path() -> Path:
    env = os.environ.get("CONTEXTCLAUDE_CHROMA")
    if env:
        return Path(env)
    return _ascii_path(root() / "data" / "chroma")


def lock_path() -> Path:
    return root() / "data" / "worker.lock"


def status_path() -> Path:
    return root() / "data" / "status.json"


def redact_patterns_path() -> Path:
    return Path(home()) / ".contextclaude" / "redact_patterns.txt"


def pause_path() -> Path:
    return root() / "data" / "pause"


def activity_path() -> Path:
    return root() / "data" / "atividade.jsonl"
