# ContextClaude Fase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Grafo de conhecimento leve em SQLite sobre a tabela `relations` existente: consulta de vizinhos via `cc.py grafo`, e páginas de entidade no vault (`vault/<proj>/Entidades/<nome>.md`) com `[[links]]` bidirecionais que o Obsidian renderiza como grafo visual.

**Architecture:** `graph.py` carrega todas as relações do projeto (ou todas) em memória e faz BFS em Python a partir da entidade pedida, normalizando nomes (minúsculas, espaços colapsados) para unificar nós como "SQLite"/"sqlite" sem alterar o schema. `notes.write_entity_note` grava uma página por entidade, sempre regerada (sobrescrita, não acumulada como as notas de sessão). O worker chama isso após `_update_summary`, para cada entidade vista na sessão processada.

**Tech Stack:** Python 3.13 stdlib (sqlite3), sem dependências novas.

**Spec:** `docs/design-fase3.md` (Fases anteriores: `docs/design.md`, `docs/design-fase2.md`)

## Global Constraints
- Sem cache, sem Cognee, sem grafo visual fora do Obsidian, sem reranking/ML.
- Nenhuma mudança de schema: usa a tabela `relations(session_id, project, source, relation, target)` já existente. Normalização é feita em memória na leitura, não gravada no banco.
- `cc.py grafo <entidade> [-p projeto] [-d 2]`: `-d` é profundidade (padrão 2); sem resultados imprime `Nada encontrado.`.
- Vault: `vault/<proj>/Entidades/<nome-normalizado-sanitizado>.md`, mesmo padrão de sanitização de `notes._BAD` usado em `_dir`.
- Testes: `python -m unittest discover -s tests -t .` em `C:\Users\João\ContextClaude`; suíte atual (94 testes, 1 falha ambiental pré-existente por lock de worker real) deve continuar assim — nenhuma regressão nova.
- Consultas moderadas (10-100 relações, profundidade 2) sem timeout: tudo em memória, sem I/O por nível de BFS.

## Review Focus
- Entidade que só aparece como `destino` (nunca como `origem`): `neighbors` deve achá-la também (busca bidirecional), não só sainda.
- Duas variantes de nome na mesma consulta ("SQLite" vs " sqlite ", "SQLITE"): devem cair no mesmo nó, sem duplicar arestas quando o grafo "volta" por caminhos diferentes no BFS.
- `-d` maior que o diâmetro real do grafo não deve lançar nem duplicar arestas (early stop quando a fronteira esvazia).
- Entidade inexistente ou projeto sem relações: `neighbors` devolve `[]`, `cc.py grafo` imprime `Nada encontrado.`, sem traceback.
- Reprocessar a mesma sessão (worker roda duas vezes) não deve duplicar nem corromper a página de entidade — ela é sobrescrita, não acumulada.

---

### Task 1: `graph.py` — normalização e busca de vizinhos

**Files:**
- Create: `src/contextclaude/graph.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Produces:
  - `normalize(name: str) -> str` — minúsculas, `strip()`, espaços internos colapsados em um só.
  - `neighbors(conn, entity: str, projeto: str | None = None, depth: int = 2, limit: int = 100) -> list[dict]` — cada item `{"origem": str, "relacao": str, "destino": str, "depth": int}`, nomes exibidos no texto original (primeira grafia vista), sem duplicar a mesma aresta física mesmo revisitada pelo lado oposto no BFS.
  - `display_name(conn, entity: str, projeto: str | None = None) -> str` — grafia original da primeira vez que a entidade apareceu (por `id` ascendente) em `source` ou `target`; devolve o próprio `entity` se não houver nenhuma ocorrência.

- [ ] **Step 1: testes (falham primeiro).**
  Em `tests/test_graph.py`, usando `db.connect()` com `CONTEXTCLAUDE_ROOT` temporário e inserindo linhas diretamente em `relations` (`session_id, project, source, relation, target`):
  - `test_normalize_lowercases_and_collapses_whitespace`: `normalize("  SQLite   Core ") == "sqlite core"`.
  - `test_neighbors_depth_1_finds_both_directions`: relações `A --usa--> B`, `A --contem--> D` (duas de saída) e `C --depende--> A` (uma de entrada) — espelha o critério de sucesso 1 do design; `neighbors(conn, "A", depth=1)` contém as três, cada uma com `depth=1`, nenhuma repetida.
  - `test_neighbors_depth_2_includes_second_hop`: soma `B --gera--> D`; `neighbors(conn, "A", depth=1)` não inclui a aresta `B-D`; `neighbors(conn, "A", depth=2)` inclui, com `depth=2`.
  - `test_neighbors_stops_without_duplicating_when_graph_cycles`: relações formando um ciclo (`A--r1-->B`, `B--r2-->A`); `neighbors(conn, "A", depth=3)` devolve cada uma das duas arestas exatamente uma vez.
  - `test_neighbors_respects_limit`: entidade com 3 arestas diretas, `limit=1` devolve exatamente 1 item.
  - `test_neighbors_filters_by_project`: mesma entidade em dois projetos diferentes; `projeto="p1"` só traz arestas de `p1`.
  - `test_neighbors_unifies_case_and_whitespace_variants`: insere `SQLite --usa--> B` e, em outra sessão, ` sqlite --roda_em--> C`; `neighbors(conn, "SQLITE", depth=1)` devolve as duas arestas.
  - `test_neighbors_unknown_entity_returns_empty_list`.
  - `test_display_name_prefers_first_seen_original`: insere `SQLite` (id menor) e depois `sqlite` (id maior) em arestas distintas; `display_name(conn, "sqlite") == "SQLite"`.
  - `test_display_name_falls_back_to_input_when_absent`: `display_name(conn, "fantasma") == "fantasma"`.

- [ ] **Step 2:** Rodar `python -m unittest tests.test_graph -v`. Esperado: FAIL (módulo/funções não existem).

- [ ] **Step 3: implementar.** Carregar todas as linhas de `relations` (filtradas por `projeto` se informado) uma vez; construir:
  - `display`: dict `chave_normalizada -> primeira grafia original vista` (ordem de `id` ascendente — usar `ORDER BY id ASC` na query).
  - `phys`: lista de `(origem_normalizada, relacao, destino_normalizada)` por índice.
  - `adj`: dict `chave_normalizada -> [(índice_da_aresta, é_origem: bool), ...]`.

  BFS por níveis a partir de `{normalize(entity)}`, marcando **arestas** visitadas por índice (não por par de nós) para não duplicar quando revisitadas pelo lado oposto; parar quando a fronteira do próximo nível ficar vazia ou `len(out) >= limit`. Cada aresta emitida usa `display[origem]`/`display[destino]` para o texto exibido.

  `display_name` reaproveita o mesmo `display` dict (basta chamar a função interna de carga com `depth` irrelevante, ou expor um `_load(conn, projeto)` privado usado por `neighbors` e `display_name`).

- [ ] **Step 4:** Rodar `python -m unittest tests.test_graph -v`. Esperado: OK. Rodar a suíte inteira para garantir zero regressão.

### Task 2: `notes.write_entity_note`

**Files:**
- Modify: `src/contextclaude/notes.py`
- Test: `tests/test_notes.py`

**Interfaces:**
- Consumes: nada novo (usa `_dir`, `_BAD`, `_rel` já existentes).
- Produces: `write_entity_note(projeto: str, key: str, display: str, relations: list[dict]) -> Path` — grava (sobrescrevendo) `vault/<proj>/Entidades/<_BAD.sub(key)>.md` com frontmatter (`projeto`, `entidade: display`), título `# {display}` e uma seção `## Relações` com uma linha por item de `relations` no mesmo formato de `_rel` (`- [[origem]] —relacao→ [[destino]]`), ou `_nada_` se `relations` for vazia.

  **Minha decisão:** `key` (normalizado) e `display` (grafia original) vêm separados, em vez do `write_entity_note(projeto, name, relations)` esboçado no design — assim `notes.py` não precisa importar `graph.py` para normalizar (evitaria import circular, já que `graph.write_entities`, na Task 3, importa `notes`). Quem chama (`graph.write_entities`) já tem as duas formas calculadas.

- [ ] **Step 1: testes (falham primeiro).**
  - `test_write_entity_note_creates_file_with_wikilinks`: `write_entity_note("app", "sqlite", "SQLite", [{"origem": "SQLite", "relacao": "usa", "destino": "B"}])` cria `vault/app/Entidades/sqlite.md` contendo `# SQLite` e `[[B]]`.
  - `test_write_entity_note_empty_relations_shows_placeholder`: `relations=[]` → arquivo contém `_nada_`.
  - `test_write_entity_note_overwrites_instead_of_accumulating`: chamar duas vezes com conteúdos diferentes; só existe um arquivo em `Entidades/`, com o conteúdo da segunda chamada.
  - `test_write_entity_note_sanitizes_key_for_filesystem`: `key` contendo `/` ou `:` gera nome de arquivo sem esses caracteres (reusa `_BAD`).

- [ ] **Step 2:** Rodar `python -m unittest tests.test_notes -v`. Esperado: FAIL nos 4 novos testes.

- [ ] **Step 3:** Implementar `write_entity_note` em `notes.py`, reaproveitando `_dir(projeto) / "Entidades"` e `_rel`.

- [ ] **Step 4:** Rodar `python -m unittest tests.test_notes -v`. Esperado: OK. Suíte inteira sem regressão.

### Task 3: ligação no worker e comando `cc.py grafo`

**Files:**
- Modify: `src/contextclaude/graph.py` (nova função `write_entities`)
- Modify: `src/contextclaude/pipeline.py:116` (chamar após `_update_summary`)
- Modify: `src/cc.py` (subcomando `grafo`)
- Test: `tests/test_graph.py`, `tests/test_pipeline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `graph.neighbors`, `graph.normalize`, `graph.display_name`, `notes.write_entity_note`.
- Produces:
  - `graph.write_entities(conn, session_id: str, project: str) -> None` — lê as linhas de `relations` dessa `session_id`+`project`, calcula o conjunto de entidades normalizadas vistas (origem e destino), e para cada uma chama `neighbors(conn, entidade, projeto=project, depth=1)` (grafo completo da entidade, não só desta sessão) e `write_entity_note(project, chave, display_name(conn, entidade, projeto=project), vizinhos)`.
  - `cc.main` ganha o subcomando `grafo <entidade> [-p PROJETO] [-d 2]`: abre conexão, chama `graph.neighbors`, imprime uma linha por aresta no formato `f"{origem} --{relacao}--> {destino}"`; sem resultados imprime `Nada encontrado.`; erro imprime `f"Falha no grafo: {e!r}"` e retorna 1. Não usa o lock do worker (é só leitura, como `buscar`).

- [ ] **Step 1: testes (falham primeiro).**
  - Em `tests/test_graph.py`: `test_write_entities_creates_a_page_per_entity_seen_in_session` — popula `relations` com uma linha de uma sessão; chama `write_entities(conn, sid, "app")`; confere que `vault/app/Entidades/<origem>.md` e `.../<destino>.md` existem e cada um contém `[[<a_outra>]]`.
  - Em `tests/test_pipeline.py`: `test_process_one_writes_entity_pages_for_relations_seen` — reusa o `DATA` existente (tem uma relação `A usa B`); depois de `self.run_q()`, `(notes._dir("app") / "Entidades" / "a.md").exists()` e `.../"b.md").exists()` são `True`.
  - Em `tests/test_cli.py`: `test_grafo_prints_edges_or_nada_encontrado` (mock de `graph.neighbors`, mesmo padrão de `run_cli` usado para `buscar`/`indexar`: retorno `[{"origem":"A","relacao":"usa","destino":"B","depth":1}]` → linha `"A --usa--> B"`; retorno `[]` → `"Nada encontrado."`); `test_grafo_passes_projeto_and_depth_options` (confere `kwargs["projeto"]` e `kwargs["depth"]` recebidos por `graph.neighbors`); `test_grafo_error_returns_1_with_message`.

- [ ] **Step 2:** Rodar os três arquivos de teste. Esperado: FAIL nos novos casos.

- [ ] **Step 3:** Implementar `graph.write_entities`; inserir a chamada em `pipeline._process_one` logo após `_update_summary(project, data, merge, sid)`, protegida da mesma forma que erros de resumo não devem derrubar a sessão (decisão: se `write_entities` falhar, não deve impedir a sessão de ficar `done` — a persistência em `facts`/`relations` e o `commit()` já aconteceram antes; seguir o padrão de `_update_summary`, que loga em vez de propagar, mas aqui simplesmente deixar propagar é aceitável *se* a escrita em disco de markdown dificilmente falha — **ruling:** não envolver em try/except extra; é código novo e simples, consistente com `notes.write_session_note` que também não é protegido). Adicionar `grafo` ao `argparse` em `cc.py` seguindo o padrão de `buscar`.

- [ ] **Step 4:** Rodar a suíte inteira. Esperado: OK, sem a falha ambiental de `test_indexar_prints_count` ficar pior (ela já falha hoje por lock externo, fora do controle deste plano).

### Task 4: verificação real e revisão

**Files:** nenhum (só execução e checagem).

- [ ] **Step 1:** Com o worker real pausado (ou aguardando sua vez), rodar `cc.py grafo` contra uma sessão de teste plantada no projeto `teste-fase3`: inserir 3-4 relações formando um pequeno ciclo/ramificação direto no SQLite, chamar `python src/cc.py grafo <entidade>` com `-d 1` e `-d 2`, e confirmar visualmente que a saída bate com os critérios de sucesso 1 e 2 do `docs/design-fase3.md`.
- [ ] **Step 2:** Rodar o worker uma vez sobre essa sessão de teste (ou chamar `pipeline.process_queue` diretamente) e abrir `vault/teste-fase3/Entidades/*.md` no Obsidian (ou ler o markdown) para confirmar os `[[links]]` e o critério de sucesso 4.
- [ ] **Step 3:** Rodar o worker/`process_queue` uma segunda vez sobre a mesma sessão (idempotência) e confirmar que não houve duplicação de arquivos em `Entidades/` (critério implícito do Review Focus).
- [ ] **Step 4:** Apagar os dados de teste (`teste-fase3` no SQLite e no vault) e registrar o resultado.
- [ ] **Step 5:** Revisão final independente (agente `revisor`) do diff da Fase 3; corrigir achados Alto/Crítico com ciclo RED→GREEN antes de considerar a fase concluída.
