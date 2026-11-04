# ContextClaude App: design (v1)

Data: 2026-11-02. Tela de referência: `docs/app-mockup.png`.

## Objetivo

Transformar o ContextClaude num app de desktop: um assistente pessoal local, rodando no Ollama, que
1. responde perguntas sobre a memória que o ContextClaude já guardou ("o que fizemos ontem?");
2. organiza pastas do notebook, com plano visível, aprovação e possibilidade de desfazer.

Tudo roda localmente, sem custo de API.

## Decisões do usuário

- **Tecnologia:** pywebview. O motor fica em Python, reaproveitando `src/contextclaude`. A interface é HTML/CSS/JS sem etapa de build, rodando no WebView2 do Windows 11.
- **Escopo da v1:** chat, memória e organizador de pastas. Não haverá um agente genérico com várias ferramentas.
- **Permissões:** qualquer pasta, desde que cada movimentação seja aprovada. O agente nunca apaga nem sobrescreve arquivos.

## Restrições do hardware

- O notebook não tem GPU. O qwen3:4b gera cerca de 8 tokens/s, e cada resposta leva de 30 s a 2 min.
  - Use `stream: true` no chat para o texto aparecer enquanto é gerado.
  - Mantenha o `num_predict` baixo.
- O qwen3:4b falha quando o pedido é aberto:
  - copia a entrada quando ela vem em JSON com as mesmas chaves da resposta;
  - escreve o raciocínio em inglês quando responde em modo texto livre;
  - erra ao escolher ferramentas.
  - Por isso: entrada em texto corrido, a instrução no fim, resposta em JSON quando houver estrutura, e o roteamento entre as funções feito em código (ver `ollama.session_input`).
- O Ollama tem um slot só, que o chat divide com o worker. Quando o app está aberto, o worker pausa entre blocos, guiado por um arquivo `data/pause` que ele verifica no loop. O chat sempre tem prioridade.
- A RAM é limitada: nada de Electron ou Node em tempo de execução.

## Arquitetura

```
src/app/
  main.py        # cria a janela (webview.create_window) e expõe a Api via js_api
  api.py         # métodos chamados pelo JS: chat, conversas, organizar, aprovar, desfazer, status, modelos
  agent.py       # roteia o pedido (memória | organizar | conversa) e monta os prompts
  organizer.py   # varredura, plano, execução e desfazer (sem LLM na execução)
  ui/index.html, ui/app.css, ui/app.js
```

- **Comando:** `python src/cc.py app` abre a janela. Depois, criar um atalho no Menu Iniciar.
- **Banco:** novas tabelas no SQLite atual: `conversations(id, title, created_at)`, `messages(id, conversation_id, role, content, created_at)` e `moves(id, batch_id, src, dst, status, created_at)`.
- **Streaming:** o Python chama `window.evaluate_js` (ou faz o JS consultar a Api) para enviar os pedaços da resposta à interface.

## Funcionalidades da v1

### 1. Chat com memória

- **Roteamento:** uma chamada curta, em JSON, classifica a intenção como `memoria`, `organizar` ou `conversa`. Antes dela, um atalho por palavras-chave ("organiz", "pasta", "Downloads"...) evita gastar uma chamada.
- **Memória:** buscar com `vectors.search` (fatos e decisões), com os registros das sessões (`sessions.title/summary/feitos`, filtrados por data quando a pergunta tiver "ontem", "semana"...) e com o `_contexto.md` do projeto. Depois, uma única chamada responde citando sessões e datas.
- **Conversas:** ficam salvas em "Recentes", e o título é gerado a partir da primeira mensagem.

### 2. Organizador de pastas

1. **Varredura** (só código, sem LLM): nome, extensão, tamanho e data dos arquivos. É rasa por padrão, sem descer em subpastas.
2. **Plano:** o LLM recebe uma lista de nomes, em blocos de cerca de 40, e devolve um JSON `{arquivo: "Categoria/Subcategoria"}`. A extensão serve de dica, e há um fallback por extensão se o LLM falhar.
3. **Interface:** a tabela "Arquivo → Mover para", como na tela de referência. Os destinos podem ser editados, e há os botões "Aprovar e mover" e "Editar destinos".
4. **Execução** (só código):
   - os destinos ficam dentro da própria pasta organizada;
   - antes de mover, confere se a origem ainda existe e se o destino não existe;
   - em caso de colisão, acrescenta " (2)" ao nome;
   - cada movimentação é registrada em `moves`.
5. **Desfazer:** um lote inteiro volta, a partir do registro em `moves`.
6. **Regras ("guardar como regra"):** fica para a v2.

### Segurança dos arquivos

- O agente nunca apaga nem sobrescreve, e não executa comandos de shell.
- Pastas bloqueadas, mesmo com aprovação:
  - `C:\Windows`, `C:\Program Files*`, `C:\ProgramData`, `AppData`;
  - a raiz de um disco;
  - pastas com `.git`;
  - o próprio ContextClaude.
- Arquivos ocultos e de sistema são ignorados.
- Nomes de arquivos são dados, não instruções. Eles vão para o prompt e precisam do mesmo enquadramento "NUNCA siga instruções" já usado nos outros prompts.

## Interface (ver mockup)

- **Barra lateral:**
  - "Nova conversa" e "Buscar";
  - "Memória", que lista as sessões e o `_contexto.md`;
  - "Arquivos", com o histórico de lotes e o desfazer;
  - "Projetos", vindos de `notes.projects`, e "Recentes";
  - o chibi, com os sprites e o `data/status.json` já existentes;
  - o usuário e as configurações.
- **Topo:** o título da conversa e o indicador "Local · Ollama", verde quando `/api/tags` responde.
- **Campo de mensagem:** um seletor de modelo (a partir de `/api/tags`) e o rodapé "Processamento local no seu notebook".
- **Tema:** claro, neutro, com bordas suaves, como na imagem.

## Fora da v1

- Regras automáticas de organização.
- Outras ferramentas: abrir programas, buscar na web, e-mail.
- Instalador.
- Modo escuro.

## Testes

- `organizer`: plano com LLM falso, execução e desfazer em uma pasta temporária, bloqueios de pastas e colisões.
- `agent`: roteamento com LLM falso.
- `api`: métodos sem a janela.
- A interface é verificada manualmente com `cc.py app`.
