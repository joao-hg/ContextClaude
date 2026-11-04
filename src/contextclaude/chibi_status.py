import json
import os
import time
from datetime import datetime

from . import config

_REPLACE_RETRIES = 5
_REPLACE_RETRY_DELAY_S = 0.02

ESTADOS = ("idle", "pensando", "digitando", "processando", "feliz", "notificacoes")
# Estados "pulso": transientes, podem ser sobrescritos por outra escrita em
# milissegundos (ex.: feliz -> idle, notificacoes -> idle). pulso_estado/
# pulso_evento_id guardam o último pulso visto separadamente de estado/
# evento_id "ao vivo", para o widget conseguir detectar e exibir o pulso
# mesmo que o poll só rode depois que o estado "ao vivo" já tenha avançado.
PULSO_ESTADOS = frozenset({"feliz", "notificacoes"})
_DEFAULT = {"estado": "idle", "sessao": None, "evento_id": 0, "ts": "",
            "pulso_estado": None, "pulso_evento_id": 0,
            "rodada": None, "ultima_rodada": None}


def read_status() -> dict:
    try:
        with open(config.status_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("estado") in ESTADOS:
            merged = dict(_DEFAULT)
            merged.update(data)
            return merged
    except Exception:
        pass
    return dict(_DEFAULT)


def write_status(estado: str, sessao: str = None, now=datetime.now) -> None:
    if estado not in ESTADOS:
        return
    try:
        path = config.status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = read_status()
        evento_id = prev.get("evento_id", 0) + 1
        if estado in PULSO_ESTADOS:
            pulso_estado, pulso_evento_id = estado, evento_id
        else:
            pulso_estado = prev.get("pulso_estado")
            pulso_evento_id = prev.get("pulso_evento_id", 0)
        data = {"estado": estado, "sessao": sessao, "evento_id": evento_id,
                "ts": now().isoformat(timespec="seconds"),
                "pulso_estado": pulso_estado, "pulso_evento_id": pulso_evento_id,
                "rodada": prev.get("rodada"), "ultima_rodada": prev.get("ultima_rodada")}
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _write_data(data: dict) -> None:
    path = config.status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    # status.json pode estar aberto por outro processo no instante exato do
    # replace (o widget faz polling duas vezes por tick de 400ms); tenta de
    # novo algumas vezes antes de desistir, em vez de perder a escrita.
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_RETRY_DELAY_S)


def write_rodada_inicio(inicio_ts: str, estimativa_s: float | None, sessoes: int) -> None:
    try:
        data = dict(read_status())
        data["rodada"] = {"ativa": True, "inicio": inicio_ts,
                          "estimativa_s": estimativa_s, "sessoes": sessoes}
        _write_data(data)
    except Exception:
        pass


def write_rodada_fim(status: str, duration_s: float, sessoes: int, fatos: int,
                     decisoes: int, mensagem: str | None = None) -> None:
    try:
        prev = read_status()
        data = dict(prev)
        evento_id = ((prev.get("ultima_rodada") or {}).get("evento_id", 0)) + 1
        data["rodada"] = None
        data["ultima_rodada"] = {"evento_id": evento_id, "status": status,
                                 "duration_s": duration_s, "sessoes": sessoes,
                                 "fatos": fatos, "decisoes": decisoes,
                                 "mensagem": mensagem}
        _write_data(data)
    except Exception:
        pass
