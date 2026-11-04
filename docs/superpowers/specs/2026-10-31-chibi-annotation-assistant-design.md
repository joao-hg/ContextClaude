# Assistente de anotações (chibi v2) — design

## Intenção e porquê

O chibi hoje (ver `2026-10-30-chibi-assistant-design.md`) é puramente passivo:
reflete o estado atual do pipeline (`idle/pensando/digitando/processando/
feliz/notificacoes`) numa animação, sem nenhum texto, nenhuma noção de
progresso e nenhuma notificação fora da própria janela. O usuário quer
evoluir isso para um "assistente de anotações" de verdade: avisa quando
começa a anotar, mostra quanto tempo falta (estimativa que aprende com o
histórico), avisa quando termina com um resumo do que encontrou, avisa
erro com texto explicando o motivo, e permite clicar no bonequinho pra ver
um histórico das últimas rodadas.

## Goals

- Um aviso por **rodada** (uma execução de `pipeline.process_queue` que
  processa pelo menos uma sessão), não por sessão individual nem por
  transição de estado interno — balão de texto perto do bonequinho **e**
  notificação nativa do Windows, juntos.
- Durante uma rodada ativa, mostrar um tempo de espera que **aprende com o
  histórico** (duração de rodadas anteriores); sem histórico suficiente,
  mostrar só o tempo decorrido ("anotando há Xs"), nunca um número
  inventado.
- Ao terminar com sucesso: aviso com resumo (quantas sessões processadas,
  quantos fatos/decisões encontrados).
- Ao terminar com erro: aviso com texto explicando o motivo (hoje só existe
  o pulso visual "notificacoes", sem nenhuma mensagem).
- Clique simples no bonequinho (sem arrastar) abre/fecha um painel com o
  histórico das últimas rodadas (quando, duração, sessões, o que foi
  encontrado).
- A animação/pulsos existentes continuam funcionando exatamente como hoje.

## Non-goals

- Mudar *quando* o pipeline roda (continua só em `SessionStart`/
  `SessionEnd`, via `hooks.py`) — fora de escopo mexer no gatilho.
- Um framework de notificação genérico pro resto do projeto — isto é
  específico do chibi.
- Mostrar o texto completo dos fatos/decisões no painel de histórico — só
  contagens e metadados; ler o conteúdo real continua sendo via vault.

## Anti-goals (seria falha mesmo "funcionando")

- Notificação/toast disparando por sessão dentro da mesma rodada (viraria
  spam em fila acumulada).
- Toast ou balão atrasando/travando o worker — tem que ser best-effort,
  igual `chibi_status` hoje (qualquer falha é engolida, nunca propaga).
- Histórico de rodadas crescendo sem limite no banco.
- Round que abre e nunca fecha (ex.: erro no meio) deixando o balão
  mostrando contagem regressiva pra sempre.

## Constraints

- Mesmas do design original (Windows apenas, pipeline não pode ficar mais
  lento/instável, escrita de status essencialmente grátis).
- `pywin32` (já presente neste ambiente, mas não declarado) passa a ser
  dependência nova em `requirements.txt`. Só o processo do widget o
  importa — mesmo isolamento que já existe para Pillow/tkinter (worker.py
  nunca importa nada de UI/notificação; se `pywin32` faltar numa outra
  máquina, o bonequinho e o pipeline continuam funcionando, só o toast
  nativo some).
- `Shell_NotifyIcon` precisa de um HWND, mas não precisa de uma janela/
  message loop dedicada — `root.winfo_id()` do Tk já serve, já que só
  vamos chamar `NIM_ADD`/`NIM_MODIFY` com `NIIF_INFO`, sem interatividade
  no próprio ícone de bandeja.

## Approaches (decididas na conversa)

1. **Notificação nativa**: `pywin32` (`win32gui.Shell_NotifyIcon` com
   `NIIF_INFO`) — ESCOLHIDA. Zero dependência pesada nova (já presente no
   ambiente), API simples, e o Windows 10/11 já renderiza isso como toast
   da Central de Ações. Descartadas: `win11toast`/`windows-toasts`
   (dependência nova não testada aqui, mais bonitas mas risco maior agora);
   PowerShell BurntToast via subprocess (exige instalar módulo PS à parte,
   mais lento por chamada).
2. **Histórico de duração**: tabela nova no sqlite que o projeto já usa
   (`data/contextclaude.db`, via `db.py`) — ESCOLHIDA. Consistente com o
   padrão existente, fácil agregar/podar com SQL. Descartada: arquivo JSON
   solto tipo log (mais simples de escrever, pior pra agregar e limitar
   tamanho).
3. **Balão de texto**: segunda janela Tk (`Toplevel`), mesma técnica de
   color-key/transparência do widget principal — ESCOLHIDA. Reaproveita
   técnica já validada. Descartada: desenhar o texto dentro do próprio
   frame do sprite (acopla o texto à animação, exigiria re-renderizar a
   cartela a cada tick só pra atualizar um contador).

## O que fica a cargo de quem implementar

### Schema novo (`db.py`)

Tabela `chibi_rounds`, uma linha por rodada fechada (sucesso ou erro):

```sql
CREATE TABLE IF NOT EXISTS chibi_rounds(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT, duration_s REAL,
    sessoes INTEGER, chars_total INTEGER,
    fatos INTEGER, decisoes INTEGER, pendencias INTEGER,
    status TEXT NOT NULL,  -- 'done' | 'error'
    mensagem TEXT          -- motivo, só quando status='error'
);
```

Sugestão: helper `db.round_throughput(conn)` devolvendo `chars_total /
duration_s` médio das últimas N rodadas `status='done'` (ex.: últimas 10),
ou `None` se não houver nenhuma — é o número usado pra estimar ETA da
próxima rodada. Podar linhas antigas (manter só as últimas ~50) é decisão
de implementação, não precisa ser exato.

### `chibi_status.py` — novos campos/funções

O `status.json` ganha dois blocos novos, mantendo o padrão já existente de
`pulso_estado`/`pulso_evento_id` para detecção de "evento novo" mesmo que
o poll do widget rode depois do estado já ter mudado:

- `rodada`: `{"ativa": bool, "inicio": ts, "estimativa_s": float|None,
  "sessoes": int}` — presente enquanto uma rodada está rodando, `None`
  quando não há nenhuma.
- `ultima_rodada`: `{"evento_id": int, "status": "done"|"error",
  "duration_s": float, "sessoes": int, "fatos": int, "decisoes": int,
  "mensagem": str|None}` — sobrescrito a cada rodada fechada;
  `evento_id` incremental próprio (mesmo truque do pulso) pra o widget
  detectar "isso é novo" mesmo se só ler depois.

Funções novas (nomes sugeridos, escrita atômica igual `write_status`):
`write_rodada_inicio(inicio_ts, estimativa_s, sessoes)` e
`write_rodada_fim(status, duration_s, sessoes, fatos, decisoes,
mensagem=None)`. `write_status` existente não muda.

### `pipeline.py` / `worker.py` — instrumentação

Em `process_queue`: antes do loop, calcular o batch pendente (`_pending`),
se vazio não abre rodada nenhuma (sem aviso). Se não vazio: somar bytes
pendentes por `os.path.getsize(path) - offset` de cada linha (barato, só
`stat`), consultar `db.round_throughput`, calcular `estimativa_s =
chars_total / throughput` (ou `None`), chamar `write_rodada_inicio`.

Durante o loop, acumular `fatos`/`decisoes`/`pendencias` (os dicts `data`
já têm essas listas em `_process_one` — somar os tamanhos) **e** os bytes
de fato consumidos por sessão processada (`new_off - offset_original`,
não a estimativa pré-scan). Ao final **sempre** fechar a rodada que foi
aberta. Contrato importante pro builder: toda `write_rodada_inicio` tem
que ter exatamente uma `write_rodada_fim` correspondente — inclusive nos
retornos antecipados (`down`) e em exceções não previstas.

**Falha parcial dentro da rodada** (decisão, revisada pelo builder-check):
hoje `_fail` marca a sessão individual como `status='error'` na tabela
`sessions` quando as tentativas se esgotam (`attempts >= MAX_ATTEMPTS`) e
o loop de `process_queue` segue para as outras sessões sem propagar
exceção — ou seja, uma rodada com 4 sessões OK e 1 esgotada retornaria
normalmente hoje. Para a rodada refletir isso: `process_queue` deve
acumular as falhas terminais (sessões que `_fail` marcou como `'error'`,
mais o caso `down`) e, se houver qualquer uma, fechar a rodada como
`status='error'` com uma mensagem resumida (ex.: `"4 sessões anotadas, 1
falhou: <motivo da última falha>"`), mesmo que outras sessões tenham sido
concluídas — consistente com o pulso `notificacoes` que a sessão
individual já dispara hoje. Só fecha como `"done"` quando **nenhuma**
sessão da rodada falhou definitivamente.

`chars_total` gravado em `chibi_rounds` no fechamento é o **consumo real**
(soma dos bytes efetivamente lidos/processados na rodada), não a
estimativa pré-scan usada só para calcular `estimativa_s` no início —
evita que `round_throughput` fique enviesado por rodadas com falha
parcial (sessão pulada ainda conta os bytes que outras sessões, sim,
consumiram).

`db.round_throughput(conn)`: taxa **agregada**, `soma(chars_total) /
soma(duration_s)` das últimas N rodadas `status='done'` — não a média das
taxas rodada a rodada, que deixaria rodadas pequenas (duração perto de
zero) distorcerem o resultado.

Inserir a linha correspondente em `chibi_rounds` no mesmo momento de
`write_rodada_fim` (pelo worker/pipeline, que já tem a conexão sqlite
aberta).

### `chibi_widget.py` — UI nova

1. **Bolha de texto**: `Toplevel` adicional, mesma técnica de
   `-transparentcolor`, posicionada acima/ao lado do bonequinho. Visível
   quando `rodada.ativa` (texto recalculado a cada tick: `"Anotando N
   sessões · ~MM:SS"` contando regressivo a partir de `inicio` +
   `estimativa_s`; sem estimativa, `"Anotando... (Xs)"` só com o tempo
   decorrido) e por alguns segundos após detectar um `ultima_rodada` novo
   (resumo de sucesso ou texto de erro).
2. **Notificação nativa**: módulo novo `contextclaude/chibi_notify.py`
   (mesmo princípio de isolamento de `chibi_gui.py` — só o processo do
   widget importa `pywin32`), com uma função `notify(titulo, texto)`
   best-effort (qualquer exceção, incluindo `pywin32` ausente, é
   engolida). Chamada pelo `chibi_widget.py` ao detectar início de rodada
   nova e `ultima_rodada` nova — nunca pelo `pipeline.py`/`worker.py`
   diretamente, pra manter o pipeline livre de dependência de UI.
3. **Clique vs. arraste** (revisado pelo builder-check): hoje
   `<ButtonPress-1>` já inicia o arraste, e `<Double-Button-1>`/
   `<Button-3>` fecham a janela. No Windows, a sequência de um duplo-clique
   é ButtonPress→ButtonRelease→ButtonPress→Double-Button-1→ButtonRelease —
   ou seja, todo duplo-clique passa por um `<ButtonRelease-1>` de
   deslocamento pequeno antes do segundo press. Um handler naive em
   `<ButtonRelease-1>` ("deslocamento pequeno = clique, abre o painel")
   dispararia em *todo* duplo-clique também, concorrendo com o fechamento
   existente. Fix: no `<ButtonRelease-1>` com deslocamento pequeno, agendar
   a abertura do painel com `root.after(250, abrir_historico)` (cancelável);
   se `<Double-Button-1>` chegar antes dos 250ms, cancelar esse `after` — o
   fechamento continua funcionando exatamente como hoje, só o clique simples
   fica com ~250ms de atraso perceptível antes de abrir o painel.
4. **Painel de histórico**: `Toplevel` simples (pode ter borda normal,
   não precisa ser frameless como o bonequinho) listando as últimas ~10
   linhas de `chibi_rounds` (leitura direta via sqlite, que já está em
   WAL mode — leitura concorrente com o worker é segura). Aberto/fechado
   pelo clique simples no sprite.

### `requirements.txt`

Adicionar `pywin32`.

### Testes a estender/criar

- `tests/test_chibi_status.py`: `write_rodada_inicio`/`write_rodada_fim`.
- `tests/test_db_project.py` (ou arquivo novo): schema de `chibi_rounds`
  e `round_throughput` (com histórico vazio, com dados).
- `tests/test_pipeline.py`: rodada abre e fecha exatamente uma vez por
  `process_queue` com trabalho; fila vazia não abre rodada; erro/`down`
  fecha a rodada com `status='error'`.
- `tests/test_chibi_widget.py`: lógica pura de clique-vs-arraste (testável
  sem Tk real, mesmo padrão de `_apply_status`/`_advance_frame`).
- `tests/test_chibi_notify.py` (novo): wrapper best-effort engole exceção
  (mock de `win32gui` levantando erro).
