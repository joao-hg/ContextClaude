import pytest

from contextclaude import ollama


@pytest.fixture(autouse=True)
def _no_real_consolidation(monkeypatch):
    """Nenhum teste chama o Ollama de verdade para consolidar o registro da sessão."""
    def fake(current, new):
        feitos = list(dict.fromkeys((current.get("feitos") or []) + (new.get("feitos") or [])))
        resumo = " ".join([current.get("resumo") or ""] + (new.get("resumos") or [])).strip()
        return {"titulo": current.get("titulo") or new.get("titulo") or "Sessão", "resumo": resumo, "feitos": feitos}
    monkeypatch.setattr(ollama, "consolidate_session", fake)


@pytest.fixture(autouse=True)
def _isolated_data_root(monkeypatch, tmp_path):
    """Nenhum teste escreve em data/ do repositório (log.txt, banco): a raiz padrão é um tmp."""
    monkeypatch.setenv("CONTEXTCLAUDE_ROOT", str(tmp_path))
