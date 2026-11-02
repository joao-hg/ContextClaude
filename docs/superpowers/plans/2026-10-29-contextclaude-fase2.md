# ContextClaude Fase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (execução nativa, escolhida pelo usuário). Steps use checkbox syntax.

**Goal:** Busca semântica (ChromaDB + `qwen3-embedding:0.6b`) sobre fatos/decisões/pendências, via CLI `cc.py buscar` e via hook `UserPromptSubmit` em projetos com `.contextclaude`.

**Architecture:** Chroma é índice derivado do SQLite (`facts.indexed`). O worker indexa pendentes após a fila; falha de embedding só é logada. O hook de prompt embute o prompt, consulta o projeto, filtra por distância e por itens já injetados na sessão, e emite `additionalContext` ≤ 800 caracteres. Qualquer falha = saída vazia, exit 0.

**Tech Stack:** Python 3.13, stdlib + `chromadb`, Ollama `/api/embed`, unittest.

**Spec:** `docs/design-fase2.md` (Fase 1: `docs/design.md`)

## Global Constraints
- Embeddings: `qwen3-embedding:0.6b`, 1024 dims, `keep_alive` "10m". Chroma em `config.root()/data/chroma`, coleção `memoria`, `hnsw:space=cosine`, `anonymized_telemetry=False`.
- Zero tokens do Claude em indexação e busca manual; o hook de prompt injeta ≤ 800 caracteres.
- Hook de prompt: timeout 8 s no settings.json, embed ≤ 4 s, prompts < 15 caracteres ignorados, prompt passa por `redact`, sem marcador `.contextclaude` = silêncio.
- Testes: `python -m unittest discover -s tests -t .` em `C:\Users\João\ContextClaude`; Fase 1 (56 testes) deve continuar verde. Testes nunca chamam Ollama real: `embed` é injetado (`embed_fn`).
- PowerShell 5.1 (sem `&&`); arquivos com `encoding="utf-8"`.

## Review Focus
- Ollama fora/lento durante `index_pending` ou no hook: fatos ficam `indexed=0`, nada quebra, saída vazia.
- Chroma não instalado/corrompido: hook e worker silenciosos; `cc.py buscar` dá mensagem clara.
- Segredo no prompt não chega ao Ollama nem ao log (redact antes do embed).
- Reindexar duas vezes não duplica (ids `fato-<id>`, upsert).
- Mesma sessão não recebe o mesmo fato duas vezes; sessões diferentes sim.

---

### Task 1: Dependência, esquema e `embed`

**Files:**
- Modify: `src/contextclaude/db.py` (SCHEMA + migração em `connect`)
- Modify: `src/contextclaude/config.py` (constantes e `chroma_path()`)
- Create: `src/contextclaude/embed.py`
- Test: `tests/test_embed_schema.py`

**Interfaces:**
- Produces: `config.EMBED_MODEL = "qwen3-embedding:0.6b"`, `config.chroma_path() -> Path`; tabela `injected(session_id TEXT, fact_id INTEGER, PRIMARY KEY(session_id, fact_id))`; coluna `facts.indexed INTEGER NOT NULL DEFAULT 0` (adicionada por migração em bancos antigos); `embed.embed(texts: list[str], timeout: float = 60, post=ollama._http_post) -> list[list[float]]`.

- [ ] **Step 1:** `pip install chromadb` (PowerShell: `python -m pip install chromadb`); conferir `python -c "import chromadb"`.
- [ ] **Step 2: testes (falham primeiro).** `test_connect_adds_indexed_column_to_old_db` (cria banco com `facts` sem `indexed`, `db.connect()` → `PRAGMA table_info(facts)` contém `indexed`); `test_injected_table_exists`; `test_embed_posts_model_and_returns_vectors` (post falso recebe path `/api/embed`, body com `model=config.EMBED_MODEL`, `input=texts`, `keep_alive="10m"`; devolve `{"embeddings": [[1.0],[2.0]]}` → resultado igual); `test_embed_empty_list_does_not_post`.
- [ ] **Step 3:** Rodar: `python -m unittest tests.test_embed_schema -v`. Expected: FAIL.
- [ ] **Step 4:** Implementar: `ALTER TABLE facts ADD COLUMN indexed INTEGER NOT NULL DEFAULT 0` em `connect` quando a coluna faltar (via `PRAGMA table_info`), mais `CREATE TABLE IF NOT EXISTS injected` no SCHEMA; `embed.py` chamando `ollama._http_post("/api/embed", {...}, timeout)`.
- [ ] **Step 5:** Rodar a suíte inteira. Expected: tudo OK.

### Task 2: `vectors.py` (índice e busca)

**Files:**
- Create: `src/contextclaude/vectors.py`
- Test: `tests/test_vectors.py`

**Interfaces:**
- Consumes: `embed.embed`, `config.chroma_path()`, tabela `facts`.
- Produces:
  - `index_pending(conn, embed_fn=embed.embed, limit=500) -> int` (lotes de 32; marca `indexed=1` só após upsert bem-sucedido; exceção de embed interrompe e deixa o restante pendente; devolve nº indexado);
  - `rebuild(conn, embed_fn=embed.embed) -> int` (apaga a coleção, zera `indexed`, chama `index_pending` sem limite);
  - `search(query, projeto=None, n=5, embed_fn=embed.embed, timeout=60) -> list[dict]` com chaves `id` (int do fato), `text`, `projeto`, `tipo`, `data`, `distance`, ordenadas por distância; `[]` se a coleção não existe/está vazia.
  - Metadados Chroma: `projeto`, `tipo` (kind), `sessao`, `data` (created_at). Documento = `text`.
- Testes usam `embed_fn` falso determinístico (ex.: vetor por hash de palavras, 8 dims) e `CONTEXTCLAUDE_ROOT` temporário; fechar o client/conn no tearDown.

- [ ] **Step 1: testes (falham primeiro):** `test_index_pending_marks_indexed`; `test_index_twice_no_duplicates`; `test_embed_failure_leaves_pending` (embed_fn levanta → `indexed=0` e sem exceção propagada de `index_pending`? **ruling:** `index_pending` propaga; o worker captura); `test_search_returns_closest_first_and_filters_project`; `test_search_empty_when_no_collection`; `test_rebuild_restores_after_deleting_chroma_dir`.
- [ ] **Step 2:** Rodar `python -m unittest tests.test_vectors -v`. Expected: FAIL.
- [ ] **Step 3:** Implementar com `chromadb.PersistentClient(path=str(config.chroma_path()), settings=Settings(anonymized_telemetry=False))`, `get_or_create_collection("memoria", metadata={"hnsw:space": "cosine"})`, `upsert`.
- [ ] **Step 4:** Rodar a suíte inteira. Expected: OK.

### Task 3: Worker e CLI `cc.py`

**Files:**
- Modify: `src/worker.py` (após `process_queue`, `try: vectors.index_pending(conn) except Exception as e: log(...)`)
- Create: `src/cc.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `vectors.search`, `vectors.rebuild`.
- Produces: `cc.main(argv: list[str], out=print) -> int` — `buscar <consulta> [-p PROJETO] [-n 5]` imprime linhas `[<projeto>/<tipo> <data>] <texto> (dist 0.xx)`; sem resultados imprime `Nada encontrado.`; erro de Chroma/Ollama imprime mensagem em português e retorna 1; `indexar` chama `rebuild` e imprime a contagem. `cc.py` em modo script força `sys.stdout.reconfigure(encoding="utf-8")`.

- [ ] **Step 1: testes (falham primeiro):** `main(["buscar","x"], out=...)` com `vectors.search` mockado: formato da linha, `-p` e `-n` repassados, "Nada encontrado.", erro → retorno 1 e mensagem; `indexar` mockado imprime contagem.
- [ ] **Step 2:** Rodar `python -m unittest tests.test_cli -v`. Expected: FAIL.
- [ ] **Step 3:** Implementar `cc.py` (argparse, `sys.path` como em `hook.py`) e a linha do worker. Teste de worker: `test_worker_index_failure_is_logged_not_raised` mockando `vectors.index_pending`.
- [ ] **Step 4:** Suíte inteira. Expected: OK.

### Task 4: Hook `UserPromptSubmit`

**Files:**
- Modify: `src/contextclaude/hooks.py`, `src/hook.py` (modo `prompt`)
- Modify: `src/contextclaude/config.py` (`PROMPT_MIN_CHARS=15`, `PROMPT_MAX_CHARS=800`, `PROMPT_N=4`, `PROMPT_MAX_DISTANCE=0.6` inicial, `PROMPT_EMBED_TIMEOUT=4`)
- Test: `tests/test_prompt_hook.py`

**Interfaces:**
- Consumes: `vectors.search(..., timeout=config.PROMPT_EMBED_TIMEOUT)`, tabela `injected`, `redact.redact`, `project_name`.
- Produces: `hooks.prompt_submit(payload: dict, search=vectors.search) -> str` (payload: `session_id`, `cwd`, `prompt`). Retorna `""` quando: sem marcador, prompt < 15 chars, busca levanta, nenhum item com `distance <= PROMPT_MAX_DISTANCE`, ou todos já injetados. Caso contrário: cabeçalho `Memória relevante (ContextClaude):\n` + linhas `- [tipo data] texto`, cortado em 800 caracteres, e registra os `fact_id`s em `injected`. `hook.py prompt` escreve `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":...}}` só se não vazio.

- [ ] **Step 1: testes (falham primeiro):** sem marcador → `""` e `search` não chamado; prompt curto → `""`; segredo no prompt chega ao `search` como `[REDACTED]`; filtro de distância; teto de 800; segunda chamada na mesma sessão não repete ids, outra sessão repete; `search` levantando → `""`; `hook.py prompt` via subprocess com `CONTEXTCLAUDE_ROOT` temporário sem marcador → stdout vazio, exit 0.
- [ ] **Step 2:** Rodar `python -m unittest tests.test_prompt_hook -v`. Expected: FAIL.
- [ ] **Step 3:** Implementar `prompt_submit` (conexão com `HOOK_DB_TIMEOUT`, sem spawn) e ramo `prompt` no `hook.py` (mesmo padrão try/except BaseException).
- [ ] **Step 4:** Suíte inteira. Expected: OK.

### Task 5: Instalação e verificação real

**Files:**
- Modify: `C:\Users\João\.claude\settings.json` (hook `UserPromptSubmit`: `python "C:\Users\João\ContextClaude\src\hook.py" prompt`, timeout 8; backup antes em `settings.json.bak-fase2`; editar via `python -I`)
- Modify: `docs/REVERT.md` (como remover o hook de prompt e o Chroma)

- [ ] **Step 1:** Backup do settings e inserir o hook preservando os existentes; validar JSON com `python -I`.
- [ ] **Step 2:** Verificação real com Ollama: usar uma sessão de teste curta (fatos plantados via SQLite em projeto `teste-fase2`, marcador criado), `cc.py indexar`, `cc.py buscar` com consulta sem palavras em comum (critério 1), medir distâncias relacionadas × não relacionadas e ajustar `PROMPT_MAX_DISTANCE`; medir tempo do hook com modelo frio e quente.
- [ ] **Step 3:** Critérios 2–4 do spec: hook com Ollama parado = vazio; apagar `data/chroma` + `cc.py indexar` restaura; suíte verde.
- [ ] **Step 4:** Apagar dados de teste (`teste-fase2` no SQLite, vault e Chroma) e registrar o resultado.
- [ ] **Step 5:** Revisão final independente (`revisor`) do diff da Fase 2; corrigir Alto/Crítico com teste RED→GREEN.
