# Assistente de anotações (chibi v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar ao chibi um ciclo completo de "assistente de anotações": avisar (balão + toast nativo) quando uma rodada de anotação começa e termina, mostrar um tempo de espera que aprende com o histórico, e permitir ver o histórico de rodadas com um clique.

**Architecture:** `pipeline.process_queue` abre e fecha uma "rodada" por chamada (nunca por sessão), gravando o fechamento em uma tabela sqlite nova (`chibi_rounds`) e em dois blocos novos de `status.json` (`rodada`/`ultima_rodada`), seguindo exatamente o padrão já existente de `pulso_estado`/`pulso_evento_id`. `chibi_widget.py` faz polling desses blocos para atualizar uma segunda janela Tk (balão de texto) e disparar um toast nativo via `pywin32` (módulo isolado `chibi_notify.py`, só importado pelo processo do widget). Um painel de histórico (terceira `Toplevel`) lê `chibi_rounds` direto via sqlite (WAL, leitura concorrente segura) e abre com um clique simples, distinguido de arraste/duplo-clique por um debounce.

**Tech Stack:** Python 3.13, sqlite3 (stdlib), Tkinter, Pillow (já usado), `pywin32` (novo).

**Spec:** `docs/superpowers/specs/2026-10-31-chibi-annotation-assistant-design.md`

## Global Constraints

- Windows apenas; o pipeline não pode ficar mais lento ou instável; escrita de status é essencialmente grátis (igual hoje).
- `pywin32` é dependência nova em `requirements.txt`. Só `chibi_notify.py` e `chibi_widget.py` (processo do widget) a importam — `pipeline.py`/`worker.py` nunca importam Pillow, tkinter ou `pywin32`.
- `Shell_NotifyIcon` usa `root.winfo_id()` como HWND; não precisa de message loop dedicada (sem interatividade no ícone de bandeja).
- Toda `write_rodada_inicio` tem exatamente uma `write_rodada_fim` correspondente, garantido estruturalmente por `try/finally` em `process_queue` — inclusive em retornos antecipados (`down`) e exceções não previstas.
- Rodada com qualquer sessão que esgota tentativas (`_fail` marca `status='error'`) ou com `down` fecha como `status='error'`, mesmo que outras sessões tenham sido concluídas no mesmo lote.
- `chars_total` gravado no fechamento é consumo real (bytes efetivamente lidos), nunca a estimativa pré-scan usada só para o ETA de abertura.
- `db.round_throughput` é taxa agregada — `soma(chars_total)/soma(duration_s)` das últimas N rodadas `done` — nunca a média das taxas rodada a rodada.
- `chibi_rounds` é podada automaticamente (mantém só as últimas 50 linhas) a cada inserção — nunca cresce sem limite.
- Clique simples (abre painel de histórico) tem um debounce de 250ms cancelável para não quebrar o fechamento existente por duplo-clique (`<Double-Button-1>`) ou botão direito (`<Button-3>`).
- Qualquer falha em `chibi_notify.notify` (incluindo `pywin32` ausente) é engolida, nunca propaga.

## Review Focus

- Fila vazia (`process_queue` sem sessões pendentes): não deve abrir rodada nem notificar nada — pinned no Task 3.
- Falha parcial dentro de uma rodada (uma sessão esgota tentativas, outras concluem): a rodada fecha como `error`, não `done`, e `chars_total` reflete só o que foi de fato consumido — pinned no Task 3.
- Rodada que atravessa várias passagens internas do loop de `process_queue` (sessão retomada reprocessada na mesma chamada) ainda conta como **uma** rodada (uma linha em `chibi_rounds`) — pinned no Task 3.
- Exceção não prevista em qualquer ponto do processamento de uma sessão (não só Ollama fora do ar) ainda fecha a rodada corretamente — pinned no Task 3.
- Balão de texto quando o tempo decorrido já passou da estimativa (ETA otimista demais) não deve mostrar contagem regressiva negativa — pinned no Task 5.

---

## Task 1: `chibi_rounds` no sqlite (`db.py`)

**Files:**
- Modify: `src/contextclaude/db.py`
- Test: `tests/test_db_project.py`

**Interfaces:**
- Produces: `db.insert_round(conn, started_at: str, finished_at: str, duration_s: float, sessoes: int, chars_total: int, fatos: int, decisoes: int, pendencias: int, status: str, mensagem: str = None) -> None` (poda automaticamente para as últimas 50 linhas após inserir); `db.round_throughput(conn, n: int = 10) -> float | None`; `db.recent_rounds(conn, limit: int = 10) -> list[sqlite3.Row]` (ordem mais recente primeiro).

- [ ] **Step 1: Escrever os testes que falham, em `tests/test_db_project.py`, numa classe nova `TestChibiRounds(Base)`**

```python
class TestChibiRounds(Base):
    def test_insert_and_recent_rounds_returns_newest_first(self):
        db.insert_round(self.conn, "2026-10-31T10:00:00", "2026-10-31T10:01:00", 60.0,
                         2, 1000, 3, 1, 0, "done")
        db.insert_round(self.conn, "2026-10-31T10:02:00", "2026-10-31T10:02:30", 30.0,
                         1, 500, 1, 0, 0, "done")
        rows = db.recent_rounds(self.conn, limit=10)
        self.assertEqual([r["sessoes"] for r in rows], [1, 2])

    def test_round_throughput_none_when_no_done_rounds(self):
        self.assertIsNone(db.round_throughput(self.conn))

    def test_round_throughput_is_aggregate_sum_not_average_of_ratios(self):
        # rodada A: 100 chars em 1s (taxa 100/s); rodada B: 10000 chars em 100s (taxa 100/s)
        # média das taxas == 100; mas a soma/soma também dá 100 aqui -- usar
        # tamanhos que DIVIRJAM entre os dois métodos:
        db.insert_round(self.conn, "t1", "t1", 1.0, 1, 100, 0, 0, 0, "done")
        db.insert_round(self.conn, "t2", "t2", 100.0, 1, 5000, 0, 0, 0, "done")
        # média das taxas: (100/1 + 5000/100) / 2 = 75.0
        # agregada: (100+5000)/(1+100) = 50.49...
        self.assertAlmostEqual(db.round_throughput(self.conn), 5100 / 101, places=3)

    def test_round_throughput_ignores_error_rounds(self):
        db.insert_round(self.conn, "t1", "t1", 1.0, 1, 100, 0, 0, 0, "error", "falhou")
        self.assertIsNone(db.round_throughput(self.conn))

    def test_insert_round_prunes_to_last_50(self):
        for i in range(55):
            db.insert_round(self.conn, str(i), str(i), 1.0, 1, 10, 0, 0, 0, "done")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM chibi_rounds").fetchone()[0], 50)
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python -m pytest tests/test_db_project.py -v -k ChibiRounds`
Expected: FAIL (`chibi_rounds` não existe / `insert_round` não existe)

- [ ] **Step 3: Adicionar a tabela ao `SCHEMA` em `db.py`**

Acrescentar ao final da string `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS chibi_rounds(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT, duration_s REAL,
    sessoes INTEGER, chars_total INTEGER,
    fatos INTEGER, decisoes INTEGER, pendencias INTEGER,
    status TEXT NOT NULL,
    mensagem TEXT
);
```

- [ ] **Step 4: Implementar `insert_round`, `round_throughput`, `recent_rounds` e um helper privado `_prune_rounds` em `db.py`**

`insert_round` insere a linha e chama `_prune_rounds(conn, keep=50)` antes do commit. `round_throughput` soma `chars_total`/`duration_s` das últimas `n` linhas com `status='done'` (subquery `ORDER BY id DESC LIMIT n`) e devolve `None` se a soma de `duration_s` ou de `chars_total` for 0/vazia. `recent_rounds` devolve `ORDER BY id DESC LIMIT limit`. `_prune_rounds(conn, keep)` apaga por `id NOT IN (SELECT id FROM chibi_rounds ORDER BY id DESC LIMIT keep)`.

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `python -m pytest tests/test_db_project.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/contextclaude/db.py tests/test_db_project.py
git commit -m "feat(db): adiciona tabela chibi_rounds e agregação de throughput"
```

---

## Task 2: Campos de rodada em `chibi_status.py`

**Files:**
- Modify: `src/contextclaude/chibi_status.py`
- Test: `tests/test_chibi_status.py`

**Interfaces:**
- Consumes: nada de outras tasks.
- Produces: `chibi_status.write_rodada_inicio(inicio_ts: str, estimativa_s: float | None, sessoes: int) -> None`; `chibi_status.write_rodada_fim(status: str, duration_s: float, sessoes: int, fatos: int, decisoes: int, mensagem: str | None = None) -> None`. `read_status()` passa a incluir as chaves `"rodada"` (default `None`) e `"ultima_rodada"` (default `None`). `write_status` preserva ambos os campos inalterados a cada chamada.

- [ ] **Step 1: Atualizar o teste existente que quebra com o novo default, em `tests/test_chibi_status.py`**

```python
def test_read_status_default_when_file_missing(self):
    self.assertEqual(chibi_status.read_status(),
                      {"estado": "idle", "sessao": None, "evento_id": 0, "ts": "",
                       "pulso_estado": None, "pulso_evento_id": 0,
                       "rodada": None, "ultima_rodada": None})
```

- [ ] **Step 2: Escrever os testes novos que falham, em `tests/test_chibi_status.py`**

```python
def test_write_rodada_inicio_sets_ativa_block(self):
    chibi_status.write_rodada_inicio("2026-10-31T10:00:00", 42.0, 2)
    out = chibi_status.read_status()
    self.assertEqual(out["rodada"], {"ativa": True, "inicio": "2026-10-31T10:00:00",
                                      "estimativa_s": 42.0, "sessoes": 2})
    self.assertIsNone(out["ultima_rodada"])

def test_write_rodada_fim_clears_rodada_and_sets_ultima_rodada(self):
    chibi_status.write_rodada_inicio("2026-10-31T10:00:00", 42.0, 2)
    chibi_status.write_rodada_fim("done", 50.0, 2, 3, 1)
    out = chibi_status.read_status()
    self.assertIsNone(out["rodada"])
    self.assertEqual(out["ultima_rodada"],
                      {"evento_id": 1, "status": "done", "duration_s": 50.0,
                       "sessoes": 2, "fatos": 3, "decisoes": 1, "mensagem": None})

def test_write_rodada_fim_evento_id_increments_across_rounds(self):
    chibi_status.write_rodada_fim("done", 1.0, 1, 0, 0)
    chibi_status.write_rodada_fim("error", 2.0, 0, 0, 0, "Ollama fora do ar")
    out = chibi_status.read_status()
    self.assertEqual(out["ultima_rodada"]["evento_id"], 2)
    self.assertEqual(out["ultima_rodada"]["mensagem"], "Ollama fora do ar")

def test_write_status_preserves_rodada_fields_across_normal_writes(self):
    chibi_status.write_rodada_inicio("2026-10-31T10:00:00", None, 1)
    chibi_status.write_status("pensando", sessao="s1")
    out = chibi_status.read_status()
    self.assertEqual(out["estado"], "pensando")
    self.assertTrue(out["rodada"]["ativa"])
```

- [ ] **Step 3: Rodar os testes e confirmar que falham**

Run: `python -m pytest tests/test_chibi_status.py -v`
Expected: FAIL (`write_rodada_inicio`/`write_rodada_fim` não existem; default antigo não tem as chaves novas)

- [ ] **Step 4: Adicionar `"rodada": None, "ultima_rodada": None` a `_DEFAULT` em `chibi_status.py`**

- [ ] **Step 5: Em `write_status`, acrescentar ao dict `data` as chaves `"rodada": prev.get("rodada"), "ultima_rodada": prev.get("ultima_rodada")`**

- [ ] **Step 6: Implementar `write_rodada_inicio` e `write_rodada_fim` em `chibi_status.py`**

Mesmo padrão de escrita atômica de `write_status` (ler `prev = read_status()`, copiar para `data = dict(prev)`, sobrescrever só os campos relevantes, escrever em `.tmp` e `os.replace`), envolto em `try/except Exception: pass`. `write_rodada_inicio` sobrescreve `data["rodada"]` com `{"ativa": True, "inicio": inicio_ts, "estimativa_s": estimativa_s, "sessoes": sessoes}`. `write_rodada_fim` calcula `evento_id = ((prev.get("ultima_rodada") or {}).get("evento_id", 0)) + 1`, sobrescreve `data["rodada"] = None` e `data["ultima_rodada"] = {"evento_id": evento_id, "status": status, "duration_s": duration_s, "sessoes": sessoes, "fatos": fatos, "decisoes": decisoes, "mensagem": mensagem}`.

- [ ] **Step 7: Rodar os testes e confirmar que passam**

Run: `python -m pytest tests/test_chibi_status.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/contextclaude/chibi_status.py tests/test_chibi_status.py
git commit -m "feat(chibi_status): adiciona write_rodada_inicio/write_rodada_fim"
```

---

## Task 3: Instrumentação de rodada em `pipeline.py`

**Files:**
- Modify: `src/contextclaude/pipeline.py:1-191` (import, `_process_one`, `process_queue`; nova função privada `_pending_bytes`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `db.insert_round`, `db.round_throughput` (Task 1); `chibi_status.write_rodada_inicio`, `chibi_status.write_rodada_fim` (Task 2).
- Produces: `_process_one(conn, row, extract, merge, now) -> dict` com chaves `{"result": "done"|"idle"|"down", "chars": int, "fatos": int, "decisoes": int, "pendencias": int}` (antes devolvia só a string `result` — assinatura pública de `process_queue` não muda).

- [ ] **Step 1: Escrever os testes que falham, em `tests/test_pipeline.py`**

```python
def test_empty_queue_does_not_open_a_round(self):
    self.run_q()
    out = chibi_status.read_status()
    self.assertIsNone(out["rodada"])
    self.assertIsNone(out["ultima_rodada"])

def test_successful_round_closes_done_with_summary_in_status_and_db(self):
    self.transcript("decidimos usar sqlite")
    db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
    self.run_q()
    out = chibi_status.read_status()["ultima_rodada"]
    self.assertEqual((out["status"], out["sessoes"], out["fatos"], out["decisoes"]), ("done", 1, 1, 1))
    row = db.recent_rounds(self.conn, limit=1)[0]
    self.assertEqual(row["chars_total"], os.path.getsize(self.tpath))

def test_down_closes_round_as_error_with_message(self):
    self.transcript("x")
    db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)

    def down(lines):
        raise urllib.error.URLError("fora")

    self.run_q(down)
    out = chibi_status.read_status()["ultima_rodada"]
    self.assertEqual(out["status"], "error")

def test_partial_failure_within_round_closes_as_error_with_real_chars_total(self):
    self.transcript("boa")
    db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
    bad_path = os.path.join(self.tmp.name, "bad.jsonl")
    with open(bad_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "ruim"}}, ensure_ascii=False) + "\n")
    db.enqueue(self.conn, "s2", r"C:\dev\app2", bad_path)
    self.conn.execute("UPDATE sessions SET attempts=? WHERE session_id='s2'", (pipeline.MAX_ATTEMPTS - 1,))
    self.conn.commit()

    def picky(lines):
        if any("ruim" in l for l in lines):
            raise RuntimeError("falha de verdade")
        return dict(DATA)

    self.run_q(picky)
    out = chibi_status.read_status()["ultima_rodada"]
    self.assertEqual(out["status"], "error")
    self.assertEqual(out["sessoes"], 1)  # só s1 concluiu
    row = db.recent_rounds(self.conn, limit=1)[0]
    self.assertEqual(row["chars_total"], os.path.getsize(self.tpath))  # não inclui bad_path

def test_resumed_session_requiring_second_internal_pass_is_still_one_round(self):
    self.transcript("um")
    db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
    self.conn.execute("UPDATE sessions SET status='queued' WHERE session_id='s1'")
    self.conn.commit()
    self.transcript("um", "dois", mode="a")
    db.enqueue(self.conn, "s2", r"C:\dev\app", os.path.join(self.tmp.name, "t2.jsonl"))
    with open(os.path.join(self.tmp.name, "t2.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "tres"}}, ensure_ascii=False) + "\n")
    self.run_q()
    self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM chibi_rounds").fetchone()[0], 1)

def test_unexpected_exception_mid_processing_still_closes_the_round(self):
    self.transcript("decidimos usar sqlite")
    db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
    orig = pipeline.graph.write_entities
    pipeline.graph.write_entities = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        self.run_q()
    finally:
        pipeline.graph.write_entities = orig
    self.assertIsNotNone(chibi_status.read_status()["ultima_rodada"])
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python -m pytest tests/test_pipeline.py -v -k "round or resumed or unexpected"`
Expected: FAIL (`ultima_rodada` sempre `None`, `chibi_rounds` vazia)

- [ ] **Step 3: Adicionar `db` ao import de `pipeline.py` (linha 5: `from . import chibi_status, db, graph, notes, ollama`)**

- [ ] **Step 4: Adicionar `_pending_bytes(row) -> int` em `pipeline.py`, perto de `_pending`**

Bytes pendentes de uma sessão para a estimativa de abertura: `os.path.getsize(path) - offset` se o arquivo existir e `offset <= size`, senão `size` (arquivo reiniciado) ou `0` (sem arquivo).

- [ ] **Step 5: Mudar o retorno de `_process_one` (linha 118) para o dict descrito em Interfaces**

No ramo `down` (dentro do `except Exception as e` que já existe), devolver `{"result": "down", "chars": 0, "fatos": 0, "decisoes": 0, "pendencias": 0}` em vez de `"down"`. No fim da função, calcular `fatos, decisoes, pendencias = (len(data["fatos"]), len(data["decisoes"]), len(data["pendencias"])) if lines else (0, 0, 0)` e `chars = new_off - offset` (o `offset` local, já ajustado pelo reinício de transcrição), e devolver o dict completo em vez de `"done" if lines else "idle"`.

- [ ] **Step 6: Reescrever `process_queue` (linha 168) com abertura/fechamento de rodada**

Estrutura: calcular `primeiro_batch = list(_pending(conn, failed))`; se vazio, `return 0` sem abrir rodada. Senão, `inicio = now()`, `chars_pendentes = sum(_pending_bytes(r) for r in primeiro_batch)`, `throughput = db.round_throughput(conn)`, `estimativa_s = chars_pendentes / throughput if throughput else None`, `chibi_status.write_rodada_inicio(inicio.isoformat(timespec="seconds"), estimativa_s, len(primeiro_batch))`. Dentro de um `try/finally`: o `while` loop existente, mas cada `except Exception as e:` em volta de `_process_one` passa a checar, depois de chamar `_fail`, se a sessão terminou com `status='error'` de verdade (reconsultando a linha: `conn.execute("SELECT status FROM sessions WHERE session_id=?", (row["session_id"],)).fetchone()["status"] == "error"`) — só então acrescenta `str(e)` à lista `falhas`; o ramo `result == "down"` acrescenta `"Ollama fora do ar"` a `falhas` e faz `return done` (o `finally` ainda roda). Acumular `chars_total`, `fatos_total`, `decisoes_total`, `pendencias_total` a partir do dict de cada `_process_one` bem-sucedido. No `finally`: `fim = now()`, `duration_s = (fim - inicio).total_seconds()`, `status_final = "error" if falhas else "done"`, `mensagem = "; ".join(falhas)[:500] if falhas else None`, chamar `chibi_status.write_rodada_fim(status_final, duration_s, done, fatos_total, decisoes_total, mensagem)` e `db.insert_round(conn, inicio.isoformat(timespec="seconds"), fim.isoformat(timespec="seconds"), duration_s, done, chars_total, fatos_total, decisoes_total, pendencias_total, status_final, mensagem)`.

- [ ] **Step 7: Rodar a suíte inteira de `test_pipeline.py` e confirmar que passa (incluindo os testes pré-existentes)**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS (todos, novos e antigos)

- [ ] **Step 8: Commit**

```bash
git add src/contextclaude/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): abre e fecha uma rodada por process_queue com resumo e falha parcial"
```

---

## Task 4: Toast nativo isolado (`chibi_notify.py`)

**Files:**
- Create: `src/contextclaude/chibi_notify.py`
- Modify: `requirements.txt` (acrescentar `pywin32`)
- Test: `tests/test_chibi_notify.py`

**Interfaces:**
- Produces: `chibi_notify.notify(titulo: str, texto: str) -> None` — best-effort, qualquer exceção (incluindo `pywin32` ausente/`ImportError`) é engolida; nunca levanta.

- [ ] **Step 1: Escrever os testes que falham, em `tests/test_chibi_notify.py`**

```python
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, "src")
from contextclaude import chibi_notify


class TestChibiNotify(unittest.TestCase):
    def test_notify_swallows_win32_exception(self):
        with mock.patch.object(chibi_notify, "_show_balloon", side_effect=RuntimeError("sem HWND")):
            chibi_notify.notify("Título", "Texto")  # não deve levantar

    def test_notify_swallows_missing_pywin32(self):
        with mock.patch.object(chibi_notify, "_show_balloon", side_effect=ImportError("no pywin32")):
            chibi_notify.notify("Título", "Texto")  # não deve levantar

    def test_notify_calls_show_balloon_with_title_and_text(self):
        with mock.patch.object(chibi_notify, "_show_balloon") as m:
            chibi_notify.notify("Título", "Texto")
            m.assert_called_once_with("Título", "Texto")
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python -m pytest tests/test_chibi_notify.py -v`
Expected: FAIL (`contextclaude.chibi_notify` não existe)

- [ ] **Step 3: Acrescentar `pywin32` a `requirements.txt`**

- [ ] **Step 4: Implementar `src/contextclaude/chibi_notify.py` com `_show_balloon(titulo: str, texto: str) -> None` e `notify(titulo: str, texto: str) -> None`**

`_show_balloon` importa `win32gui`/`win32con` no corpo da função (não no topo do módulo, para que a ausência de `pywin32` só quebre esta chamada) e chama `win32gui.Shell_NotifyIcon` com a flag `NIIF_INFO`, usando um HWND de uma janela oculta criada/obtida para a chamada (ex.: `win32gui.CreateWindow` com uma classe mínima registrada via `win32gui.RegisterClass`, ou reaproveitando um HWND já existente se for passado — decisão de implementação). **Confira a assinatura real de `Shell_NotifyIcon` na versão de `pywin32` instalada neste ambiente (`pip show pywin32`) antes de montar a tupla `NOTIFYICONDATA`** — não assuma de memória a ordem dos campos. `notify` chama `_show_balloon(titulo, texto)` dentro de `try/except Exception: pass`.

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `python -m pytest tests/test_chibi_notify.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/contextclaude/chibi_notify.py requirements.txt tests/test_chibi_notify.py
git commit -m "feat(chibi): adiciona toast nativo best-effort via pywin32"
```

---

## Task 5: Balão de texto e notificação no widget

**Files:**
- Modify: `src/chibi_widget.py`
- Test: `tests/test_chibi_widget.py`

**Interfaces:**
- Consumes: `status["rodada"]`/`status["ultima_rodada"]` (Task 2); `chibi_notify.notify` (Task 4).
- Produces: `_initial_bubble_state(status: dict) -> dict`; `_bubble_text(bubble_state: dict, status: dict, now=time.monotonic) -> str | None` (muta `bubble_state`); `_initial_notify_state(status: dict) -> dict`; `_maybe_notify(notify_state: dict, status: dict, notify=chibi_notify.notify) -> None` (muta `notify_state`); constante `BUBBLE_SUMMARY_SECONDS = 6`.

- [ ] **Step 1: Escrever os testes que falham, em `tests/test_chibi_widget.py`**

```python
class TestChibiWidgetBubble(unittest.TestCase):
    def test_no_bubble_when_no_round_active_and_no_recent_summary(self):
        status = {"rodada": None, "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status)
        self.assertIsNone(chibi_widget._bubble_text(state, status))

    def test_bubble_shows_elapsed_time_without_estimate(self):
        inicio = datetime.now().isoformat(timespec="seconds")
        status = {"rodada": {"ativa": True, "inicio": inicio, "estimativa_s": None, "sessoes": 2},
                   "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status)
        texto = chibi_widget._bubble_text(state, status)
        self.assertIn("2 sessões", texto)
        self.assertNotIn("~", texto)

    def test_bubble_countdown_never_goes_negative_past_estimate(self):
        inicio = (datetime.now() - timedelta(seconds=100)).isoformat(timespec="seconds")
        status = {"rodada": {"ativa": True, "inicio": inicio, "estimativa_s": 10.0, "sessoes": 1},
                   "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status)
        texto = chibi_widget._bubble_text(state, status)
        self.assertNotIn("-", texto)

    def test_bubble_shows_summary_for_a_few_seconds_after_round_ends(self):
        status_antes = {"rodada": None, "ultima_rodada": None}
        state = chibi_widget._initial_bubble_state(status_antes)
        status_depois = {"rodada": None, "ultima_rodada": {
            "evento_id": 1, "status": "done", "sessoes": 2, "fatos": 3, "decisoes": 1, "mensagem": None}}
        clock = {"t": 100.0}
        texto = chibi_widget._bubble_text(state, status_depois, now=lambda: clock["t"])
        self.assertIn("3 fatos", texto)
        clock["t"] += chibi_widget.BUBBLE_SUMMARY_SECONDS + 1
        self.assertIsNone(chibi_widget._bubble_text(state, status_depois, now=lambda: clock["t"]))

    def test_bubble_shows_error_message_after_failed_round(self):
        state = chibi_widget._initial_bubble_state({"rodada": None, "ultima_rodada": None})
        status = {"rodada": None, "ultima_rodada": {
            "evento_id": 1, "status": "error", "sessoes": 1, "fatos": 0, "decisoes": 0,
            "mensagem": "Ollama fora do ar"}}
        texto = chibi_widget._bubble_text(state, status)
        self.assertIn("Ollama fora do ar", texto)

    def test_summary_seen_at_startup_is_not_replayed(self):
        status = {"rodada": None, "ultima_rodada": {
            "evento_id": 5, "status": "done", "sessoes": 1, "fatos": 1, "decisoes": 0, "mensagem": None}}
        state = chibi_widget._initial_bubble_state(status)
        self.assertIsNone(chibi_widget._bubble_text(state, dict(status)))


class TestChibiWidgetNotify(unittest.TestCase):
    def test_notify_fires_once_on_round_start(self):
        calls = []
        status = {"rodada": {"ativa": True, "inicio": "t", "estimativa_s": None, "sessoes": 1},
                   "ultima_rodada": None}
        state = chibi_widget._initial_notify_state({"rodada": None, "ultima_rodada": None})
        chibi_widget._maybe_notify(state, status, notify=lambda t, m: calls.append((t, m)))
        chibi_widget._maybe_notify(state, status, notify=lambda t, m: calls.append((t, m)))
        self.assertEqual(len(calls), 1)

    def test_notify_fires_on_new_ultima_rodada_with_summary_text(self):
        calls = []
        status = {"rodada": None, "ultima_rodada": {
            "evento_id": 1, "status": "done", "sessoes": 2, "fatos": 4, "decisoes": 1, "mensagem": None}}
        state = chibi_widget._initial_notify_state({"rodada": None, "ultima_rodada": None})
        chibi_widget._maybe_notify(state, status, notify=lambda t, m: calls.append((t, m)))
        self.assertEqual(len(calls), 1)
        self.assertIn("4", calls[0][1])

    def test_notify_does_not_replay_summary_already_seen_at_startup(self):
        calls = []
        status = {"rodada": None, "ultima_rodada": {
            "evento_id": 3, "status": "done", "sessoes": 1, "fatos": 1, "decisoes": 0, "mensagem": None}}
        state = chibi_widget._initial_notify_state(status)
        chibi_widget._maybe_notify(state, dict(status), notify=lambda t, m: calls.append((t, m)))
        self.assertEqual(calls, [])
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python -m pytest tests/test_chibi_widget.py -v -k "Bubble or Notify"`
Expected: FAIL (funções não existem)

- [ ] **Step 3: Implementar `_initial_bubble_state`, `_bubble_text`, `_initial_notify_state`, `_maybe_notify` em `src/chibi_widget.py`**

`_bubble_text`: se `status["ultima_rodada"]` tiver `evento_id` diferente de `bubble_state["last_ultima_rodada_evento_id"]`, atualiza esse campo e marca `bubble_state["mostrar_resumo_ate"] = now() + BUBBLE_SUMMARY_SECONDS`. Se `status["rodada"]` estiver ativa: calcula `elapsed` a partir de `datetime.now() - datetime.fromisoformat(rodada["inicio"])`; com `estimativa_s`, `restante = estimativa_s - elapsed` — se `restante <= 0` mostra texto de "quase lá" (sem número negativo), senão `~MM:SS`; sem `estimativa_s`, mostra só os segundos decorridos. Senão, se `now() < bubble_state["mostrar_resumo_ate"]` (e esse valor não é `None`), devolve o resumo de sucesso (fatos/decisões) ou a `mensagem` de erro de `ultima_rodada`. Caso contrário devolve `None`. `_maybe_notify`: dispara `notify` uma vez quando `rodada.ativa` passa de falso/ausente para verdadeiro (flag em `notify_state`, resetada quando a rodada deixa de estar ativa); dispara `notify` quando `ultima_rodada.evento_id` muda em relação a `notify_state["last_evento_id"]` (seedado pelo `evento_id` já presente em `_initial_notify_state`, mesmo truque de "não repetir pulso já visto" de `_initial_state`).

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `python -m pytest tests/test_chibi_widget.py -v`
Expected: PASS

- [ ] **Step 5: Ligar o balão à janela real em `_run_window`**

Criar `bubble_root = tk.Toplevel(root)` com o mesmo truque `-transparentcolor`/`overrideredirect`/`topmost`, um `tk.Label` de texto, posicionado acima da janela principal (`geometry` relativa a `root.winfo_x()`/`winfo_y()`, recalculada a cada tick junto do arraste). Adicionar `bubble_state = _initial_bubble_state(read_status())` e `notify_state = _initial_notify_state(read_status())` antes do `mainloop`. Adicionar `poll_bubble()`, chamada por `root.after(POLL_STATUS_MS, poll_bubble)` (mesma cadência de `poll_status`): lê `status = read_status()`, chama `_maybe_notify(notify_state, status)`, calcula `texto = _bubble_text(bubble_state, status)` e mostra/esconde `bubble_root` (`deiconify()`/`withdraw()`) com `label.configure(text=texto)` quando não for `None`.

- [ ] **Step 6: Commit**

```bash
git add src/chibi_widget.py tests/test_chibi_widget.py
git commit -m "feat(chibi_widget): balão de texto e notificação nativa por rodada"
```

---

## Task 6: Clique vs. duplo-clique e painel de histórico

**Files:**
- Modify: `src/chibi_widget.py`
- Test: `tests/test_chibi_widget.py`

**Interfaces:**
- Consumes: `db.recent_rounds` (Task 1).
- Produces: `_is_click_not_drag(start_xy: tuple[int, int], end_xy: tuple[int, int], threshold: float = CLICK_MOVE_THRESHOLD_PX) -> bool`; `_format_round_row(row) -> str` (aceita `sqlite3.Row` ou `dict`); constantes `CLICK_MOVE_THRESHOLD_PX = 4`, `CLICK_OPEN_DELAY_MS = 250`.

- [ ] **Step 1: Escrever os testes que falham, em `tests/test_chibi_widget.py`**

```python
class TestChibiWidgetClick(unittest.TestCase):
    def test_small_movement_counts_as_click(self):
        self.assertTrue(chibi_widget._is_click_not_drag((100, 100), (102, 101)))

    def test_large_movement_counts_as_drag(self):
        self.assertFalse(chibi_widget._is_click_not_drag((100, 100), (130, 100)))

    def test_format_round_row_marks_done_and_error(self):
        done = {"finished_at": "2026-10-31T10:00:00", "status": "done", "sessoes": 2,
                "fatos": 3, "decisoes": 1, "mensagem": None}
        erro = {"finished_at": "2026-10-31T10:05:00", "status": "error", "sessoes": 1,
                "fatos": 0, "decisoes": 0, "mensagem": "Ollama fora do ar"}
        self.assertIn("OK", chibi_widget._format_round_row(done))
        linha_erro = chibi_widget._format_round_row(erro)
        self.assertIn("ERRO", linha_erro)
        self.assertIn("Ollama fora do ar", linha_erro)
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `python -m pytest tests/test_chibi_widget.py -v -k Click`
Expected: FAIL (funções não existem)

- [ ] **Step 3: Implementar `_is_click_not_drag` e `_format_round_row` em `src/chibi_widget.py`**

`_is_click_not_drag`: distância euclidiana entre os dois pontos menor que `threshold`. `_format_round_row`: `f"{row['finished_at']} [{'OK' if row['status']=='done' else 'ERRO'}] {row['sessoes']} sessões, {row['fatos']} fatos, {row['decisoes']} decisões"`, acrescentando `" — " + row['mensagem']` quando `status == 'error'` e `mensagem` não for vazio.

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `python -m pytest tests/test_chibi_widget.py -v`
Expected: PASS

- [ ] **Step 5: Ligar o clique/arraste e o painel de histórico em `_run_window`**

Substituir o binding direto `label.bind("<Double-Button-1>", close_widget)` por um `on_double_click` que primeiro cancela um `after` pendente (ver abaixo) e then chama `close_widget`. Adicionar `label.bind("<ButtonRelease-1>", on_release)`, onde `on_release` checa `_is_click_not_drag((drag["x"], drag["y"]), (event.x, event.y))` e, se verdadeiro, agenda `root.after(CLICK_OPEN_DELAY_MS, toggle_history_panel)` guardando o id retornado numa variável de estado (`pending_click["after_id"]`) para `on_double_click` poder cancelá-lo com `root.after_cancel`. Implementar `toggle_history_panel()`: se já houver uma `Toplevel` de histórico aberta, destrói e esquece; senão cria uma `Toplevel` com título, um `tk.Text` somente leitura listando `_format_round_row(r)` para cada `r` em `db.recent_rounds(conn, limit=10)` (abrindo e fechando uma conexão `db.connect()` de vida curta, dentro de `try/except Exception: rows = []` para nunca quebrar o widget se o banco estiver indisponível), e um `protocol("WM_DELETE_WINDOW", ...)` que limpa a referência guardada ao fechar.

- [ ] **Step 6: Commit**

```bash
git add src/chibi_widget.py tests/test_chibi_widget.py
git commit -m "feat(chibi_widget): clique simples abre painel de histórico de rodadas"
```

---

## Self-Review

**Cobertura do spec:** Goals (aviso por rodada em dobro balão+toast — Task 5; ETA que aprende com histórico — Tasks 1+3+5; resumo de sucesso — Tasks 2+3+5; erro com texto — Tasks 2+3+5; clique abre histórico — Task 6; animação/pulsos existentes intactos — nenhuma task toca `_apply_status`/`_advance_frame`) todos cobertos. Non-goals/anti-goals respeitados pela própria estrutura (rodada por `process_queue`, não por sessão; try/finally garante fechamento; poda automática de `chibi_rounds`). Approaches (pywin32, sqlite, Toplevel) implementadas exatamente como decidido.

**Consistência de tipos:** `_process_one` muda de `str` para `dict` — só usado dentro de `process_queue` (confirmado via grep, nenhum outro chamador). `rodada`/`ultima_rodada` usam as mesmas chaves em `chibi_status.py` (produtor), `pipeline.py` (consumidor indireto via `chibi_status`) e `chibi_widget.py` (consumidor direto) — nomes revisados para bater entre as três tasks.

**Review Focus:** as 5 entradas têm teste dedicado (Task 3: fila vazia, falha parcial, rodada única em múltiplas passagens, exceção não prevista; Task 5: contagem regressiva nunca negativa).

---

Plano completo e salvo em `docs/superpowers/plans/2026-10-31-chibi-annotation-assistant.md`. Please review the plan. Which execution approach would you prefer?

- **Subagent-driven** - A fresh subagent implements each task and a fresh reviewer checks it before the next one starts, then a whole-branch review at the end. Most thorough; costs a fresh context per task and per review.
- **Native** - I implement every task myself in this session, the way this harness runs work, then one fresh reviewer on the most capable model checks the whole branch. Cheapest and fastest; no independent review until the end. Runs well with a mid-tier session model, since the plan carries the design.

For this plan I recommend **Native**, because as 6 tasks form a tight, sequential interface chain (db → status → pipeline → notify → widget×2) where each task's implementer needs the previous task's exact code in context anyway, and a shipped mistake here only affects a local desktop widget, not production data — a fresh-subagent-per-task would mostly re-derive context already available cheaply in one continuous session. Does the plan capture what you want, and which approach should we use?
