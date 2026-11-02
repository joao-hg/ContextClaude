"""Entry point dos hooks do Claude Code: hook.py start|end|prompt (payload JSON no stdin).

Sempre sai com 0 e não escreve nada, exceto o recall opcional (SessionStart/UserPromptSubmit).
Nada fora do try: até falha de import precisa ser silenciosa.
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


_out_lock = threading.Lock()
WATCHDOG_SECONDS = 6  # o hook de prompt tem timeout de 8 s no settings.json


def _emit(real_out, event: str, text: str, system_message: str = None) -> None:
    out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    if system_message:
        out["systemMessage"] = system_message
    with _out_lock:
        real_out.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        real_out.flush()


def _watchdog() -> None:
    with _out_lock:  # espera uma escrita em andamento terminar
        os._exit(0)


def main() -> None:
    # Só o JSON do recall pode chegar ao stdout real; qualquer print de biblioteca vai para o devnull.
    real_out = sys.stdout.buffer
    try:
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")
    except BaseException:
        pass
    try:
        from contextclaude import hooks
        from contextclaude.logutil import log
    except BaseException:
        return
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else ""
        if mode == "prompt":
            seconds = float(os.environ.get("CONTEXTCLAUDE_WATCHDOG", WATCHDOG_SECONDS))
            t = threading.Timer(seconds, _watchdog)
            t.daemon = True
            t.start()
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return
        if mode == "start":
            recall = hooks.session_start(payload)
            if recall:
                _emit(real_out, "SessionStart", recall, system_message=recall)
        elif mode == "end":
            hooks.session_end(payload)
        elif mode == "prompt":
            extra = hooks.prompt_submit(payload)
            if extra:
                _emit(real_out, "UserPromptSubmit", extra)
    except BaseException as e:
        try:
            log(f"hook {sys.argv[1:]} falhou: {e!r}")
        except BaseException:
            pass


if __name__ == "__main__":
    main()
    try:
        sys.stderr = open(os.devnull, "w")  # evita ruído de pipe quebrado no encerramento
    except BaseException:
        pass
    os._exit(0)
