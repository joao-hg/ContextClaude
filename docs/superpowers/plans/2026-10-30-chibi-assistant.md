# Assistente Chibi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar feedback visual em tempo real do pipeline de anotação do ContextClaude através de um bichinho chibi animado (tkinter + Pillow) que aparece enquanto o Claude Code está aberto.

**Architecture:** Um módulo stdlib-only (`chibi_status.py`) escreve/lê `data/status.json` a cada transição de fase do pipeline (`pipeline.py`, `worker.py`); um processo separado (`chibi_widget.py`, singleton via trava de arquivo, mesmo padrão de `worker.py`/`lock.py`) faz polling desse arquivo e de `tasklist` e desenha a animação correspondente (`chibi_gui.py`, Pillow + tkinter). O hook `session_start` passa a spawnar esse processo além do worker. Pillow/tkinter ficam isolados nos módulos do widget para que um ambiente sem essas libs nunca afete a extração de fatos.

**Tech Stack:** Python stdlib (`json`, `subprocess`, `msvcrt` via `lock.py`, `tkinter`), Pillow (nova dependência, só para os módulos do widget).

**Spec:** `docs/superpowers/specs/2026-10-30-chibi-assistant-design.md`

## Global Constraints

- Windows apenas — o projeto já assume isso (`msvcrt`, `tasklist`).
- Única dependência nova: `pillow`. `tkinter` já é stdlib. Nenhuma outra lib.
- Instrumentação de status deve ser essencialmente grátis: um JSON pequeno por escrita, escrita atômica (arquivo temporário + `os.replace`), nunca lança exceção.
- Processamento do worker continua sequencial, um por vez (`lock.py` já garante isso) — isso não muda.
- Widget é compartilhado: uma única instância para todas as sessões/projetos, nunca uma por sessão.
- Singleton do widget via o mesmo padrão de `worker.py`/`lock.py`: trava de arquivo exclusiva (`data/chibi.lock`), processo sai silenciosamente se já existir outra instância.
- Widget liga no primeiro `SessionStart` que o encontrar desligado; desliga sozinho quando `tasklist` não mostrar mais nenhum processo `claude.exe` (critério confirmado por observação direta nesta máquina — ver spec).
- `chibi_status.py` não pode importar Pillow nem tkinter — é o único módulo que `pipeline.py`/`worker.py` importam para reportar estado. `Pillow`/`tkinter` ficam isolados em `chibi_gui.py`/`chibi_widget.py`.
- `hooks.py` spawna o widget só em `session_start`, nunca em `session_end`.

## Review Focus

- `data/status.json` ausente ou corrompido no primeiro poll do widget deve cair para o estado padrão `idle` em vez de travar ou lançar exceção.
- `tasklist` indisponível, sem a PATH, ou falhando por qualquer motivo deve fazer o widget assumir que ainda há sessão viva (não fechar à força), em vez de crashar.
- Pillow e tkinter ausentes ou quebrados no ambiente não podem impedir `pipeline.py`/`worker.py` de funcionar — `chibi_status.py` precisa ser verificavelmente livre dessas dependências.
- Duas sessões do Claude Code abrindo quase ao mesmo tempo (dois `SessionStart` chamando `_spawn_chibi()` em sequência) não podem resultar em duas janelas do bichinho — a segunda instância precisa sair silenciosamente.
- A cartela de sprites ausente ou corrompida no disco (ex.: usuário apaga `assets/chibi/sprites.png`) não pode derrubar o processo do widget com uma exceção não tratada — só o bichinho deixa de aparecer, silenciosamente.

---

### Task 1: Generalizar `config.py` e `lock.py` para o widget

**Files:**
- Modify: `src/contextclaude/config.py`
- Modify: `src/contextclaude/lock.py`
- Test: `tests/test_pipeline.py` (classe `TestPipeline`, ao lado de `test_worker_lock_blocks_second_instance`)

**Interfaces:**
- Produces: `config.status_path() -> Path` (`root()/"data"/"status.json"`), `config.chibi_lock_path() -> Path` (`root()/"data"/"chibi.lock"`), `lock.acquire(path=None)` (usa `config.lock_path()` quando `path` é `None`, mantendo compatibilidade com chamadas existentes sem argumento).

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_lock_acquire_with_custom_path_is_independent_of_default(self):
    custom = os.path.join(self.tmp.name, "chibi.lock")
    default_handle = lock.acquire()
    try:
        custom_handle = lock.acquire(custom)
        self.assertIsNotNone(custom_handle)
        lock.release(custom_handle)
    finally:
        lock.release(default_handle)
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PYTHONPATH=src python -m unittest tests.test_pipeline.TestPipeline.test_lock_acquire_with_custom_path_is_independent_of_default -v`
Expected: FAIL (`TypeError: acquire() takes 0 positional arguments` ou similar)

- [ ] **Step 3: Implementar**

Em `config.py`, adicionar `status_path()` e `chibi_lock_path()` seguindo exatamente o padrão de `lock_path()`/`db_path()` já existente (`root() / "data" / "<nome>"`).

Em `lock.py`, mudar a assinatura de `acquire()` para `acquire(path=None)`: se `path` for `None`, usa `config.lock_path()`; senão usa `Path(path)` (precisa de `from pathlib import Path` no topo do arquivo, já que um `str` pode ser passado). O resto do corpo (abrir `a+b`, `msvcrt.locking`, `mkdir(parents=True, exist_ok=True)` no `.parent`) não muda.

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `PYTHONPATH=src python -m unittest tests.test_pipeline -v`
Expected: PASS (todos os testes da classe, incluindo os antigos)

- [ ] **Step 5: Commit**

```bash
git add src/contextclaude/config.py src/contextclaude/lock.py tests/test_pipeline.py
git commit -m "feat: generalizar lock.acquire() e caminhos de config para o widget chibi"
```

---

### Task 2: `chibi_status.py` — leitura/escrita de `data/status.json`

**Files:**
- Create: `src/contextclaude/chibi_status.py`
- Test: `tests/test_chibi_status.py`

**Interfaces:**
- Consumes: `config.status_path() -> Path` (Task 1)
- Produces: `chibi_status.ESTADOS: tuple[str, ...]` (`"idle", "pensando", "digitando", "processando", "feliz", "notificacoes"`), `chibi_status.read_status() -> dict` (sempre devolve `{"estado": str, "sessao": str|None, "evento_id": int, "ts": str}`, nunca lança), `chibi_status.write_status(estado: str, sessao: str = None, now=datetime.now) -> None` (nunca lança; ignora `estado` desconhecido; escrita atômica via arquivo temporário + `os.replace`; `evento_id` incrementa a cada chamada bem-sucedida).

- [ ] **Step 1: Escrever os testes que falham**

```python
import json
import os
import tempfile
import unittest

from contextclaude import chibi_status, config


class TestChibiStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_status_default_when_file_missing(self):
        self.assertEqual(chibi_status.read_status(),
                          {"estado": "idle", "sessao": None, "evento_id": 0, "ts": ""})

    def test_write_then_read_roundtrip(self):
        chibi_status.write_status("pensando", sessao="s1")
        out = chibi_status.read_status()
        self.assertEqual((out["estado"], out["sessao"]), ("pensando", "s1"))

    def test_evento_id_increments_on_each_write(self):
        chibi_status.write_status("pensando")
        chibi_status.write_status("digitando")
        self.assertEqual(chibi_status.read_status()["evento_id"], 2)

    def test_write_status_ignores_unknown_estado(self):
        chibi_status.write_status("vmbora")
        self.assertFalse(config.status_path().exists())

    def test_read_status_returns_default_on_corrupted_json(self):
        config.status_path().parent.mkdir(parents=True, exist_ok=True)
        config.status_path().write_text("{nao é json", encoding="utf-8")
        self.assertEqual(chibi_status.read_status()["estado"], "idle")

    def test_module_has_no_pillow_or_tkinter_dependency(self):
        src = (config.root() / ".." / "src" / "contextclaude" / "chibi_status.py")
        import pathlib
        path = pathlib.Path(__file__).resolve().parents[1] / "src" / "contextclaude" / "chibi_status.py"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("PIL", text)
        self.assertNotIn("tkinter", text)
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `PYTHONPATH=src python -m unittest tests.test_chibi_status -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'contextclaude.chibi_status'`)

- [ ] **Step 3: Implementar `chibi_status.py`**

```python
import json
import os
from datetime import datetime

from . import config

ESTADOS = ("idle", "pensando", "digitando", "processando", "feliz", "notificacoes")
_DEFAULT = {"estado": "idle", "sessao": None, "evento_id": 0, "ts": ""}


def read_status() -> dict:
    try:
        with open(config.status_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("estado") in ESTADOS:
            return data
    except Exception:
        pass
    return dict(_DEFAULT)


def write_status(estado: str, sessao: str = None, now=datetime.now) -> None:
    if estado not in ESTADOS:
        return
    try:
        path = config.status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        evento_id = read_status().get("evento_id", 0) + 1
        data = {"estado": estado, "sessao": sessao, "evento_id": evento_id,
                "ts": now().isoformat(timespec="seconds")}
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `PYTHONPATH=src python -m unittest tests.test_chibi_status -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/contextclaude/chibi_status.py tests/test_chibi_status.py
git commit -m "feat: chibi_status.py - leitura/escrita atômica de data/status.json"
```

---

### Task 3: Instrumentar `pipeline.py` e `worker.py`

**Files:**
- Modify: `src/contextclaude/pipeline.py` (`_process_one`, `_update_summary`, `_fail`)
- Modify: `src/worker.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `chibi_status.write_status(estado, sessao=None)` e `chibi_status.read_status()` (Task 2)
- Produces: nenhuma função pública nova — efeito observável só via `chibi_status.read_status()` depois de `process_queue`/`worker.main`.

- [ ] **Step 1: Escrever os testes que falham**

```python
from contextclaude import chibi_status  # adicionar ao import existente

    def test_successful_session_pulses_feliz_status(self):
        self.transcript("decidimos usar sqlite")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.assertEqual(chibi_status.read_status()["estado"], "feliz")

    def test_idle_session_sets_status_idle(self):
        self.transcript()
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        self.run_q()
        self.assertEqual(chibi_status.read_status()["estado"], "idle")

    def test_pipeline_reports_pensando_digitando_feliz_in_order(self):
        seen = []
        orig = pipeline.chibi_status.write_status
        pipeline.chibi_status.write_status = lambda estado, sessao=None, **kw: seen.append(estado)
        try:
            self.transcript("decidimos usar sqlite")
            db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
            self.run_q()
        finally:
            pipeline.chibi_status.write_status = orig
        self.assertIn("pensando", seen)
        self.assertIn("digitando", seen)
        self.assertEqual(seen[-1], "feliz")

    def test_ollama_down_pulses_notificacoes_status(self):
        self.transcript("x")
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        def down(lines):
            raise urllib.error.URLError("fora")
        self.run_q(down)
        self.assertEqual(chibi_status.read_status()["estado"], "notificacoes")

    def test_fail_at_max_attempts_pulses_notificacoes_status(self):
        db.enqueue(self.conn, "s1", r"C:\dev\app", self.tpath)
        for _ in range(pipeline.MAX_ATTEMPTS):
            row = self.conn.execute("SELECT * FROM sessions WHERE session_id='s1'").fetchone()
            pipeline._fail(self.conn, row, RuntimeError("falha"))
        self.assertEqual(self.status(), "error")
        self.assertEqual(chibi_status.read_status()["estado"], "notificacoes")
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `PYTHONPATH=src python -m unittest tests.test_pipeline -v`
Expected: FAIL nos 5 testes novos (estado permanece `idle`/default, pois nada escreve status ainda)

- [ ] **Step 3: Implementar a instrumentação**

Em `pipeline.py`, adicionar `chibi_status` ao import (`from . import chibi_status, graph, notes, ollama`).

Em `_process_one`: escrever `chibi_status.write_status("pensando", sid)` antes do bloco de extração (`data = redact_deep(...)`); no `except` que já existe, antes de `return "down"`, escrever `chibi_status.write_status("notificacoes", sid)`; depois da extração bem-sucedida e antes de `_save(...)`, escrever `chibi_status.write_status("processando", sid)`; depois do `conn.commit()`, se `lines`: escrever `"digitando"` antes de `notes.write_session_note(...)` e, ao final do bloco `if lines:` (depois de `graph.write_entities(...)`), escrever `"feliz"`; no `else` (sem `lines`), escrever `"idle"`.

Em `_update_summary`: escrever `"pensando"` antes de chamar `merge(old, data)`; se `summary` for truthy, escrever `"digitando"` antes de `notes.write_summary(...)`.

Em `_fail`: depois de `conn.commit()`, se `status == "error"`, escrever `chibi_status.write_status("notificacoes", row["session_id"])`.

Em `worker.py`: importar `chibi_status` junto com os outros módulos de `contextclaude`; depois do bloco `try/except` de `vectors.index_pending(conn)` (ainda dentro do `try` externo, antes do `finally`), escrever `chibi_status.write_status("idle")`.

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `PYTHONPATH=src python -m unittest tests.test_pipeline -v`
Expected: PASS (todos, incluindo os 9 testes antigos da classe)

- [ ] **Step 5: Commit**

```bash
git add src/contextclaude/pipeline.py src/worker.py tests/test_pipeline.py
git commit -m "feat: instrumentar pipeline.py/worker.py com data/status.json"
```

---

### Task 4: `chibi_gui.py` — fatiamento da cartela de sprites com Pillow

**Files:**
- Create: `requirements.txt`
- Create: `assets/chibi/sprites.png` (copiar de `C:\Users\João\Downloads\Cartela de sprites da streamer chibi.png`)
- Create: `src/contextclaude/chibi_gui.py`
- Test: `tests/test_chibi_gui.py`

**Interfaces:**
- Produces: `chibi_gui.ESTADO_ROW: dict[str, int]` (`{"idle": 0, "pensando": 1, "digitando": 2, "feliz": 3, "processando": 4, "notificacoes": 5}`), `chibi_gui.FRAMES_PER_STATE = 8`, `chibi_gui.slice_sheet(path, grid_left: int, grid_top: int, cell_w: int, cell_h: int) -> dict[str, list[PIL.Image.Image]]`, `chibi_gui.load_frames() -> dict[str, list[PIL.Image.Image]]` (usa `SHEET_PATH` e as constantes de grade medidas no asset real).

- [ ] **Step 1: Criar `requirements.txt` e copiar o asset**

`requirements.txt`:
```
pillow
```

Criar a pasta `assets/chibi/` e copiar `C:\Users\João\Downloads\Cartela de sprites da streamer chibi.png` para `assets/chibi/sprites.png`.

Instalar a dependência: `python -m pip install -r requirements.txt`.

- [ ] **Step 2: Escrever o teste que falha (fatiamento, com imagem sintética — não depende do asset real)**

```python
import os
import tempfile
import unittest

from PIL import Image

from contextclaude import chibi_gui


class TestChibiGui(unittest.TestCase):
    def test_slice_sheet_returns_six_states_of_eight_frames_each(self):
        sheet = Image.new("RGBA", (8 * 10, 6 * 10))
        for row in range(6):
            for col in range(8):
                color = (row * 40, col * 30, 0, 255)
                for x in range(10):
                    for y in range(10):
                        sheet.putpixel((col * 10 + x, row * 10 + y), color)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sheet.png")
            sheet.save(path)
            frames = chibi_gui.slice_sheet(path, grid_left=0, grid_top=0, cell_w=10, cell_h=10)
        self.assertEqual(set(frames.keys()), set(chibi_gui.ESTADO_ROW.keys()))
        self.assertEqual(len(frames["pensando"]), chibi_gui.FRAMES_PER_STATE)
        self.assertEqual(frames["digitando"][3].getpixel((0, 0)), (2 * 40, 3 * 30, 0, 255))
```

- [ ] **Step 3: Rodar o teste e confirmar que falha**

Run: `PYTHONPATH=src python -m unittest tests.test_chibi_gui -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'contextclaude.chibi_gui'`)

- [ ] **Step 4: Medir a grade real e implementar `chibi_gui.py`**

Abrir `assets/chibi/sprites.png` (1881×836px) e medir, por inspeção visual ou com `PIL.Image.open(...).crop(...)` exploratório, onde a grade de sprites recortável começa (depois da coluna de rótulos de texto) e o tamanho exato de cada célula. Definir esses quatro valores como constantes `GRID_LEFT`, `GRID_TOP`, `CELL_W`, `CELL_H` no topo do arquivo.

```python
from pathlib import Path

from PIL import Image

SHEET_PATH = Path(__file__).resolve().parents[2] / "assets" / "chibi" / "sprites.png"
FRAMES_PER_STATE = 8
ESTADO_ROW = {"idle": 0, "pensando": 1, "digitando": 2, "feliz": 3, "processando": 4, "notificacoes": 5}
GRID_LEFT, GRID_TOP, CELL_W, CELL_H = ...  # medidos no asset real


def slice_sheet(path, grid_left: int, grid_top: int, cell_w: int, cell_h: int) -> dict:
    sheet = Image.open(path).convert("RGBA")
    out = {}
    for estado, row in ESTADO_ROW.items():
        frames = []
        for col in range(FRAMES_PER_STATE):
            x = grid_left + col * cell_w
            y = grid_top + row * cell_h
            frames.append(sheet.crop((x, y, x + cell_w, y + cell_h)))
        out[estado] = frames
    return out


def load_frames() -> dict:
    return slice_sheet(SHEET_PATH, GRID_LEFT, GRID_TOP, CELL_W, CELL_H)
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `PYTHONPATH=src python -m unittest tests.test_chibi_gui -v`
Expected: PASS

- [ ] **Step 6: Verificação manual do fatiamento real**

Rodar `PYTHONPATH=src python -c "from contextclaude import chibi_gui; f = chibi_gui.load_frames(); [im.save(f'/tmp/chibi_{k}.png') for k, fs in f.items() for im in [fs[0]]]"` (ou salvar num diretório do scratchpad) e abrir as imagens geradas para confirmar visualmente que cada uma mostra o personagem inteiro, sem metade de outra célula ou sobra da coluna de rótulo. Ajustar `GRID_LEFT`/`GRID_TOP`/`CELL_W`/`CELL_H` se necessário e repetir até as 6 imagens baterem com os 6 estados esperados.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt assets/chibi/sprites.png src/contextclaude/chibi_gui.py tests/test_chibi_gui.py
git commit -m "feat: chibi_gui.py - fatiamento da cartela de sprites com Pillow"
```

---

### Task 5: `chibi_widget.py` — processo do widget (singleton, ciclo de vida, loop)

**Files:**
- Create: `src/chibi_widget.py`
- Test: `tests/test_chibi_widget.py`

**Interfaces:**
- Consumes: `lock.acquire(path)`/`lock.release(f)` (Task 1), `config.chibi_lock_path()`/`config.status_path()` (Task 1), `chibi_status.read_status()` (Task 2), `chibi_gui.load_frames()`/`chibi_gui.ESTADO_ROW` (Task 4)
- Produces: `chibi_widget.claude_code_running(run=subprocess.run) -> bool`, `chibi_widget.main(acquire=lock.acquire, release=lock.release, load_frames=chibi_gui.load_frames, run=subprocess.run, read_status=chibi_status.read_status) -> None`

- [ ] **Step 1: Escrever os testes que falham**

```python
import os
import subprocess
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import chibi_widget
from contextclaude import config, lock


class TestChibiWidget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTEXTCLAUDE_ROOT"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_claude_code_running_true_when_process_present(self):
        def fake_run(*a, **kw):
            return subprocess.CompletedProcess(a, 0, stdout="claude.exe  1234 Console  1  50 K\n", stderr="")
        self.assertTrue(chibi_widget.claude_code_running(run=fake_run))

    def test_claude_code_running_false_when_absent(self):
        def fake_run(*a, **kw):
            return subprocess.CompletedProcess(a, 0, stdout="python.exe  1  Console  1  10 K\n", stderr="")
        self.assertFalse(chibi_widget.claude_code_running(run=fake_run))

    def test_claude_code_running_defaults_true_on_tasklist_failure(self):
        def fake_run(*a, **kw):
            raise OSError("tasklist nao encontrado")
        self.assertTrue(chibi_widget.claude_code_running(run=fake_run))

    def test_main_returns_silently_when_sprite_sheet_missing(self):
        def boom_load_frames():
            raise FileNotFoundError("sprites.png")
        chibi_widget.main(load_frames=boom_load_frames)
        handle = lock.acquire(config.chibi_lock_path())
        self.assertIsNotNone(handle)
        lock.release(handle)

    def test_main_returns_immediately_if_lock_already_held(self):
        handle = lock.acquire(config.chibi_lock_path())
        try:
            calls = []
            chibi_widget.main(load_frames=lambda: calls.append(1))
            self.assertEqual(calls, [])
        finally:
            lock.release(handle)
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `PYTHONPATH=src python -m unittest tests.test_chibi_widget -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'chibi_widget'`)

- [ ] **Step 3: Implementar `chibi_widget.py`**

```python
import os
import subprocess
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contextclaude import chibi_gui, chibi_status, config, lock
from contextclaude.logutil import log

POLL_STATUS_MS = 400
POLL_TASKLIST_MS = 7000


def claude_code_running(run=subprocess.run) -> bool:
    try:
        out = run(["tasklist"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return True
    return "claude.exe" in out.lower()


def _run_window(frames, run, read_status) -> None:
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    cell_w, cell_h = frames["idle"][0].size
    root.geometry(f"{cell_w}x{cell_h}+{screen_w - cell_w - 20}+{screen_h - cell_h - 60}")
    label = tk.Label(root, bd=0)
    label.pack()
    state = {"estado": "idle", "frame": 0, "evento_id": -1}

    def tick_animation():
        photos = frames.get(state["estado"], frames["idle"])
        from PIL import ImageTk
        state["frame"] = (state["frame"] + 1) % len(photos)
        img = ImageTk.PhotoImage(photos[state["frame"]])
        label.configure(image=img)
        label.image = img
        root.after(POLL_STATUS_MS, tick_animation)

    def poll_status():
        status = read_status()
        if status["estado"] != state["estado"]:
            state["estado"] = status["estado"]
            state["frame"] = 0
        root.after(POLL_STATUS_MS, poll_status)

    def poll_lifecycle():
        if not claude_code_running():
            root.destroy()
            return
        root.after(POLL_TASKLIST_MS, poll_lifecycle)

    tick_animation()
    poll_status()
    root.after(POLL_TASKLIST_MS, poll_lifecycle)
    root.mainloop()


def main(acquire=lock.acquire, release=lock.release, load_frames=chibi_gui.load_frames,
         run=subprocess.run, read_status=chibi_status.read_status) -> None:
    handle = acquire(config.chibi_lock_path())
    if handle is None:
        return
    try:
        try:
            frames = load_frames()
        except Exception as e:
            log(f"chibi: cartela de sprites indisponível, bichinho não vai aparecer: {e!r}")
            return
        _run_window(frames, run, read_status)
    finally:
        release(handle)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `PYTHONPATH=src python -m unittest tests.test_chibi_widget -v`
Expected: PASS

- [ ] **Step 5: Verificação manual (sem infraestrutura de teste de GUI no projeto)**

Rodar `PYTHONPATH=src python src/chibi_widget.py` e confirmar que uma janela aparece no canto inferior direito mostrando a animação `idle`. Em outro terminal, escrever manualmente `{"estado": "pensando", "sessao": null, "evento_id": 99, "ts": "x"}` em `data/status.json` e confirmar que a animação muda em até ~1s. Fechar todas as janelas do Claude Code (ou matar os processos `claude.exe` manualmente) e confirmar que o widget se fecha sozinho em até ~10s.

- [ ] **Step 6: Commit**

```bash
git add src/chibi_widget.py tests/test_chibi_widget.py
git commit -m "feat: chibi_widget.py - processo do widget (singleton, ciclo de vida, loop tkinter)"
```

---

### Task 6: Ligar o widget ao hook `session_start`

**Files:**
- Modify: `src/contextclaude/hooks.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: nenhuma interface nova de outras tasks (só spawna um processo via `subprocess.Popen`, como `_spawn_worker` já faz)
- Produces: `hooks._spawn_chibi() -> None`, `hooks.session_start(payload, spawn=_spawn_worker, spawn_chibi=_spawn_chibi) -> str`

- [ ] **Step 1: Escrever o teste que falha**

```python
    def test_start_spawns_chibi_widget(self):
        chibi_spawned = []
        hooks.session_start(self.payload(), spawn=lambda: self.spawned.append(1),
                             spawn_chibi=lambda: chibi_spawned.append(1))
        self.assertEqual(len(chibi_spawned), 1)
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PYTHONPATH=src python -m unittest tests.test_hooks.TestHooks.test_start_spawns_chibi_widget -v`
Expected: FAIL (`TypeError: session_start() got an unexpected keyword argument 'spawn_chibi'`)

- [ ] **Step 3: Implementar**

Em `hooks.py`, adicionar `_spawn_chibi()` logo depois de `_spawn_worker()`, mesmo padrão (checa `CONTEXTCLAUDE_NO_SPAWN`, monta os mesmos `creationflags`, aponta para `Path(__file__).resolve().parents[1] / "chibi_widget.py"`). Mudar a assinatura de `session_start` para `session_start(payload: dict, spawn=_spawn_worker, spawn_chibi=_spawn_chibi) -> str` e chamar `spawn_chibi()` logo depois de `spawn()`, antes do `return recall`. `session_end` não muda.

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `PYTHONPATH=src python -m unittest tests.test_hooks -v`
Expected: PASS (todos, incluindo os testes antigos — `CONTEXTCLAUDE_NO_SPAWN=1` já está no `setUp`, então `_spawn_chibi()` real não dispara processo nenhum nos testes que não injetam `spawn_chibi`)

- [ ] **Step 5: Commit**

```bash
git add src/contextclaude/hooks.py tests/test_hooks.py
git commit -m "feat: spawnar o widget chibi em session_start"
```

---

## Self-Review

**1. Cobertura do spec:** detecção de estado via `status.json` → Task 2+3; ciclo de vida via `tasklist` → Task 5; singleton via trava de arquivo → Task 1+5; GUI tkinter+Pillow → Task 4+5; widget compartilhado/liga-desliga → Task 5+6; mapeamento de estados → Task 3 (pensando/digitando/processando/feliz/notificacoes) e Task 4 (`ESTADO_ROW`); módulos separados (stdlib vs Pillow/tkinter) → Task 2 vs Task 4/5; `requirements.txt` → Task 4; `hooks.py: _spawn_chibi()` só em `session_start` → Task 6. Nenhuma seção do spec ficou sem task.

**2. Steps:** cada step de teste/implementação tem assinatura e valores exatos; os únicos trechos sem corpo de código completo são a medição manual da grade real de sprites (Task 4, Step 4) e o loop tkinter (Task 5, Step 3) — o primeiro é uma medição física do asset que só o implementador pode fazer olhando a imagem; o segundo é o único algoritmo não determinado inteiramente pela assinatura/testes (mecânica de polling do tkinter), por isso leva corpo completo.

**3. Consistência de tipos:** `chibi_status.write_status(estado, sessao=None, now=...)` usado identicamente em Task 3 e Task 5; `chibi_gui.load_frames()`/`ESTADO_ROW` usados identicamente em Task 4 e Task 5; `lock.acquire(path=None)`/`lock.release(f)` usados identicamente em Task 1, 5 e 6 (`config.chibi_lock_path()`).

**4. Review Focus:** as 5 entradas da seção têm teste na task que é dona do código — `status.json` corrompido (Task 2, `test_read_status_returns_default_on_corrupted_json`), falha do `tasklist` (Task 5, `test_claude_code_running_defaults_true_on_tasklist_failure`), ausência de Pillow/tkinter em `chibi_status.py` (Task 2, `test_module_has_no_pillow_or_tkinter_dependency`), segunda instância do widget (Task 5, `test_main_returns_immediately_if_lock_already_held`), cartela de sprites ausente (Task 5, `test_main_returns_silently_when_sprite_sheet_missing`).

**5. Proporção:** plano tem 6 tasks para um spec de ~150 linhas; blocos de código são majoritariamente assinaturas e testes, com corpo completo só nos dois pontos justificados no item 2.

---

Plan complete and saved to `docs/superpowers/plans/2026-10-30-chibi-assistant.md`. Please review the plan. Which execution approach would you prefer?

- **Subagent-driven** - A fresh subagent implements each task and a fresh reviewer checks it before the next one starts, then a whole-branch review at the end. Most thorough; costs a fresh context per task and per review.
- **Native** - I implement every task myself in this session, the way this harness runs work, then one fresh reviewer on the most capable model checks the whole branch. Cheapest and fastest; no independent review until the end. Runs well with a mid-tier session model, since the plan carries the design.

For this plan I recommend **Native**, porque as 6 tasks são sequenciais e dependem fortemente da interface exata uma da outra (`chibi_status` → `pipeline`/`worker` → `chibi_gui` → `chibi_widget` → `hooks`), e um erro de uma delas é barato de corrigir agora (código novo, sem usuários, sem dados em produção) — o custo de revisão independente por task não parece compensar aqui. Does the plan capture what you want, and which approach should we use?
