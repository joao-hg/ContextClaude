# ContextClaude — Fase 2 (busca por significado)

Data: 2026-10-29 · Status: aguardando aprovação · Pré-requisito: Fase 1 (`docs/design.md`)

## Intenção
Encontrar memórias por significado, não só por palavras, de duas formas: (1) busca manual por linha de comando; (2) recall mais inteligente, em projetos marcados com `.contextclaude`, que injeta no contexto apenas os itens relevantes ao prompt digitado.

## Objetivos
- Indexar fatos, decisões e pendências no ChromaDB, em segundo plano, sem tokens do Claude.
- `cc.py buscar` para consulta manual (custo zero de tokens).
- Hook `UserPromptSubmit` que injeta poucos itens relevantes, só em projetos com marcador.

## Não objetivos
- Ferramenta MCP, Cognee/grafo (Fase 3), reranking, indexar a transcrição bruta.

## Anti-objetivos
- Hook que passe de ~5 s ou trave o prompt do usuário.
- Injetar o mesmo item repetidamente na mesma sessão, ou injetar algo fraco só para "preencher".
- Qualquer envio de dados a serviços externos (telemetria do ChromaDB desligada).

## Restrições
- Embeddings: `qwen3-embedding:0.6b` no Ollama (já baixado; 1024 dimensões; teste em 2026-10-29: similaridade 0,68 entre frases relacionadas contra 0,35 entre não relacionadas). Roda na CPU.
- Dependência nova: `chromadb` (pip, Python 3.13). O restante segue na biblioteca padrão.
- SQLite continua a fonte da verdade; o Chroma pode ser apagado e reconstruído.

## Abordagens
- **A. Chroma como índice derivado do SQLite** *(escolhida)*: coluna `facts.indexed`; o worker indexa pendentes depois da fila; `cc.py indexar` reconstrói tudo.
- B. Gravar direto no Chroma no pipeline: acopla extração e indexação; uma falha de embedding afetaria a sessão.
- C. Busca vetorial em SQLite puro (sem Chroma): mais simples, mas foge da stack pedida.

## Desenho
- `src/contextclaude/embed.py`: `embed(texts) -> list[list[float]]` via `POST /api/embed` (modelo acima, `keep_alive` 10 min; timeout curto configurável).
- `src/contextclaude/vectors.py`: `PersistentClient` em `data/chroma`, coleção `memoria` (cosseno), id `fato-<id>`, metadados `projeto`, `tipo`, `sessao`, `data`. `index_pending(conn, limit)` (lotes de 32; falha deixa pendente), `search(query, projeto=None, n=5)` → lista com texto, projeto, tipo, data, distância.
- Esquema: migração `ALTER TABLE facts ADD COLUMN indexed INTEGER DEFAULT 0` quando faltar.
- Worker: após `process_queue`, chama `index_pending`; erro de embedding só é logado.
- `src/cc.py buscar "<consulta>" [-p projeto] [-n 5]` e `cc.py indexar` (reconstrução).
- Hook `UserPromptSubmit` (`hook.py prompt`): só com marcador no `cwd`; ignora prompts com menos de 15 caracteres; aplica `redact` ao prompt; embed com timeout de ~4 s; consulta o projeto atual (n=4, descarta distância acima de um limite); remove itens já injetados na sessão (tabela `injected(session_id, fact_id)`); teto de ~800 caracteres; saída `additionalContext`. Qualquer falha (Ollama fora, Chroma ausente, timeout) termina em silêncio com exit 0.
- Recall do `SessionStart` (`_resumo.md`) continua como está; o do prompt é complementar e limitado.

## Critérios de sucesso
1. `cc.py buscar` acha um fato relacionado sem palavras em comum com a consulta.
2. Prompt hook: saída vazia sem marcador, com Ollama fora, e para prompts curtos; com marcador e Ollama no ar, ≤ 800 caracteres e sem repetição na sessão.
3. Apagar `data/chroma` e rodar `cc.py indexar` restaura a busca.
4. Falha de embedding nunca afeta a Fase 1 (notas e SQLite seguem normais).

## Decisões minhas
- `qwen3-embedding:0.6b`: menor modelo multilíngue disponível que testei; trocar exige reindexar.
- Limite de distância e `n=4` são valores iniciais; calibro com dados reais na implementação.
- Timeout do hook `UserPromptSubmit` no `settings.json`: 8 s (o embed usa ~4 s; com o modelo frio na CPU o primeiro prompt pode ficar sem recall).
- `anonymized_telemetry=False` no Chroma.

## Deixado ao construtor
Calibração dos limites, formato do texto injetado, mensagens da CLI, tamanho de lote.
