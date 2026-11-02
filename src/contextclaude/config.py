"""Caminhos e constantes. Funções (não constantes) para permitir raiz temporária nos testes."""
import os
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:4b"
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


def chroma_path() -> Path:
    return root() / "data" / "chroma"


def lock_path() -> Path:
    return root() / "data" / "worker.lock"


def status_path() -> Path:
    return root() / "data" / "status.json"


def chibi_lock_path() -> Path:
    return root() / "data" / "chibi.lock"


def redact_patterns_path() -> Path:
    return Path(home()) / ".contextclaude" / "redact_patterns.txt"
