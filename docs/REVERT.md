# Reverter o ContextClaude

Os hooks ficam em `C:\Users\João\.claude\settings.json`, chave `hooks` (`SessionStart` e `SessionEnd`).

**Desligar:** apague a chave `hooks` (ou só os dois comandos que apontam para `ContextClaude\src\hook.py`).

**Restaurar o estado anterior:** copie `C:\Users\João\.claude\settings.json.bak-contextclaude` por cima de `settings.json`.
Atenção: isso também desfaz qualquer outra mudança feita no `settings.json` depois de 2026-10-29.

**Fase 2 (busca semântica):** o hook `UserPromptSubmit` (`hook.py prompt`) está na mesma chave `hooks`; apague só ele para parar a injeção por prompt. Backup anterior à Fase 2: `settings.json.bak-fase2`. O índice fica em `data\chroma` e pode ser apagado a qualquer momento (`python src\cc.py indexar` reconstrói a partir do SQLite). `python -m pip uninstall chromadb` remove a dependência; o worker só loga "indexação falhou".

**Desligar só o recall num projeto:** apague o arquivo `.contextclaude` da pasta do projeto.

Os dados (`data\contextclaude.db`) e o vault (`vault\`) não são apagados por nada acima.
