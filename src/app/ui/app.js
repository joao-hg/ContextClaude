'use strict';
// Interface do ContextClaude. Regra de segurança: todo texto vindo da Api entra só por
// textContent / value / propriedades seguras; innerHTML só recebe os SVGs fixos de ICONS.
(() => {
  // ---------- ícones (marcação fixa) ----------
  const svg = (body) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>';
  const PAGE = '<path d="M6 2.75h8.25L19 7.5v13.75H6z"/><path d="M14 2.75V7.5h5"/>';
  const ICONS = {
    folder: svg('<path d="M3 6.5a1.5 1.5 0 0 1 1.5-1.5h4.4l2 2.2h8.6A1.5 1.5 0 0 1 21 8.7v9.8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>'),
    doc: svg(PAGE + '<path d="M9 12h7M9 15.5h7"/>'),
    'chevron-down': svg('<path d="m6 9 6 6 6-6"/>'),
    user: svg('<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20.5c1.3-3.6 4-5.3 7.5-5.3s6.2 1.7 7.5 5.3"/>'),
    gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'),
    dots: svg('<circle cx="5" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="19" cy="12" r="1.2" fill="currentColor"/>'),
    send: svg('<path d="M21 3 10.5 13.5"/><path d="M21 3 14.5 21l-4-7.5L3 9.5z"/>'),
    f_pdf: svg(PAGE + '<path d="M8.5 17.5c2-1.2 3.6-3.6 4.2-6.5.3-1.4-1.3-1.6-1.3-.2 0 2.6 2.4 5.3 4.6 5.9 1.2.3 1.3-1.1-.1-1.2-2.3-.2-5.6.8-7.4 2z"/>'),
    f_img: svg(PAGE + '<circle cx="10" cy="11" r="1.4"/><path d="m8 18.5 3.5-3.5 2 2 1.5-1.5 2 3"/>'),
    f_sheet: svg(PAGE + '<path d="M8.5 11.5h8v7h-8zM8.5 15h8M12.5 11.5v7"/>'),
    f_doc: svg(PAGE + '<path d="M9 11h7M9 14h7M9 17h5"/>'),
    f_zip: svg(PAGE + '<path d="M11 4v2M12 6v2M11 8v2M12 10v2"/><rect x="10.3" y="13" width="2.4" height="3" rx=".6"/>'),
    f_media: svg(PAGE + '<path d="m10.5 11.5 5 3-5 3z"/>'),
    f_exe: svg(PAGE + '<circle cx="12.5" cy="14.5" r="2.2"/><path d="M12.5 10.8v1.2M12.5 17v1.2M8.8 14.5H10M15 14.5h1.2"/>'),
    f_code: svg(PAGE + '<path d="m10.5 12.5-2 2 2 2M14.5 12.5l2 2-2 2"/>'),
    f_file: svg(PAGE),
  };
  const EXT_KIND = {};
  const kinds = {
    pdf: 'pdf',
    img: 'jpg jpeg png gif webp bmp svg heic heif tif tiff ico raw',
    sheet: 'xls xlsx xlsm csv ods tsv',
    doc: 'doc docx odt rtf txt md ppt pptx odp epub',
    zip: 'zip rar 7z tar gz bz2 xz tgz iso',
    media: 'mp3 wav flac ogg m4a aac mp4 mkv avi mov webm wmv',
    exe: 'exe msi bat cmd ps1 appx msix',
    code: 'py js ts html htm css json xml yml yaml c cpp h java go rs sql sh',
  };
  for (const [k, list] of Object.entries(kinds)) for (const e of list.split(' ')) EXT_KIND[e] = k;
  const COLOR = { pdf: 'ic-pdf', img: 'ic-img', sheet: 'ic-sheet', doc: 'ic-doc', zip: 'ic-zip',
    media: 'ic-media', exe: 'ic-gen', code: 'ic-code', file: 'ic-gen' };

  const CHIBI_ROWS = { idle: 0, pensando: 1, digitando: 2, feliz: 3, processando: 4, notificacoes: 5 };
  const SPRITE_RE = /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/;

  // ---------- utilitários de DOM ----------
  const $ = (id) => document.getElementById(id);
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function icon(name, cls) {
    const s = el('span', 'icon' + (cls ? ' ' + cls : ''));
    s.innerHTML = Object.prototype.hasOwnProperty.call(ICONS, name) ? ICONS[name] : ICONS.f_file;
    return s;
  }
  function fileIcon(name) {
    const m = /\.([^.]+)$/.exec(String(name));
    const kind = (m && EXT_KIND[m[1].toLowerCase()]) || 'file';
    return icon('f_' + kind, COLOR[kind]);
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const str = (v) => (typeof v === 'string' ? v : v === undefined || v === null ? '' : String(v));
  function baseName(p) {
    const parts = str(p).split(/[\\/]+/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : str(p);
  }
  function fmtDate(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(str(s));
    if (!m) return str(s);
    return `${m[3]}/${m[2]}/${m[1]}` + (m[4] ? ` ${m[4]}:${m[5]}` : '');
  }
  const prettyDest = (d) => (str(d) ? str(d).split('/').join(' / ') : '—');

  // ---------- ponte com a Api ----------
  async function call(method, ...args) {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api[method] !== 'function') return { error: 'A interface ainda não está conectada.' };
    try {
      return await api[method](...args);
    } catch (e) {
      return { error: 'Falha ao falar com o aplicativo: ' + str(e && e.message ? e.message : e) };
    }
  }
  const isErr = (r) => !r || (typeof r === 'object' && !Array.isArray(r) && 'error' in r);
  const errText = (r, fallback) => (r && typeof r.error === 'string' && r.error) || fallback;

  // ---------- estado ----------
  const S = {
    ready: false,
    convId: null,         // conversa exibida (null = nova)
    viewToken: 0,         // muda a cada troca de conversa; invalida respostas atrasadas
    loading: false,       // carregando mensagens da conversa exibida
    deferred: [],         // eventos da conversa exibida que chegaram durante o carregamento
    shown: new Set(),     // ids de mensagens já desenhadas (evita duplicar ao recarregar)
    project: null,
    sending: false,
    req: null,            // { rid: string|null, buffer: [] } da resposta em andamento
    streamEl: null,
    statusEl: null,
    conversations: [],
    searchSeq: 0,
    view: 'chat',         // 'chat' | 'memory' | 'files'
    panelSeq: 0,
    activity: [],
    activityOpen: false,
    chatStale: false,
    models: [],
    serverState: 'idle',
    liveState: null,
    frame: 0,
  };

  const ui = {};

  // ---------- campo de mensagem ----------
  function updateSendBtn() {
    ui.send.disabled = S.sending || !S.ready || !ui.input.value.trim();
  }
  function setSending(on) {
    S.sending = on;
    updateSendBtn();
  }
  function autosize() {
    ui.input.style.height = 'auto';
    ui.input.style.height = Math.min(ui.input.scrollHeight, 200) + 'px';
  }

  // ---------- mensagens ----------
  function nearBottom() {
    const v = ui.chatView;
    return v.scrollHeight - v.scrollTop - v.clientHeight < 120;
  }
  function scrollBottom() { ui.chatView.scrollTop = ui.chatView.scrollHeight; }
  function append(node, force) {
    const stick = force || nearBottom();
    ui.messages.appendChild(node);
    ui.empty.hidden = true;
    if (stick) scrollBottom();
    return node;
  }
  function clearMessages() {
    for (const n of Array.from(ui.messages.children)) if (n !== ui.empty) n.remove();
    ui.empty.hidden = false;
    S.shown = new Set();
    S.streamEl = null;
    S.statusEl = null;
  }
  const appendUser = (text, force) => append(el('div', 'msg-user', text), force);
  const appendAssistant = (text) => append(el('div', 'msg-assistant', text));
  const appendError = (text) => append(el('div', 'msg-error', text), true);
  function clearStatus() {
    if (S.statusEl) S.statusEl.remove();
    S.statusEl = null;
  }

  function renderMessage(m) {
    if (!m || typeof m !== 'object') return;
    if (m.id !== undefined) {
      if (S.shown.has(m.id)) return;
      S.shown.add(m.id);
    }
    if (m.role === 'user') appendUser(str(m.content));
    else if (m.role === 'plan') append(renderPlan(m));
    else appendAssistant(str(m.content));
  }

  // ---------- plano do organizador ----------
  function renderPlan(m) {
    let p = null;
    try { p = JSON.parse(str(m.content)); } catch (e) { p = null; }
    if (!p || typeof p !== 'object' || !Array.isArray(p.items)) return el('div', 'msg-error', 'Não consegui ler este plano.');

    const st = {
      items: p.items.filter((i) => i && typeof i.name === 'string').map((i) => ({ name: i.name, dest: str(i.dest) })),
      editing: false,
      inputs: new Map(),
      failed: new Map(),
      moved: new Set(),
      busy: false,
    };

    const card = el('div', 'plan');
    const head = el('div', 'plan-head');
    const folderName = el('span', 'folder-name', baseName(p.folder));
    folderName.title = str(p.folder);
    const collapse = el('button', 'collapse');
    collapse.type = 'button';
    collapse.title = 'Mostrar/ocultar arquivos';
    collapse.appendChild(icon('chevron-down'));
    collapse.addEventListener('click', () => card.classList.toggle('collapsed'));
    const truncated = Number.isInteger(p.total) && p.total > st.items.length;
    const countText = truncated ? `${st.items.length} de ${p.total} arquivos` : plural(st.items.length, 'arquivo', 'arquivos');
    head.append(icon('folder'), folderName, el('span', 'count-pill', countText), collapse);

    const table = el('table');
    const thead = el('thead');
    const hr = el('tr');
    hr.append(el('th', null, 'Arquivo'), el('th', null, 'Mover para'));
    thead.appendChild(hr);
    const tbody = el('tbody');
    table.append(thead, tbody);

    const footer = el('div', 'plan-footer');
    card.append(head, table, footer);

    function readInputs() {
      for (const it of st.items) {
        const inp = st.inputs.get(it.name);
        if (inp) it.dest = inp.value.trim();
      }
    }

    function drawRows() {
      tbody.textContent = '';
      st.inputs = new Map();
      for (const it of st.items) {
        const tr = el('tr');
        if (st.failed.has(it.name)) tr.className = 'failed';
        else if (st.moved.has(it.name)) tr.className = 'moved';
        const tdFile = el('td');
        const cell = el('div', 'file-cell');
        cell.append(fileIcon(it.name), el('span', null, it.name));
        tdFile.appendChild(cell);
        const tdDest = el('td');
        if (st.editing) {
          const inp = el('input', 'dest-input');
          inp.type = 'text';
          inp.spellcheck = false;
          inp.value = it.dest;
          inp.setAttribute('aria-label', 'Destino de ' + it.name);
          st.inputs.set(it.name, inp);
          tdDest.appendChild(inp);
        } else {
          tdDest.appendChild(el('span', 'dest-text', prettyDest(it.dest)));
        }
        if (st.failed.has(it.name)) tdDest.appendChild(el('span', 'row-reason', st.failed.get(it.name)));
        tr.append(tdFile, tdDest);
        tbody.appendChild(tr);
      }
    }

    function drawPending() {
      footer.textContent = '';
      footer.appendChild(el('div', 'plan-note', 'Nenhum arquivo foi movido ainda.'));
      const actions = el('div', 'plan-actions');
      const approve = el('button', 'btn-primary', 'Aprovar e mover');
      approve.type = 'button';
      const edit = el('button', 'btn-link', st.editing ? 'Concluir edição' : 'Editar destinos');
      edit.type = 'button';
      const error = el('div', 'plan-error');
      actions.append(approve, edit);
      footer.append(actions, error);

      edit.addEventListener('click', () => {
        if (st.busy) return;
        if (st.editing) readInputs();
        st.editing = !st.editing;
        drawRows();
        edit.textContent = st.editing ? 'Concluir edição' : 'Editar destinos';
        if (st.editing && st.inputs.size) st.inputs.values().next().value.focus();
      });

      approve.addEventListener('click', async () => {
        if (st.busy) return;
        if (st.editing) readInputs();
        st.busy = true;
        approve.disabled = true;
        edit.disabled = true;
        error.textContent = '';
        approve.textContent = 'Movendo…';
        const chosen = st.items.map((it) => ({ name: it.name, dest: it.dest }));
        const res = await call('approve', m.id, chosen);
        st.busy = false;
        if (isErr(res) || !Array.isArray(res.moved) || !Array.isArray(res.failed)) {
          approve.disabled = false;
          edit.disabled = false;
          approve.textContent = 'Aprovar e mover';
          error.textContent = errText(res, 'Não foi possível mover os arquivos.');
          return;
        }
        p.batch_id = res.batch_id;
        p.result = res;
        st.editing = false;
        drawDone(res, false);
      });
    }

    function drawDone(result, undone) {
      const moved = Array.isArray(result && result.moved) ? result.moved : [];
      const failed = Array.isArray(result && result.failed) ? result.failed : [];
      st.moved = undone ? new Set() : new Set(moved.map((x) => x && x.name));
      st.failed = new Map(failed.map((x) => [x && x.name, str(x && x.motivo) || 'falhou']));
      drawRows();
      footer.textContent = '';
      if (!undone) footer.appendChild(el('div', 'plan-result', plural(moved.length, 'arquivo movido', 'arquivos movidos')));
      if (failed.length) {
        const ul = el('ul', 'plan-fails');
        for (const f of failed) ul.appendChild(el('li', null, `${str(f && f.name)}: ${str(f && f.motivo) || 'falhou'}`));
        footer.appendChild(ul);
      }
      if (undone) {
        footer.appendChild(el('div', 'plan-note', 'Movimentação desfeita.'));
        return;
      }
      if (!moved.length || !p.batch_id) return;
      const actions = el('div', 'plan-actions');
      const undoBtn = el('button', 'btn-ghost', 'Desfazer');
      undoBtn.type = 'button';
      const msg = el('div', 'plan-error');
      actions.appendChild(undoBtn);
      footer.append(actions, msg);
      undoBtn.addEventListener('click', async () => {
        undoBtn.disabled = true;
        msg.textContent = '';
        const res = await call('undo', p.batch_id);
        if (isErr(res) || typeof res.restored !== 'number') {
          undoBtn.disabled = false;
          msg.textContent = errText(res, 'Não foi possível desfazer.');
          return;
        }
        p.undone = true;
        drawDone(p.result || {}, true);
        footer.appendChild(el('div', 'plan-note',
          plural(res.restored, 'arquivo voltou', 'arquivos voltaram') + ' para o lugar.'));
        if (Array.isArray(res.failed) && res.failed.length) {
          const ul = el('ul', 'plan-fails');
          for (const f of res.failed) ul.appendChild(el('li', null, `${str(f && f.name)}: ${str(f && f.motivo)}`));
          footer.appendChild(ul);
        }
      });
    }

    if (p.batch_id) drawDone(p.result || {}, !!p.undone);
    else { drawRows(); drawPending(); }
    return card;
  }

  // ---------- eventos da Api ----------
  window.ccEvent = (ev) => {
    try {
      if (!ev || typeof ev !== 'object') return;
      if (!S.req) {                      // sem resposta pendente: só desenha, sem mexer no estado
        if (ev.conversation_id === S.convId && !S.loading) renderEvent(ev);
        return;
      }
      if (S.req.rid === null) {          // send() ainda não devolveu o request_id
        if (S.req.buffer.length < 10000) S.req.buffer.push(ev);
        return;
      }
      if (ev.request_id !== S.req.rid) return;
      handleEvent(ev);
    } catch (e) {
      console.error(e);
    }
  };

  function handleEvent(ev) {
    const type = ev.type;
    if (type === 'status') S.liveState = 'pensando';
    else if (type === 'chunk') S.liveState = 'digitando';
    const here = ev.conversation_id === S.convId;
    if (here) {
      if (S.loading) S.deferred.push(ev);
      else renderEvent(ev);
    }
    if (type === 'done' || type === 'error') {
      S.req = null;
      S.liveState = null;
      setSending(false);
      if (!here) S.streamEl = null;
      refreshRecents();
    }
  }

  function renderEvent(ev) {
    switch (ev.type) {
      case 'status':
        if (!S.statusEl) S.statusEl = append(el('div', 'msg-status'));
        S.statusEl.textContent = str(ev.text);
        break;
      case 'chunk':
        clearStatus();
        if (!S.streamEl) S.streamEl = append(el('div', 'msg-assistant streaming'));
        {
          const stick = nearBottom();
          S.streamEl.textContent += str(ev.text);
          if (stick) scrollBottom();
        }
        break;
      case 'plan':
        clearStatus();
        renderMessage(ev.message);
        break;
      case 'done': {
        clearStatus();
        const msg = ev.message && typeof ev.message === 'object' ? ev.message : null;
        const already = msg && msg.id !== undefined && S.shown.has(msg.id);
        if (S.streamEl) {
          if (already) S.streamEl.remove();
          else {
            S.streamEl.classList.remove('streaming');
            if (msg) {
              S.streamEl.textContent = str(msg.content);
              if (msg.id !== undefined) S.shown.add(msg.id);
            }
          }
        } else if (msg) {
          renderMessage(msg);
        }
        S.streamEl = null;
        break;
      }
      case 'error':
        clearStatus();
        if (S.streamEl) S.streamEl.classList.remove('streaming');
        S.streamEl = null;
        appendError(str(ev.text) || 'Algo deu errado.');
        break;
      default:
        break;
    }
  }

  async function send() {
    if (S.sending || !S.ready) return;
    const text = ui.input.value.trim();
    if (!text) return;
    showChat();
    if (S.loading) return;  // a conversa está sendo recarregada; o texto fica no campo
    const token = S.viewToken;
    const convId = S.convId;
    setSending(true);
    const bubble = appendUser(text, true);
    ui.input.value = '';
    autosize();
    updateSendBtn();
    S.req = { rid: null, buffer: [] };
    S.liveState = 'pensando';
    const model = ui.model.value || null;
    const res = await call('send', convId, text, model, S.project);
    if (isErr(res) || typeof res.request_id !== 'string') {
      S.req = null;
      S.liveState = null;
      bubble.remove();
      if (S.viewToken === token) {
        if (!ui.input.value) { ui.input.value = text; autosize(); }
        appendError(errText(res, 'Não foi possível enviar a mensagem.'));
      }
      setSending(false);
      return;
    }
    const rid = res.request_id;
    const buffered = S.req ? S.req.buffer : [];
    if (S.req) { S.req.rid = rid; S.req.buffer = []; }
    if (S.viewToken === token && convId === null) {
      S.convId = res.conversation_id;
      setTitle(text);
    }
    refreshRecents();
    for (const ev of buffered) if (S.req && S.req.rid === rid && ev.request_id === rid) handleEvent(ev);
  }

  // ---------- conversas ----------
  function setTitle(fallback) {
    const c = S.conversations.find((x) => x.id === S.convId);
    ui.title.textContent = c ? str(c.title) : str(fallback) || 'Nova conversa';
  }

  function newChat() {
    S.viewToken++;
    S.convId = null;
    S.loading = false;
    S.deferred = [];
    clearMessages();
    ui.title.textContent = 'Nova conversa';
    showChat();
    markRecents();
    ui.input.focus();
  }

  async function openConversation(id) {
    const token = ++S.viewToken;
    S.convId = id;
    S.loading = true;
    S.deferred = [];
    clearMessages();
    showChat();
    setTitle('');
    markRecents();
    const res = await call('messages', id);
    if (token !== S.viewToken) return;
    S.loading = false;
    if (isErr(res) || !Array.isArray(res)) {
      appendError(errText(res, 'Não consegui abrir a conversa.'));
    } else {
      for (const m of res) renderMessage(m);
    }
    const deferred = S.deferred;
    S.deferred = [];
    for (const ev of deferred) renderEvent(ev);
    scrollBottom();
  }

  async function refreshRecents() {
    if (!S.ready) return;
    const seq = ++S.searchSeq;
    const q = ui.search.value.trim();
    const res = await call('conversations', q);
    if (seq !== S.searchSeq) return;
    ui.recents.textContent = '';
    if (isErr(res) || !Array.isArray(res)) {
      ui.recents.appendChild(el('li', 'hint', errText(res, 'Não consegui listar as conversas.')));
      return;
    }
    S.conversations = res;
    if (!res.length) {
      ui.recents.appendChild(el('li', 'hint', q ? 'Nada encontrado.' : 'Nenhuma conversa ainda.'));
    }
    for (const c of res) {
      const li = el('li');
      const b = el('button');
      b.type = 'button';
      b.dataset.id = String(c.id);
      b.append(icon('doc'), el('span', 'label', str(c.title)));
      b.title = str(c.title);
      b.addEventListener('click', () => openConversation(c.id));
      li.appendChild(b);
      ui.recents.appendChild(li);
    }
    markRecents();
    if (S.convId !== null && S.view === 'chat') setTitle(ui.title.textContent);
  }

  function markRecents() {
    for (const b of ui.recents.querySelectorAll('button')) {
      b.classList.toggle('active', S.convId !== null && b.dataset.id === String(S.convId));
    }
  }

  // ---------- projetos ----------
  async function loadProjects() {
    const res = await call('projects');
    ui.projects.textContent = '';
    if (isErr(res) || !Array.isArray(res)) {
      ui.projects.appendChild(el('li', 'hint', errText(res, 'Não consegui listar os projetos.')));
      return;
    }
    if (!res.length) ui.projects.appendChild(el('li', 'hint', 'Nenhum projeto ainda.'));
    for (const name of res) {
      const li = el('li');
      const b = el('button');
      b.type = 'button';
      b.dataset.project = str(name);
      b.append(icon('folder'), el('span', 'label', str(name)));
      b.title = str(name);
      b.addEventListener('click', () => setProject(S.project === str(name) ? null : str(name)));
      li.appendChild(b);
      ui.projects.appendChild(li);
    }
    markProjects();
  }

  function setProject(name) {
    S.project = name;
    markProjects();
    ui.projectChip.hidden = !name;
    ui.projectChip.textContent = name ? 'Projeto: ' + name : '';
    if (S.view === 'memory') openMemory();
  }

  function markProjects() {
    for (const b of ui.projects.querySelectorAll('button')) {
      b.classList.toggle('active', b.dataset.project === S.project);
    }
  }

  // ---------- painéis ----------
  function showChat() {
    const wasPanel = S.view !== 'chat';
    S.view = 'chat';
    ui.chatView.hidden = false;
    ui.panelView.hidden = true;
    ui.navMemory.classList.remove('active');
    ui.navFiles.classList.remove('active');
    S.activityOpen = false;
    if (wasPanel) {
      setTitle(S.convId === null ? 'Nova conversa' : ui.title.dataset.chat || '');
      if (S.chatStale && S.convId !== null && !S.sending) {
        S.chatStale = false;
        openConversation(S.convId);
      }
    }
  }

  function showPanel(kind, title) {
    if (S.view === 'chat') ui.title.dataset.chat = ui.title.textContent;
    S.view = kind;
    ui.chatView.hidden = true;
    ui.panelView.hidden = false;
    ui.navMemory.classList.toggle('active', kind === 'memory');
    ui.navFiles.classList.toggle('active', kind === 'files');
    S.activityOpen = kind === 'activity';
    ui.title.textContent = title;
    ui.panelTitle.textContent = title;
    ui.panelBody.textContent = '';
    ui.panelBody.appendChild(el('div', 'muted', 'Carregando…'));
    return ++S.panelSeq;
  }

  // ---------- atividade do worker ----------
  function actRow(r) {
    const tipo = r && (r.tipo === 'ok' || r.tipo === 'erro') ? r.tipo : 'info';
    const li = el('li', 'act ' + tipo);
    const ts = str(r && r.ts);
    li.appendChild(el('span', 'act-time', ts.slice(11, 16)));
    li.appendChild(el('span', 'act-text', str(r && r.texto)));
    return li;
  }

  function renderActivity(list) {
    const prev = S.activity;
    const top = (l) => (l.length ? str(l[0] && l[0].ts) + '|' + str(l[0] && l[0].texto) : '');
    // poll a cada 5 s: sem mudança, não reconstrói (preserva a rolagem do painel)
    if (prev.length === list.length && top(prev) === top(list)) return;
    S.activity = list;
    ui.activityMini.textContent = '';
    for (const r of list.slice(0, 4)) ui.activityMini.appendChild(actRow(r));
    if (S.activityOpen) renderActivityPanel();
  }

  function renderActivityPanel() {
    ui.panelBody.textContent = '';
    if (!S.activity.length) {
      ui.panelBody.appendChild(el('div', 'muted', 'Nenhuma atividade ainda.'));
      return;
    }
    const ul = el('ul', 'activity-full');
    for (const r of S.activity) ul.appendChild(actRow(r));
    ui.panelBody.appendChild(ul);
  }

  function openActivity() {
    showPanel('activity', 'Atividade');
    renderActivityPanel();
  }

  async function openMemory() {
    const seq = showPanel('memory', S.project ? 'Memória · ' + S.project : 'Memória');
    const res = await call('memory', S.project);
    if (seq !== S.panelSeq) return;
    const body = ui.panelBody;
    body.textContent = '';
    if (isErr(res) || !Array.isArray(res.sessions)) {
      body.appendChild(el('div', 'msg-error', errText(res, 'Não consegui ler a memória.')));
      return;
    }
    body.appendChild(el('h3', 'section-title', 'Sessões'));
    if (!res.sessions.length) body.appendChild(el('div', 'muted', 'Nenhuma sessão registrada.'));
    for (const s of res.sessions) {
      const card = el('div', 'card');
      const h = el('div', 'card-head');
      const meta = [fmtDate(s.date), S.project ? '' : str(s.project)].filter(Boolean).join(' · ');
      h.append(el('div', 'card-title', str(s.title)), el('div', 'card-meta', meta));
      card.appendChild(h);
      if (str(s.summary)) card.appendChild(el('div', 'card-body', str(s.summary)));
      body.appendChild(card);
    }
    body.appendChild(el('h3', 'section-title', '_contexto.md'));
    if (!S.project) {
      body.appendChild(el('div', 'muted', 'Escolha um projeto em Projetos para ver o _contexto.md.'));
    } else if (!str(res.context)) {
      body.appendChild(el('div', 'muted', 'Este projeto ainda não tem _contexto.md.'));
    } else {
      body.appendChild(el('pre', 'context', str(res.context)));
    }
  }

  async function openFiles(notice) {
    const seq = showPanel('files', 'Arquivos');
    const res = await call('batches');
    if (seq !== S.panelSeq) return;
    const body = ui.panelBody;
    body.textContent = '';
    if (notice) body.appendChild(notice);
    if (isErr(res) || !Array.isArray(res)) {
      body.appendChild(el('div', 'msg-error', errText(res, 'Não consegui listar os lotes.')));
      return;
    }
    if (!res.length) body.appendChild(el('div', 'muted', 'Nenhum arquivo foi movido pelo organizador ainda.'));
    for (const b of res) {
      const card = el('div', 'card');
      const h = el('div', 'card-head');
      const title = el('div', 'card-title', str(b.folder));
      h.append(title, el('div', 'card-meta', fmtDate(b.created_at)));
      const row = el('div', 'card-row');
      const counts = [plural(Number(b.moved) || 0, 'movido', 'movidos'),
        plural(Number(b.undone) || 0, 'desfeito', 'desfeitos'),
        plural(Number(b.failed) || 0, 'falha', 'falhas')].join(' · ');
      row.appendChild(el('div', 'muted', counts));
      if (Number(b.moved) > 0) {
        const undoBtn = el('button', 'btn-ghost', 'Desfazer');
        undoBtn.type = 'button';
        undoBtn.addEventListener('click', async () => {
          undoBtn.disabled = true;
          const r = await call('undo', str(b.batch_id));
          S.chatStale = true;
          let note;
          if (isErr(r) || typeof r.restored !== 'number') {
            note = el('div', 'msg-error', errText(r, 'Não foi possível desfazer.'));
          } else {
            note = el('div', 'card');
            note.appendChild(el('div', 'plan-result',
              plural(r.restored, 'arquivo voltou', 'arquivos voltaram') + ' para o lugar.'));
            if (Array.isArray(r.failed) && r.failed.length) {
              const ul = el('ul', 'plan-fails');
              for (const f of r.failed) ul.appendChild(el('li', null, `${str(f && f.name)}: ${str(f && f.motivo)}`));
              note.appendChild(ul);
            }
          }
          if (S.view === 'files') openFiles(note);
        });
        row.appendChild(undoBtn);
      }
      card.append(h, row);
      body.appendChild(card);
    }
  }

  // ---------- status, modelos e chibi ----------
  let statusBusy = false;
  async function pollStatus() {
    if (!S.ready || statusBusy) return;
    statusBusy = true;
    try {
      const res = await call('status');
      if (isErr(res)) {
        ui.pill.classList.remove('on');
        ui.pill.title = 'Ollama não respondeu';
        return;
      }
      const up = !!res.ollama;
      ui.pill.classList.toggle('on', up);
      ui.pill.title = up ? 'Ollama respondendo' : 'Ollama não respondeu';
      S.serverState = Object.prototype.hasOwnProperty.call(CHIBI_ROWS, res.estado) ? res.estado : 'idle';
      ui.chibiText.textContent = str(res.mensagem);
      if (up && !S.models.length) loadModels();
      const act = await call('activity', 30);
      if (Array.isArray(act)) renderActivity(act);
    } finally {
      statusBusy = false;
    }
  }

  async function loadModels() {
    const res = await call('models');
    if (isErr(res)) return;
    const list = Array.isArray(res.models) ? res.models.map(str).filter(Boolean) : [];
    const def = str(res.default);
    const prev = ui.model.value;
    // sem lista real antes (Ollama ainda subindo), prev é só o fallback do servidor: não preservar
    const hadList = S.models.length > 0;
    S.models = list;
    const names = list.slice();
    if (def && !names.includes(def)) names.unshift(def);
    ui.model.textContent = '';
    for (const n of names) {
      const o = el('option', null, n);
      o.value = n;
      ui.model.appendChild(o);
    }
    if (!names.length) {
      const o = el('option', null, 'padrão');
      o.value = '';
      ui.model.appendChild(o);
    }
    ui.model.value = hadList && prev && names.includes(prev) ? prev : def || (names[0] || '');
  }

  async function loadSprite() {
    const res = await call('chibi_sprite');
    if (typeof res === 'string' && SPRITE_RE.test(res)) {
      ui.chibi.style.backgroundImage = 'url("' + res + '")';
    } else {
      ui.chibi.hidden = true;
    }
  }

  function tickChibi() {
    const state = S.liveState || S.serverState || 'idle';
    const row = CHIBI_ROWS[state] || 0;
    S.frame = (S.frame + 1) % 8;
    ui.chibi.style.backgroundPosition = `${(S.frame * 100) / 7}% ${(row * 100) / 5}%`;
  }

  // ---------- inicialização ----------
  function bindUi() {
    for (const n of document.querySelectorAll('[data-icon]')) n.innerHTML = ICONS[n.dataset.icon] || '';
    Object.assign(ui, {
      input: $('input'), send: $('send'), model: $('model'), messages: $('messages'), empty: $('empty'),
      chatView: $('chat-view'), panelView: $('panel-view'), panelTitle: $('panel-title'), panelBody: $('panel-body'),
      title: $('title'), pill: $('ollama-pill'), search: $('search'), recents: $('recents'),
      projects: $('projects'), projectChip: $('project-chip'), chibi: $('chibi'), chibiText: $('chibi-text'),
      activityMini: $('activity-mini'), navMemory: $('nav-memory'), navFiles: $('nav-files'),
    });

    ui.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    ui.input.addEventListener('input', () => { autosize(); updateSendBtn(); });
    ui.send.addEventListener('click', send);
    $('new-chat').addEventListener('click', newChat);
    ui.navMemory.addEventListener('click', openMemory);
    ui.navFiles.addEventListener('click', () => openFiles(null));
    ui.chibi.addEventListener('click', openActivity);
    ui.chibi.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openActivity();
      }
    });
    $('panel-close').addEventListener('click', showChat);

    let searchTimer = 0;
    ui.search.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(refreshRecents, 250);
    });

    const toggle = $('projects-toggle');
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      ui.projects.hidden = !open;
    });

    setInterval(tickChibi, 125);
    updateSendBtn();
  }

  let started = false;
  function start() {
    if (started) return;
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.status !== 'function') return;
    started = true;
    S.ready = true;
    updateSendBtn();
    loadSprite();
    loadModels();
    loadProjects();
    refreshRecents();
    pollStatus();
    setInterval(pollStatus, 5000);
  }

  bindUi();
  window.addEventListener('pywebviewready', start);
  start();  // a ponte pode já estar pronta quando o script roda
})();
