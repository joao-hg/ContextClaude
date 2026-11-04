"""Lê o JSONL de transcrição do Claude Code.

Formato (confirmado em 2026-10-29): uma linha JSON por evento. Interessam as de
type "user"/"assistant" com message.role e message.content, que é str ou lista
de blocos {type: text|tool_use|tool_result|thinking|image}. Só blocos "text" e
strings contam; blocos <system-reminder> injetados são removidos.
"""
import json
import os
import re

from .redact import redact

MAX_MSG = 4000
MAX_ASSISTANT = 2000
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
# Mensagens "user" que o próprio Claude Code injeta (skills, notificações, comandos): não são fala do usuário.
_INJECTED = ("Base directory for this skill", "<task-notification", "<command-", "<local-command", "[SYSTEM NOTIFICATION")
_LABEL = {"user": "Usuário", "assistant": "Claude"}


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _clip(text: str, role: str) -> str:
    """Respostas longas do Claude são, em geral, código/tabelas; o que dura costuma estar no
    começo (plano) e no fim (conclusão). Mantém as duas pontas e corta o meio."""
    if role == "assistant" and len(text) > MAX_ASSISTANT:
        head = MAX_ASSISTANT * 3 // 5
        return text[:head] + "\n[…]\n" + text[-(MAX_ASSISTANT - head):]
    return text[:MAX_MSG]


def read_new(path: str, offset: int, with_pos: bool = False) -> tuple[list, int]:
    """Linhas de fala novas a partir de `offset` e o novo offset (fim da última linha completa).
    Com with_pos=True, cada item é (linha, offset_logo_após_ela), para checkpoint por bloco."""
    if not path or not os.path.isfile(path):
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1
    lines: list = []
    pos = offset
    for raw in data[:end].split(b"\n")[:-1]:
        pos += len(raw) + 1
        try:
            d = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") not in _LABEL:
            continue
        msg = d.get("message")
        if not isinstance(msg, dict):
            continue
        text = redact(_REMINDER.sub("", _text_of(msg.get("content")))).strip()
        if text and not (d["type"] == "user" and text.startswith(_INJECTED)):
            line = f"{_LABEL[d['type']]}: {_clip(text, d['type'])}"
            lines.append((line, pos) if with_pos else line)
    return lines, offset + end
