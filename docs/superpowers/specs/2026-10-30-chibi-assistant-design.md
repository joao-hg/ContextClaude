# Assistente chibi — design

## Intenção e porquê

Hoje o pipeline de anotação (Ollama local extraindo fatos/decisões dos
transcripts, via `worker.py`) roda inteiramente invisível: o usuário não
sabe se está parado, lento, travado ou trabalhando. O objetivo é dar um
feedback visual contínuo e agradável disso enquanto o Claude Code está
aberto, usando uma cartela de sprites já pronta (personagem chibi, 6
estados × 8 frames cada: IDLE, PENSANDO, DIGITANDO, FELIZ, PROCESSANDO,
NOTIFICAÇÕES).

## Goals

- Mostrar em tempo real o que o pipeline de anotação está fazendo.
- Reaproveitar a infraestrutura já existente (hooks, lock por arquivo,
  `config.py`) em vez de criar um sistema paralelo.
- Robustez: o widget nunca pode travar, atrasar ou derrubar o pipeline
  de anotação em si.

## Non-goals

- Não é um assistente interativo/de chat — não recebe comandos, não
  responde perguntas.
- Não cobre múltiplos personagens/skins — só a cartela fornecida.
- Não inicia com o Windows nem fica ligado fora de sessões do Claude
  Code.

## Anti-goals (seria falha mesmo "funcionando")

- Atrapalhar o pipeline (ex.: escrita de status bloquear o worker).
- Mais de uma instância do bichinho na tela ao mesmo tempo.
- Ficar "fantasma" na tela depois que todas as sessões do Claude Code
  já fecharam.

## Constraints

- Windows apenas (o projeto todo já assume isso: `msvcrt`, `tasklist`).
- Sem dependências pesadas: `tkinter` já é stdlib; única dependência
  nova é `Pillow`, para fatiar a cartela de sprites.
- O pipeline já é lento por ser CPU-bound (~9 tok/s, sem GPU) —
  qualquer instrumentação de status precisa ser essencialmente grátis
  (escrita de um JSON pequeno, nada de I/O pesado por frame).
- Processamento continua sequencial, um worker por vez (`lock.py` já
  garante isso) — isso não muda.

## Approaches consideradas

### Detecção de estado do pipeline

1. **Arquivo de status (`data/status.json`), escrito pelo
   `pipeline.py`/`ollama.py`/`worker.py` a cada transição de fase —
   ESCOLHIDA.** Simples, desacoplada (se o widget cair, o pipeline não
   percebe), e segue o padrão já usado no projeto (`log.txt`,
   `worker.lock`).
2. Inferir o estado só lendo o sqlite (`sessions`) — descartada: as
   transições "pensando" (chamando o Ollama) vs. "digitando" (escrevendo
   no vault) acontecem dentro da mesma linha de processamento, sem
   granularidade nenhuma no banco pra distinguir uma da outra.
3. IPC via socket entre worker e widget — descartada: complexidade
   desnecessária para o volume de dados (um estado + um id de sessão).

### Ciclo de vida do processo do widget

1. **Checagem periódica via `tasklist`** se ainda existe algum processo
   do Claude Code rodando, e se fecha sozinho quando não tiver mais
   nenhum — ESCOLHIDA. Autorrecuperável: não depende de nenhum hook
   disparar corretamente no fechamento (janela fechada à força, crash
   etc.). Critério confirmado por observação direta nesta máquina:
   a sessão do Claude Code aparece no `tasklist` como processo
   `claude.exe` (um único processo por sessão, não colide com os
   `python.exe` do hook/worker/widget). O widget considera "tem sessão
   viva" quando a contagem de `claude.exe` em `tasklist` for > 0.
2. Contagem de sessões abertas via hooks (`SessionStart`
   incrementa/`SessionEnd` decrementa) — descartada: se o `SessionEnd`
   não disparar (fechar a janela do terminal direto), o contador nunca
   volta a zero e o bichinho fica preso na tela pra sempre.

### Singleton do processo

- Reaproveita o mesmo padrão de `worker.py`/`lock.py`: o hook sempre
  tenta spawnar o processo do widget (barato, idempotente); o processo
  do widget tenta uma trava exclusiva de arquivo logo ao iniciar — se
  já existe outro rodando, sai imediatamente sem fazer nada.

### GUI

- Janela `tkinter` sem borda, sempre no topo (`-topmost`), num canto da
  tela, com `Pillow` fatiando a cartela de sprites em frames. Único
  caminho razoável dado o stack atual do projeto (puro Python, sem
  Electron/webview); não há alternativa real a comparar aqui.

## Decisões já tomadas na conversa

- **Compartilhado**: um único widget, não um por sessão — reflete o
  estado geral da fila (que já é global: mesmo worker, mesmo banco,
  vários projetos).
- **Liga/desliga**: aparece no primeiro `SessionStart` que o encontrar
  desligado; desliga sozinho quando a checagem por `tasklist` não achar
  mais nenhum processo do Claude Code.
- **Mapeamento de estados** (pipeline → animação da cartela):
  - `idle`: fila vazia, nada rodando.
  - `pensando`: chamada ativa ao Ollama em `ollama._chat` (tanto
    `extract` de um chunk quanto `merge_summary`).
  - `digitando`: escrevendo arquivos no `vault/`
    (`notes.write_session_note`, `notes.write_summary`,
    `graph.write_entities`).
  - `processando`: demais etapas do worker em volta disso (ler
    transcript novo, salvar fatos/relações no sqlite) — estado
    "guarda-chuva" para o que não é nem `pensando` nem `digitando`;
    decisão minha, o usuário pode corrigir se achar que não faz
    sentido.
  - `feliz`: pulso transiente ao concluir uma sessão com sucesso.
  - `notificacoes`: pulso transiente em erro (Ollama fora do ar,
    `HTTPError`, sessão caiu em `status='error'`).

## O que fica a cargo de quem implementar

- Formato exato do `status.json` (sugestão:
  `{"estado": str, "sessao": str|null, "evento_id": int, "ts": str}`,
  com `evento_id` incrementando a cada escrita para o widget detectar
  pulsos transientes de `feliz`/`notificacoes` mesmo que poll perca
  algum). Escrita atômica (write-temp-then-rename), já tem precedente
  no projeto (`db.py`/`notes.py` costumam escrever assim).
- Onde exatamente instrumentar cada escrita de estado dentro de
  `pipeline.py` (`_extract_resilient`, `_process_one`,
  `_update_summary`) e `ollama.py` (`_chat`).
- Geometria de corte da cartela de sprites: arquivo de referência tem
  1881×836px, grade de 6 linhas (um estado por linha) × 8 frames — a
  coluna de rótulo texto à esquerda na imagem de referência (ex.: "IDLE
  / 8 FRAMES") não faz parte da grade de sprites recortável; confirmar
  a grade exata (tamanho de célula, bordas/gutters) ao fatiar com
  Pillow.
- Onde salvar a cartela no repo: sugestão `assets/chibi/sprites.png`.
- Módulos: `src/contextclaude/chibi_status.py` (só stdlib — leitura e
  escrita atômica do `status.json`, nenhuma dependência de Pillow/
  tkinter) é o que `pipeline.py`/`ollama.py`/`worker.py` importam para
  reportar estado. Pillow e tkinter ficam isolados em
  `src/chibi_widget.py` (entry point, espelhando `hook.py`/`worker.py`)
  e/ou `src/contextclaude/chibi_gui.py`, que só o processo do widget
  importa — assim um Pillow ausente/quebrado nunca derruba o pipeline
  de anotação em si (só o bichinho deixa de aparecer).
- `hooks.py`: adicionar `_spawn_chibi()` chamado só em `session_start`
  (não em `session_end` — o widget se desliga sozinho).
- `config.py`: generalizar `lock_path()` para aceitar um nome (ou
  adicionar `chibi_lock_path()`/`status_path()` dedicados).
- Posição/tamanho da janela na tela: sugestão canto inferior direito,
  arrastável, lembrando a posição entre sessões num arquivo local —
  detalhe de UX menor, ajustável depois sem redesenhar a arquitetura.
- Intervalos de polling: sugestão 300–500ms para o `status.json` e
  5–10s para a checagem via `tasklist` (mais cara).
- Dependência nova: o projeto não tem `requirements.txt`/`pyproject.toml`
  hoje (instalação manual). Decisão minha: criar um `requirements.txt`
  mínimo com `pillow`, já que será a primeira dependência externa do
  projeto.
