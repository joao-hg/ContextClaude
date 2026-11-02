# ContextClaude Fase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hooks do Claude Code enfileiram sessões; um worker destacado usa o Qwen3 4B (Ollama) para extrair fatos/decisões/relações e gravar em SQLite e num vault Obsidian por projeto, com recall opcional por marcador.

**Architecture:** Dois entry points finos (`hook.py start|end`) chamam o pacote `contextclaude`. `end` e `start` só enfileiram no SQLite e disparam `worker.py` destacado. O worker lê a transcrição JSONL, filtra segredos, pede extração JSON ao Ollama e grava notas Markdown + linhas SQLite.

**Tech Stack:** Python 3.13 (somente biblioteca padrão: `sqlite3`, `urllib`, `unittest`), Ollama `qwen3:4b` em `http://localhost:11434`.

**Spec:** `C:\Users\João\ContextClaude\docs\design.md`

**Nota:** `C:\Users\João\ContextClaude` não é repositório git; não há passos de commit. Os testes rodam com `python -m unittest` a partir da raiz do projeto.

## Global Constraints

- Raiz: `C:\Users\João\ContextClaude`, com subpastas `vault`, `src`, `data`, `docs`.
- Fase 1 usa só a biblioteca padrão do Python 3.13.
- Todo hook termina com exit 0 e saída vazia, exceto o recall do `SessionStart`.
- Nenhuma chamada ao `claude` ou à API Anthropic.
- Segredos filtrados por regex antes de ir ao modelo.
- `keep_alive` do Ollama de ~2 min; o Ollama não é encerrado ao fechar o Claude Code.
- Diretório inicial `C:\Users\João` vira o projeto `_geral`; nomes de projeto iguais com caminhos diferentes recebem sufixo curto de hash.
- Recall só com arquivo `.contextclaude` na pasta do projeto; teto de ~1500 caracteres.
- Backup do `~/.claude/settings.json` antes de editar; reversão documentada em `docs/REVERT.md`.
- Caminhos contêm acento (`João`): sempre abrir arquivos com `encoding="utf-8"` e ler o stdin dos hooks como bytes UTF-8.

## Review Focus

- Caminho com acento (`C:\Users\João\...`) em cwd, transcript_path e comando do hook: tudo deve funcionar.
- `cwd` igual ao diretório inicial → projeto `_geral`; dois caminhos com o mesmo nome → pastas distintas no vault.
- Ollama devolve JSON inválido ou campos faltando, ou está fora do ar: item volta para a fila sem travar nem perder dados.
- Marcador `.contextclaude` existe mas `_resumo.md` ainda não: saída vazia, sem erro.
- Dois `SessionEnd` quase simultâneos: um único worker processa a fila (trava), sem duplicar notas.
- Stdin vazio/inválido nos hooks e transcrição com linhas gigantes ou blocos não textuais (tool_use, imagens): exit 0, sem exceção.

---

### Task 1: Config, banco e nome de projeto

**Files:**
- Create: `src/contextclaude/__init__.py`, `src/contextclaude/config.py`, `src/contextclaude/db.py`, `src/contextclaude/project.py`
- Test: `tests/test_db_project.py`

**Interfaces:**
- Produces:
  - `config.ROOT: Path`, `config.VAULT: Path`, `config.DB_PATH: Path`, `config.LOG_PATH: Path`, `config.OLLAMA_URL = "http://localhost:11434"`, `config.MODEL = "qwen3:4b"`, `config.RECALL_MAX_CHARS = 1500`. `ROOT` vem de `os.environ.get("CONTEXTCLAUDE_ROOT")` ou do pai de `src`, para permitir testes em pasta temporária.
  - `db.connect() -> sqlite3.Connection` (cria `data/` e o esquema se faltar; `row_factory = sqlite3.Row`).
  - Tabelas: `sessions(session_id PK, project, cwd, transcript_path, status TEXT, offset INTEGER, created_at, processed_at)` com status `queued|done|error`; `facts(id, session_id, project, kind, text, created_at)` com kind `fato|decisao|pendencia`; `relations(id, session_id, project, source, relation, target)`.
  - `db.enqueue(conn, session_id, cwd, transcript_path) -> None` (idempotente: não rebaixa `done`).
  - `project.project_name(cwd: str) -> str`.

- [ ] **Step 1: Escrever testes `test_project_name_*` e `test_enqueue_idempotent`**
  - `project_name(r"C:\Users\João")` == `"_geral"`; `project_name(r"C:\dev\app")` == `"app"`.
  - Dois cwds com basename `app` em caminhos diferentes → nomes distintos, o segundo com sufixo `-` + 6 hex de hash (a primeira ocorrência registrada fica sem sufixo; o registro de caminho→nome persiste numa tabela `projects(cwd PK, name UNIQUE)` consultada por `project_name(conn, cwd)`; ajuste a assinatura para `project_name(conn, cwd)`).
  - `enqueue` duas vezes gera 1 linha; após `status='done'`, novo `enqueue` não volta para `queued`.
- [ ] **Step 2: Rodar `python -m unittest tests.test_db_project -v`; esperado: falha por módulo ausente**
- [ ] **Step 3: Implementar os quatro módulos conforme Interfaces** (comparar caminhos com `os.path.normcase(os.path.normpath(...))`).
- [ ] **Step 4: Rodar o mesmo comando; esperado: PASS**

### Task 2: Filtro de segredos

**Files:**
- Create: `src/contextclaude/redact.py`
- Test: `tests/test_redact.py`

**Interfaces:**
- Produces: `redact.redact(text: str) -> str` — substitui por `[REDACTED]`.

- [ ] **Step 1: Testes** — `redact("key sk-ant-api03-AAAAAAAAAAAAAAAAAAAA")`, `"ghp_" + "a"*36`, `"AKIAABCDEFGHIJKLMNOP"`, `"Authorization: Bearer abc.def.ghi"`, `"password=hunter2"`, e `"-----BEGIN PRIVATE KEY-----\nXXXX\n-----END PRIVATE KEY-----"` não contêm mais o segredo e contêm `[REDACTED]`; texto comum (`"decidimos usar sqlite"`) volta inalterado.
- [ ] **Step 2: Rodar `python -m unittest tests.test_redact -v`; esperado: FAIL**
- [ ] **Step 3: Implementar `redact` com uma lista de regex compiladas** (chaves Anthropic/OpenAI `sk-...`, GitHub `gh[pousr]_`, AWS `AKIA`, header Bearer, pares `password|passwd|secret|token|api_key = valor`, blocos PEM).
- [ ] **Step 4: Rodar; esperado: PASS**

### Task 3: Leitor de transcrição

**Files:**
- Create: `src/contextclaude/transcript.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces: `transcript.read_new(path: str, offset: int) -> tuple[list[str], int]` — devolve linhas de conversa no formato `"Usuário: ..."` / `"Claude: ..."` a partir do byte `offset` e o novo offset. Ignora linhas que não são JSON, mensagens sem texto e blocos que não são `text` (tool_use, tool_result, imagens); trunca cada mensagem em 4000 caracteres.

- [ ] **Step 1: Confirmar o formato real**: abrir 1 arquivo `~/.claude/projects/*/*.jsonl` com `Grep`/`Read` limitado e anotar os campos (`type`, `message.role`, `message.content`) no topo de `transcript.py` como comentário. Os testes usam um JSONL fabricado nesse mesmo formato.
- [ ] **Step 2: Testes** — `test_extracts_user_and_assistant_text`; `test_skips_tool_blocks_and_garbage_lines` (linha `not json`, bloco `tool_use`); `test_offset_resumes` (segunda leitura com o offset devolvido retorna só linhas novas); `test_truncates_long_message`; `test_missing_file_returns_empty`.
- [ ] **Step 3: Rodar `python -m unittest tests.test_transcript -v`; esperado: FAIL**
- [ ] **Step 4: Implementar `read_new`**, lendo em binário a partir de `offset`, decodificando UTF-8 com `errors="replace"` e só avançando o offset até a última quebra de linha completa.
- [ ] **Step 5: Rodar; esperado: PASS**

### Task 4: Cliente Ollama

**Files:**
- Create: `src/contextclaude/ollama.py`
- Test: `tests/test_ollama.py`

**Interfaces:**
- Consumes: `config.OLLAMA_URL`, `config.MODEL`.
- Produces:
  - `ollama.is_up(timeout: float = 0.5) -> bool`.
  - `ollama.ensure_running() -> bool` — se `is_up()` falso, inicia `ollama serve` destacado (`subprocess.Popen` com `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, saída descartada), espera até ~15 s; devolve se subiu.
  - `ollama.extract(lines: list[str], post=_http_post) -> dict` — devolve `{"fatos": [str], "decisoes": [str], "pendencias": [str], "relacoes": [{"origem": str, "relacao": str, "destino": str}], "resumo": str}`; levanta `ExtractionError` se o JSON for inválido ou sem as chaves; `post` é injetável para teste.
  - `ollama.merge_summary(old: str, new_items: dict, post=_http_post) -> str` — gera o `_resumo.md` consolidado (máx. ~1500 caracteres).

- [ ] **Step 1: Testes** com `post` falso — `test_extract_parses_valid_json`; `test_extract_invalid_json_raises`; `test_extract_missing_keys_raises`; `test_request_disables_thinking` (corpo contém `"think": false`, `"format": "json"`, `"keep_alive": "2m"`, `"stream": false`, modelo `qwen3:4b`).
- [ ] **Step 2: Rodar `python -m unittest tests.test_ollama -v`; esperado: FAIL**
- [ ] **Step 3: Implementar** com `urllib.request` para `POST /api/chat`; prompt em português instruindo a extrair só o que é durável (decisões, fatos do projeto, pendências, relações entre entidades) e a responder somente JSON com as chaves acima.
- [ ] **Step 4: Rodar; esperado: PASS**
- [ ] **Step 5: Teste manual, uma vez**: `python -c "from contextclaude import ollama; print(ollama.extract(['Usuário: vamos usar sqlite para a fila', 'Claude: ok']))"` com `PYTHONPATH=src`; esperado: dict com `decisoes` mencionando sqlite. Se o Qwen3 devolver JSON inválido com frequência, ajustar o prompt aqui.

### Task 5: Escritor de notas

**Files:**
- Create: `src/contextclaude/notes.py`
- Test: `tests/test_notes.py`

**Interfaces:**
- Consumes: `config.VAULT`.
- Produces:
  - `notes.write_session_note(project: str, session_id: str, when: datetime, data: dict) -> Path` — grava `vault/<project>/Sessões/AAAA-MM-DD-hhmm.md` com seções Resumo, Decisões, Fatos, Pendências, Relações; entidades das relações viram `[[wikilinks]]`; cria pastas; nome de arquivo repetido recebe sufixo `-2`.
  - `notes.read_summary(project: str) -> str | None`; `notes.write_summary(project: str, text: str) -> Path` (`vault/<project>/_resumo.md`).

- [ ] **Step 1: Testes** (com `CONTEXTCLAUDE_ROOT` temporário) — `test_session_note_has_wikilinks_and_sections`; `test_collision_gets_suffix`; `test_summary_roundtrip`; `test_read_summary_missing_returns_none`; `test_project_name_with_accent`.
- [ ] **Step 2: Rodar `python -m unittest tests.test_notes -v`; esperado: FAIL**
- [ ] **Step 3: Implementar** (sanitizar o nome do projeto para caracteres inválidos no Windows; frontmatter YAML com `projeto`, `sessao`, `data`).
- [ ] **Step 4: Rodar; esperado: PASS**

### Task 6: Worker

**Files:**
- Create: `src/worker.py`, `src/contextclaude/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `db.*`, `transcript.read_new`, `redact.redact`, `ollama.extract`, `ollama.merge_summary`, `notes.*`, `project.project_name`.
- Produces:
  - `pipeline.process_queue(conn, extract=ollama.extract, merge=ollama.merge_summary, now=datetime.now) -> int` — processa todas as sessões `queued` (retorna quantas concluiu). Por sessão: lê a partir de `offset`, aplica `redact`, divide em blocos de ~6000 caracteres, extrai por bloco, agrega, grava nota + linhas em `facts`/`relations`, atualiza o resumo, marca `done` e salva o novo `offset`. Sessão sem texto novo vira `done` sem nota. Falha de extração mantém `queued` (conta tentativas; após 5 vira `error`).
  - `src/worker.py`: entry point; adquire trava de arquivo exclusiva em `data/worker.lock` (se ocupada, sai em silêncio), chama `ollama.ensure_running()`, depois `process_queue`, registra erros em `config.LOG_PATH`; nunca imprime em stdout/stderr.

- [ ] **Step 1: Testes** com `extract`/`merge` falsos e JSONL temporário — `test_session_processed_creates_note_and_rows`; `test_extraction_error_keeps_queued_then_errors_after_5`; `test_empty_transcript_marks_done_without_note`; `test_secret_never_reaches_extract` (o falso captura as linhas e confirma `[REDACTED]`); `test_second_run_does_not_duplicate`; `test_worker_lock_blocks_second_instance`.
- [ ] **Step 2: Rodar `python -m unittest tests.test_pipeline -v`; esperado: FAIL**
- [ ] **Step 3: Implementar `pipeline.py` e `worker.py`** (trava via `msvcrt.locking`; coluna extra `attempts INTEGER` em `sessions`, adicionar ao esquema da Task 1 se ainda não estiver).
- [ ] **Step 4: Rodar; esperado: PASS**

### Task 7: Hooks e recall

**Files:**
- Create: `src/hook.py`, `src/contextclaude/hooks.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `db.enqueue`, `ollama.ensure_running`, `notes.read_summary`, `project.project_name`, `config.RECALL_MAX_CHARS`.
- Produces:
  - `hooks.session_start(payload: dict, spawn=_spawn_worker) -> str` — retorna o texto do recall ou `""`. Fluxo: registra a sessão atual como `queued` com `db.enqueue` (cobre fechamento abrupto: se o `SessionEnd` nunca vier, a próxima execução do worker a processa); chama `spawn()` para o worker reprocessar tudo que estiver `queued` e subir o Ollama sem bloquear o hook; se `cwd/.contextclaude` existir e houver `_resumo.md`, retorna o resumo cortado em `RECALL_MAX_CHARS`.
  - `hooks.session_end(payload: dict, spawn=_spawn_worker) -> None` — `db.enqueue(...)` e `spawn()`.
  - `_spawn_worker()` — `subprocess.Popen([sys.executable, worker.py], ...)` destacado, sem janela, sem herdar stdio.
  - `src/hook.py start|end`: lê o stdin como bytes UTF-8, decodifica o JSON (campos `session_id`, `transcript_path`, `cwd`), embrulha tudo em `try/except Exception` (log em `LOG_PATH`), escreve no stdout apenas o JSON `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <recall>}}` quando o recall não é vazio, e sempre sai com 0.

- [ ] **Step 1: Testes** — `test_start_without_marker_returns_empty`; `test_start_with_marker_returns_summary_truncated`; `test_start_marker_without_summary_returns_empty`; `test_end_enqueues_and_spawns` (spawn falso chamado uma vez); `test_hook_cli_bad_stdin_exits_zero_silent` (subprocess real com stdin vazio e lixo: exit 0, stdout e stderr vazios); `test_hook_cli_accented_cwd`.
- [ ] **Step 2: Rodar `python -m unittest tests.test_hooks -v`; esperado: FAIL**
- [ ] **Step 3: Implementar** conforme Interfaces. Como `start` registra a sessão como `queued` e o worker usa `offset`, o worker pode rodar com a sessão ainda aberta sem duplicar: só lê o que for novo. Adicionar teste `test_start_enqueues_current_session`.
- [ ] **Step 4: Rodar; esperado: PASS**
- [ ] **Step 5: Medir**: `Measure-Command { '{}' | python src\hook.py start }` deve ficar em ~1 s ou menos; se passar, mover trabalho para o worker.

### Task 8: Instalação e verificação ponta a ponta

**Files:**
- Modify: `C:\Users\João\.claude\settings.json` (bloco `hooks`; backup antes)
- Create: `docs/REVERT.md`

- [ ] **Step 1: Backup**: copiar `settings.json` para `settings.json.bak-contextclaude` (usar `python -I` para ler o JSON; ver `~/.claude/OTIMIZACAO.md` por hooks já existentes e preservá-los).
- [ ] **Step 2: Adicionar hooks** `SessionStart` e `SessionEnd`, ambos com `command` = `python "C:\Users\João\ContextClaude\src\hook.py" start|end`, `timeout` curto (5 s). Validar o JSON resultante com `python -I -c "import json; json.load(open(r'...settings.json', encoding='utf-8'))"`.
- [ ] **Step 3: Escrever `docs/REVERT.md`**: como restaurar o backup e como remover os dois hooks.
- [ ] **Step 4: Verificação real**: abrir uma sessão do Claude Code numa pasta de teste (`C:\Users\João\ContextClaude\teste-projeto`), conversar 2–3 turnos sobre uma decisão concreta, encerrar. Conferir: (a) `vault\teste-projeto\Sessões\*.md` existe com a decisão; (b) `facts` tem linhas (`python -I` + `sqlite3`); (c) nenhum texto extra apareceu no contexto da sessão; (d) o uso de tokens da sessão não inclui nada do worker (`python -I ~/.claude/scripts/cc-usage.py`).
- [ ] **Step 5: Recall**: criar `teste-projeto\.contextclaude`, abrir nova sessão e perguntar o que foi decidido; esperado: o Claude responde a partir do resumo. Remover o marcador e confirmar que a próxima sessão não recebe nada.
- [ ] **Step 6: Robustez**: encerrar o processo do Ollama no meio de um processamento e confirmar que a sessão continua `queued` e é concluída na próxima execução.

---

## Self-Review

- **Cobertura da spec:** hooks (T7, T8), worker/fila/redact (T2, T6), Ollama (T4), SQLite (T1), vault e resumo (T5), projeto/`_geral`/hash (T1), recall com marcador e teto (T7), backup/reversão (T8), critérios de sucesso 1–4 (T8 passos 4–6). `keep_alive` e não encerrar o Ollama em T4.
- **Ajustes de consistência:** `project_name` ganhou `conn` (T1) e todos os consumidores usam essa assinatura; `attempts` é coluna de `sessions` (T6 → esquema de T1).
- **Risco conhecido:** o formato exato do JSONL é confirmado em T3 passo 1; a qualidade do Qwen3 4B é validada em T4 passo 5 antes de construir em cima.
