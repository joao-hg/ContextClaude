# ContextClaude — Fase 3 (grafo de conhecimento leve)

Data: 2026-10-30 · Status: awaiting approval · Pré-requisito: Fase 1 (`docs/design.md`) + Fase 2 (`docs/design-fase2.md`)

## Intenção
Conectar entidades (ferramentas, arquivos, conceitos, decisões) que o worker extrai, num grafo relacional leve em SQLite. Permitir navegação de dependências e contexto, sem tokens do Claude e sem dependências pesadas (sem Cognee).

## Objetivos
- Indexar relações (origem, relação, destino) conforme extraídas pelo worker na tabela `relations` existente.
- `cc.py grafo <entidade> [-p projeto] [-d 2]`: consulta recursiva em SQLite que lista vizinhos até profundidade N.
- Páginas de entidade no vault: `vault/<proj>/Entidades/<nome>.md`, com `[[links]]` bidirecionais. Obsidian renderiza o grafo visualmente.
- Normalização de nomes: "SQLite", "sqlite", "sql ite" → nó único `sqlite` no banco (chave menor, espaços e case-insensitive).

## Não objetivos
- Grafo visual fora do Obsidian, machine learning, reranking, Cognee.
- Injetar o grafo no recall de prompt (Fase 2 já injeta por similaridade; grafo é para navegação manual).

## Anti-objetivos
- Consultas lentas: atingir profundidade máxima sem timeout (`LIMIT` nos resultados e iteração com early stop).

## Restrições
- Banco de dados: SQLite, tabela `relations` (session_id, project, source, relation, target), já existente.
- Nomes: armazenados normalizados no banco; exibição preserva o primeiro visto (original).
- CLI: `cc.py` existente, novo subcomando.
- Vault: markdown de entidades em `Entidades/<nome>.md` junto com `Sessões/*.md` e `_resumo.md`.

## Abordagens
- **A. Busca relacional pura em SQLite** *(escolhida)*: BFS iterativo (`WITH RECURSIVE` ou Python), sem cache, sem índice. Simples, sem overhead.
- B. Cache do grafo em memória (Redis, arquivo JSON): overhead desnecessário para grafo que muda a cada sessão.
- C. Neo4j ou TigerGraph: overkill para a escala daqui; adiciona dependência pesada.

## Desenho
- `src/contextclaude/graph.py`: `normalize(name: str) -> str` (minúsculas, strip, espaço→espaço único, remover pontuação dupla); `neighbors(conn, entity: str, projeto=None, depth: int = 2, limit: int = 100)` → lista de vizinhos com caminho (origem → relação → destino → ...).
- `src/contextclaude/notes.py`: novo `write_entity_note(projeto, name, relations)` que gera `vault/<proj>/Entidades/<nome>.md` com lista de relações de/para, como `[[outra_entidade]] via relação`.
- Worker: após `_update_summary`, chama `graph.write_entities(conn, projeto)` (lê `relations` dessa sessão, escreve uma página por entidade vista).
- `src/cc.py`: novo subcomando `grafo <entidade>` (mesmo estilo de `buscar`, com `-p` e `-d` opcionais). Exibe os vizinhos em formato legível: `<origem> --relação--> <destino>`.
- Teste: grafo manual de exemplo; consulta por profundidade; normalization; bidireção (A→B e B←A).

## Critérios de sucesso
1. Uma entidade com duas relações de saída e uma de entrada devolve todas as três em qualquer profundidade ≥ 1.
2. `cc.py grafo x -d 1` retorna só vizinhos diretos; `-d 2` inclui vizinhos dos vizinhos.
3. Nomes "SQLite" e "sqlite" na mesma sessão criam um nó único no banco e uma página de entidade.
4. `vault/<proj>/Entidades/sqlite.md` contém `[[outra_entidade]]` se há relação de/para.
5. Sem timeout em grafo moderado (10-100 relações, profundidade 2).

## Decisões minhas
- Sem cache: a grafo muda de sessão em sessão, caching aumenta complexidade e falsos positivos.
- Normalização case-insensitive: "password", "Password", "PASSWORD" são secreto único; "Firebase" e "firebase" são ferramentas únicas.
- Profundidade padrão: 2 (vizinhos e vizinhos-dos-vizinhos); usuário pode aumentar.
- Bidirecional no vault: `A --rel--> B` e `B <--rel-- A` aparecem nas duas páginas (Obsidian faz isso com links).

## Deixado ao construtor
Escolha do algoritmo (SQL recursivo vs Python BFS); limite de profundidade por padrão; formato de saída da CLI (ASCII art, tabela, ou linha); normalização exata de nomes (remover URLs, pontuação, etc.).

## Nota: Problema de dados real
O banco tem 0 fatos e 0 relações. A extração falha em transcrições grandes (JSON cortado pelo `num_predict` do modelo). **Sugestão antes da Fase 3:** fixar o worker para reprocessar o bloco com chunking menor se a extração falhar (tarefa auxiliar, não na Fase 3).
