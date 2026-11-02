# ContextClaude — Design

Data: 2026-10-29 · Status: aguardando aprovação

## Intenção
Memória local e permanente para o Claude Code. Quando uma sessão do Claude Code roda, o Qwen3 4B (via Ollama) anota o que aconteceu, sem gastar tokens do Claude. As notas ficam num vault do Obsidian (uma pasta por projeto) e num SQLite. Em projetos escolhidos, um resumo curto pode voltar ao Claude no início da sessão.

## Objetivos
- Captura automática, ligada ao ciclo de vida do Claude Code (hooks).
- Zero tokens do Claude na captura e no processamento.
- Uma pasta por projeto no Obsidian, criada pelo diretório onde o Claude Code abriu.
- Recall opcional, por projeto, com resumo pequeno.
- Evoluir em camadas: SQLite+Obsidian → ChromaDB → Cognee.

## Não objetivos
- Claude Desktop (fora do escopo).
- Nuvem, sincronização, multiusuário.
- Mudar o comportamento do Claude além de injetar o resumo opcional.

## Anti-objetivos (seria fracasso mesmo funcionando)
- Hook que atrasa a abertura do Claude Code ou escreve texto no contexto sem querer.
- Qualquer chamada ao `claude` ou à API Anthropic pelo worker.
- Falha do ContextClaude que derrube ou trave uma sessão.
- Segredos (chaves, tokens) gravados no vault.

## Restrições
- Windows 11, PowerShell 5.1, Python 3.13, Ollama com `qwen3:4b` já instalado. Obsidian não está instalado (o vault é só uma pasta de Markdown; instalar o app fica a seu cargo).
- Fase 1 usa só a biblioteca padrão do Python.

## Abordagens consideradas
- **A. Tudo de uma vez** (SQLite+ChromaDB+Cognee): visão completa, mas muitas dependências de uma vez, risco no Windows/Python 3.13, difícil isolar falhas.
- **B. Em camadas** *(escolhida)*: cada camada funciona antes da próxima.
- **C. Só SQLite+Obsidian**: simples, sem busca semântica nem grafo.

## Desenho da Fase 1
Estrutura: `C:\Users\João\ContextClaude\{vault, src, data, docs}`.

**Hooks** (em `~/.claude/settings.json`, globais, todos terminam com exit 0 e saída vazia salvo o recall):
- `SessionStart`: garante o Ollama no ar (inicia `ollama serve` destacado se a porta 11434 não responder), registra a sessão, enfileira transcrições de sessões anteriores que ficaram sem processar (cobre fechamento abrupto) e, se existir o marcador, imprime o resumo. Meta: concluir em cerca de 1 s; o trabalho pesado fica no worker.
- `SessionEnd`: enfileira a transcrição e dispara o worker destacado.

**Worker** (processo separado, fila no SQLite): lê a transcrição JSONL, filtra segredos por padrão de texto, divide em blocos e pede ao Qwen3 (modo sem raciocínio, saída JSON) fatos, decisões, pendências e relações entre entidades. Grava:
- SQLite: sessões, fatos, relações (com origem e data).
- Vault: `vault/<Projeto>/Sessões/AAAA-MM-DD-hhmm.md` com `[[links]]` para entidades e `vault/<Projeto>/_resumo.md`, resumo consolidado e atualizado a cada sessão.

**Projeto**: nome da pasta do diretório de trabalho; se dois caminhos tiverem o mesmo nome, acrescenta um sufixo curto de hash. Diretório inicial (`C:\Users\João`) vira o projeto `_geral`.

**Recall opcional**: um arquivo `.contextclaude` na pasta do projeto liga o recall. Só nesses projetos o `SessionStart` injeta o `_resumo.md`, limitado a um teto de caracteres (a definir na implementação, ordem de ~1500).

## Fases seguintes (cada uma com spec própria depois da Fase 1)
- Fase 2: ChromaDB para busca por significado (exige um modelo de embeddings no Ollama).
- Fase 3: Cognee para grafo; se não rodar bem no Python 3.13/Windows ou com modelo de 4B, avaliamos alternativa antes de insistir.

## Critérios de sucesso da Fase 1
1. Sessão de teste gera nota no vault e linhas no SQLite em poucos minutos após o fim.
2. Saída dos hooks vazia sem marcador (verificado) e nenhum processo `claude` criado pelo worker.
3. Com marcador, o resumo aparece no início da sessão, dentro do teto.
4. Matar o worker ou o Ollama no meio não afeta a sessão; o item é reprocessado depois.

## Decisões minhas (não suas — corrija se discordar)
- Ollama não é encerrado ao fechar o Claude Code; o modelo só é descarregado da memória por `keep_alive` curto (~2 min).
- `SessionEnd` + varredura no `SessionStart`, em vez de rodar a cada `Stop` (evita processar a cada turno).
- Filtro de segredos por regex antes de enviar ao modelo.
- Backup do `settings.json` antes de editar, com instruções de reversão em `docs/`.
- Formato exato da transcrição será confirmado na implementação.

## Deixado ao construtor
Esquema exato das tabelas, prompts de extração, limites de tamanho dos blocos, teto final do recall, rotação de logs (`data/log`).
