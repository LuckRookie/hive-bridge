const apiFetch = (url, opts) =>
  window.sidecar && window.sidecar.fetch ? window.sidecar.fetch(url, opts) : fetch(url, opts);

const state = {
  host: '',
  tab: 'live',
  snapshot: null,
  pollTimer: null,
  live: {
    out: '',
    offset: 0,
    runtime: '',
    threadId: '',
    turnId: '',
    cwd: '',
    name: '',
    status: 'idle',
    prompt: '',
    streamText: '',
    tools: [],
    summary: null,
    timer: null,
  },
};

const $ = (id) => document.getElementById(id);

function showToast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 2400);
}

function fmtTime(ts) {
  if (!ts) return '';
  const ms = ts > 1e12 ? ts : ts * 1000;
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function esc(str) {
  return String(str ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function setStatus(kind, label) {
  const pill = $('status-pill');
  pill.className = 'status-pill ' + (kind || '');
  $('status-text').textContent = label;
}

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll('.tab').forEach((btn) => {
    const on = btn.dataset.tab === tab;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-selected', String(on));
  });
  document.querySelectorAll('.panel').forEach((p) => {
    p.classList.toggle('active', p.id === `panel-${tab}`);
  });
}

function renderHosts(snap) {
  const sel = $('host-select');
  const prev = state.host || snap.host || '';
  const hosts = snap.hosts || [];
  sel.innerHTML = hosts.length
    ? hosts.map((h) => `<option value="${esc(h)}">${esc(h)}</option>`).join('')
    : '<option value="">(none)</option>';
  if (prev && hosts.includes(prev)) {
    sel.value = prev;
    state.host = prev;
  } else if (snap.host) {
    sel.value = snap.host;
    state.host = snap.host;
  }
  if (snap.defaultCwd && !$('diff-cwd').value) {
    $('diff-cwd').value = snap.defaultCwd;
  }
}

function renderBoard(snap) {
  const errEl = $('board-error');
  if (snap.errors?.bus) {
    errEl.textContent = snap.errors.bus;
    errEl.classList.remove('hidden');
  } else {
    errEl.classList.add('hidden');
  }

  const msgs = snap.messages || [];
  const unreadCount = msgs.filter((m) => !m.ackedAt).length;
  $('count-board').textContent = unreadCount ? `${unreadCount}` : (msgs.length ? `${msgs.length}` : '');

  // Update topic filter dropdown preserving selection
  const topicSel = $('filter-topic');
  const currentTopic = topicSel.value;
  const topics = [...new Set(msgs.map((m) => m.topic).filter(Boolean))];
  topicSel.innerHTML =
    '<option value="">All topics</option>' +
    topics.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
  if (topics.includes(currentTopic)) topicSel.value = currentTopic;

  const fTopic = topicSel.value;
  const fTo = $('filter-to').value;
  const fUnread = $('filter-unread').checked;

  const filtered = msgs.filter((m) => {
    if (fTopic && m.topic !== fTopic) return false;
    if (fTo && m.to !== fTo) return false;
    if (fUnread && m.ackedAt) return false;
    return true;
  });

  const list = $('board-list');
  const workers = snap.workers || [];
  const workersBar = workers.length
    ? `<div class="card" style="padding:7px 10px;">
         <div class="meta" style="margin-bottom:4px;">Seated Workers (<code>hive-relay-bus wait</code>):</div>
         <div class="card-actions" style="margin-top:0;">
           ${workers
             .map((w) => {
               const cls = w.status === 'waiting' ? 'active' : w.status === 'busy' ? 'warn' : '';
               return `<button type="button" class="btn sm" data-seat="${esc(w.agent)}" title="${esc(w.cwd || '')}">
                 @${esc(w.agent)} <span class="pill ${cls}">${esc(w.status)}</span>
               </button>`;
             })
             .join('')}
         </div>
       </div>`
    : '';

  if (!filtered.length) {
    list.innerHTML = workersBar + '<div class="empty">No blackboard messages match the filter.</div>';
    list.querySelectorAll('[data-seat]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const seat = btn.dataset.seat;
        const sel = $('post-to');
        if (![...sel.options].some((o) => o.value === seat)) {
          const opt = document.createElement('option');
          opt.value = seat;
          opt.textContent = `@${seat}`;
          sel.appendChild(opt);
        }
        sel.value = seat;
        $('post-text').focus();
      });
    });
    return;
  }

  list.innerHTML = workersBar + filtered
    .slice()
    .reverse()
    .map((m) => {
      const unread = !m.ackedAt;
      return `
        <article class="card ${unread ? 'unread' : ''}">
          <div class="card-head">
            <div>
              <strong>${esc(m.from)}</strong> → <strong>@${esc(m.to)}</strong>
              <span class="pill">${esc(m.topic)}</span>
            </div>
            <span class="meta">#${m.id} · ${fmtTime(m.createdAt)}</span>
          </div>
          <div class="body-text">${esc(m.body)}</div>
          ${m.cwd ? `<div class="meta">cwd: ${esc(m.cwd)}</div>` : ''}
          ${m.artifact ? `<div class="meta">artifact: ${esc(m.artifact)}</div>` : ''}
          <div class="card-actions">
            ${unread ? `<button type="button" class="btn sm" data-ack="${m.id}">Ack</button>` : `<span class="meta">acked by ${esc(m.ackedBy || 'ok')}</span>`}
            ${window.sidecar?.agent ? `<button type="button" class="btn sm" data-relay="${m.id}">Relay to Chat</button>` : ''}
          </div>
        </article>`;
    })
    .join('');

  list.querySelectorAll('[data-seat]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const seat = btn.dataset.seat;
      const sel = $('post-to');
      if (![...sel.options].some((o) => o.value === seat)) {
        const opt = document.createElement('option');
        opt.value = seat;
        opt.textContent = `@${seat}`;
        sel.appendChild(opt);
      }
      sel.value = seat;
      $('post-text').focus();
    });
  });

  list.querySelectorAll('[data-ack]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = Number(btn.dataset.ack);
      const res = await apiFetch('/api/board/ack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: state.host, id, by: 'owner' }),
      });
      const json = await res.json();
      if (json.ok) {
        showToast(`Acked #${id}`);
        refresh(true);
      } else {
        showToast(`Ack failed: ${json.error}`);
      }
    });
  });

  list.querySelectorAll('[data-relay]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = Number(btn.dataset.relay);
      const msg = msgs.find((x) => x.id === id);
      if (!msg || !window.sidecar?.agent) return;
      try {
        await window.sidecar.agent.sendMessage(
          `[Hive Bridge #${msg.id} | host=${state.host} | topic=${msg.topic} | ${msg.from} -> ${msg.to}]\n${msg.body}`,
        );
        showToast(`Relayed #${id} to Antigravity chat`);
      } catch (e) {
        showToast(`Relay failed: ${e.message}`);
      }
    });
  });
}

function renderSessions(snap) {
  const errEl = $('sessions-error');
  if (snap.errors?.codex) {
    errEl.textContent = snap.errors.codex;
    errEl.classList.remove('hidden');
  } else {
    errEl.classList.add('hidden');
  }

  const sessions = snap.sessions || [];
  const slots = (snap.slots || []).filter((sl) => sl.runtime === 'codex');
  $('count-sessions').textContent = sessions.length ? `${sessions.length}` : '';

  const q = ($('filter-cwd').value || '').trim().toLowerCase();
  const filteredSlots = q
    ? slots.filter((sl) => `${sl.role || ''} ${sl.cwd || ''} ${sl.threadId || ''}`.toLowerCase().includes(q))
    : slots;
  const filtered = q
    ? sessions.filter((s) => `${s.name || ''} ${s.cwd || ''} ${s.id || ''}`.toLowerCase().includes(q))
    : sessions;

  const list = $('sessions-list');
  if (!filteredSlots.length && !filtered.length) {
    list.innerHTML = '<div class="empty">No pinned slots or Codex sessions found on this host.</div>';
    return;
  }

  const slotsHtml = filteredSlots.length
    ? `<div class="meta" style="margin:2px 0;">Pinned Slots (${filteredSlots.length}) — reused automatically by <code>hive-bridge run</code></div>` +
      filteredSlots
        .map(
          (sl) => `
          <article class="card unread">
            <div class="card-head">
              <div>
                <span class="pill active">slot</span>
                <span class="pill">${esc(sl.runtime)}:${esc(sl.role)}</span>
                <strong>${esc(sl.name || sl.role)}</strong>
              </div>
              <span class="meta">${sl.turns || 0} turns · ${fmtTime(sl.updatedAt)}</span>
            </div>
            <div class="meta">${esc(sl.threadId)}</div>
            <div class="meta">${esc(sl.cwd)}</div>
            <div class="card-actions">
              <button type="button" class="btn sm" data-copy="${esc(sl.threadId)}">Copy UUID</button>
              <button type="button" class="btn sm" data-diff="${esc(sl.cwd)}">View Diff</button>
              <button type="button" class="btn sm" data-rotate-cwd="${esc(sl.cwd)}" data-rotate-role="${esc(sl.role)}" data-rotate-rt="${esc(sl.runtime)}">Rotate</button>
            </div>
          </article>`,
        )
        .join('')
    : '';

  const sessionsHtml = filtered.length
    ? `<div class="meta" style="margin:6px 0 2px;">Codex Daemon Sessions (${filtered.length})</div>` +
      filtered
        .map((s) => {
          const statusType = typeof s.status === 'object' && s.status ? s.status.type : String(s.status || 'idle');
          const isActive = statusType === 'active';
          const turnId = s.activeTurn?.turnId || '';
          const lifeBadge = s.lifecycle
            ? `<span class="pill ${s.lifecycle === 'slot' ? 'active' : 'warn'}">${esc(s.lifecycle)}</span>`
            : '';
          return `
            <article class="card">
              <div class="card-head">
                <div>
                  <strong>${esc(s.name || '(unnamed)')}</strong>
                  ${lifeBadge}
                </div>
                <span class="pill ${isActive ? 'active' : ''}">${esc(statusType)}</span>
              </div>
              <div class="meta">${esc(s.id)}</div>
              ${s.cwd ? `<div class="meta">${esc(s.cwd)}</div>` : ''}
              <div class="card-actions">
                <button type="button" class="btn sm primary" data-open-chat-rt="codex" data-open-chat-id="${esc(s.id)}" data-open-chat-cwd="${esc(s.cwd || '')}" data-open-chat-name="${esc(s.name || '')}" data-open-chat-out="${esc(s.activeTurn?.out || '')}">Chat / Watch</button>
                <button type="button" class="btn sm" data-copy="${esc(s.id)}">Copy UUID</button>
                ${s.cwd ? `<button type="button" class="btn sm" data-diff="${esc(s.cwd)}">View Diff</button>` : ''}
                ${isActive && turnId ? `<button type="button" class="btn sm danger" data-int-thread="${esc(s.id)}" data-int-turn="${esc(turnId)}">Interrupt</button>` : ''}
              </div>
              ${isActive && turnId ? `
                <div class="steer-box">
                  <input type="text" class="input sm grow" data-steer-input="${esc(s.id)}" placeholder="Mid-turn steer instruction…">
                  <button type="button" class="btn sm primary" data-steer-thread="${esc(s.id)}" data-steer-turn="${esc(turnId)}">Steer</button>
                </div>` : ''}
            </article>`;
        })
        .join('')
    : '';

  list.innerHTML = slotsHtml + sessionsHtml;

  list.querySelectorAll('[data-rotate-role]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const cwd = btn.dataset.rotateCwd;
      const role = btn.dataset.rotateRole;
      const runtime = btn.dataset.rotateRt;
      btn.disabled = true;
      btn.textContent = 'Rotating…';
      try {
        const res = await apiFetch('/api/slot/rotate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ host: state.host, runtime, cwd, role, deleteOld: true }),
        });
        const json = await res.json();
        if (json.ok) {
          showToast(`Rotated ${runtime}:${role}`);
          refresh(true);
        } else {
          showToast(`Rotate failed: ${json.error}`);
        }
      } finally {
        btn.disabled = false;
        btn.textContent = 'Rotate';
      }
    });
  });

  list.querySelectorAll('[data-open-chat-id]').forEach((btn) => {
    btn.addEventListener('click', () => {
      openLiveTarget({
        runtime: btn.dataset.openChatRt,
        threadId: btn.dataset.openChatId,
        cwd: btn.dataset.openChatCwd,
        name: btn.dataset.openChatName,
        out: btn.dataset.openChatOut || '',
      });
    });
  });

  list.querySelectorAll('[data-copy]').forEach((btn) => {
    btn.addEventListener('click', () => {
      navigator.clipboard?.writeText(btn.dataset.copy);
      showToast('Copied thread UUID');
    });
  });

  list.querySelectorAll('[data-diff]').forEach((btn) => {
    btn.addEventListener('click', () => {
      $('diff-cwd').value = btn.dataset.diff;
      switchTab('evidence');
      loadGitDiff();
    });
  });

  list.querySelectorAll('[data-steer-thread]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const threadId = btn.dataset.steerThread;
      const turnId = btn.dataset.steerTurn;
      const input = list.querySelector(`[data-steer-input="${CSS.escape(threadId)}"]`);
      const text = (input?.value || '').trim();
      if (!text) return;
      const res = await apiFetch('/api/codex/steer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: state.host, threadId, turnId, text }),
      });
      const json = await res.json();
      if (json.ok) {
        if (input) input.value = '';
        showToast('Steered running Codex turn');
        refresh(true);
      } else {
        showToast(`Steer failed: ${json.error}`);
      }
    });
  });

  list.querySelectorAll('[data-int-thread]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const threadId = btn.dataset.intThread;
      const turnId = btn.dataset.intTurn;
      const res = await apiFetch('/api/codex/interrupt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: state.host, threadId, turnId }),
      });
      const json = await res.json();
      if (json.ok) {
        showToast('Interrupt sent');
        refresh(true);
      } else {
        showToast(`Interrupt failed: ${json.error}`);
      }
    });
  });
}

function renderClaude(snap) {
  const errEl = $('claude-error');
  if (snap.errors?.claude) {
    errEl.textContent = snap.errors.claude;
    errEl.classList.remove('hidden');
  } else {
    errEl.classList.add('hidden');
  }

  const all = snap.claudeSessions || [];
  const liveCount = all.filter((s) => s.alive).length;
  $('count-claude').textContent = liveCount ? `${liveCount}` : (all.length ? `${all.length}` : '');

  const q = ($('filter-claude-cwd').value || '').trim().toLowerCase();
  const liveOnly = $('filter-claude-live').checked;

  const claudeSlots = (snap.slots || []).filter((sl) => {
    if (sl.runtime !== 'claude') return false;
    if (!q) return true;
    return `${sl.role || ''} ${sl.cwd || ''} ${sl.threadId || ''}`.toLowerCase().includes(q);
  });

  const filtered = all.filter((s) => {
    if (liveOnly && !s.alive) return false;
    if (!q) return true;
    return `${s.name || ''} ${s.cwd || ''} ${s.id || ''}`.toLowerCase().includes(q);
  });

  const list = $('claude-list');
  if (!claudeSlots.length && !filtered.length) {
    list.innerHTML = `<div class="empty">${liveOnly ? 'No live Claude sessions running right now. Uncheck "Live only" to view recent stopped sessions.' : 'No Claude sessions found on this host.'}</div>`;
    return;
  }

  const slotsHtml = claudeSlots.length
    ? `<div class="meta" style="margin:2px 0;">Pinned Claude Slots (${claudeSlots.length})</div>` +
      claudeSlots
        .map(
          (sl) => `
          <article class="card unread">
            <div class="card-head">
              <div>
                <span class="pill active">slot</span>
                <span class="pill">claude:${esc(sl.role)}</span>
                <strong>${esc(sl.name || sl.role)}</strong>
              </div>
              <span class="meta">${sl.turns || 0} turns · ${fmtTime(sl.updatedAt)}</span>
            </div>
            <div class="meta">${esc(sl.threadId)}</div>
            <div class="meta">${esc(sl.cwd)}</div>
            <div class="card-actions">
              <button type="button" class="btn sm" data-copy="${esc(sl.threadId)}">Copy UUID</button>
              <button type="button" class="btn sm" data-diff="${esc(sl.cwd)}">View Diff</button>
              <button type="button" class="btn sm" data-crotate-cwd="${esc(sl.cwd)}" data-crotate-role="${esc(sl.role)}">Rotate</button>
            </div>
          </article>`,
        )
        .join('')
    : '';

  const sessionsHtml = filtered.length
    ? `<div class="meta" style="margin:6px 0 2px;">Remote Claude Sessions (${filtered.length})</div>` +
      filtered
        .map((s) => {
          const st = String(s.status || (s.alive ? 'live' : 'stopped'));
          return `
            <article class="card">
              <div class="card-head">
                <div>
                  <strong>${esc(s.name || '(unnamed)')}</strong>
                  <span class="pill">${esc(s.entrypoint || 'cli')}</span>
                  ${s.slot ? `<span class="pill active">slot:${esc(s.slot.role)}</span>` : ''}
                </div>
                <span class="pill ${s.alive ? 'active' : ''}">${esc(st)}${s.pid && s.alive ? ` · pid ${s.pid}` : ''}</span>
              </div>
              <div class="meta">${esc(s.id)}</div>
              ${s.cwd ? `<div class="meta">${esc(s.cwd)}</div>` : ''}
              <div class="card-actions">
                <button type="button" class="btn sm primary" data-open-chat-rt="claude" data-open-chat-id="${esc(s.id)}" data-open-chat-cwd="${esc(s.cwd || '')}" data-open-chat-name="${esc(s.name || '')}">Chat Live</button>
                <button type="button" class="btn sm" data-copy="${esc(s.id)}">Copy UUID</button>
                ${s.cwd ? `<button type="button" class="btn sm" data-diff="${esc(s.cwd)}">View Diff</button>` : ''}
                <button type="button" class="btn sm" data-msg-claude="${esc(s.name || s.id.slice(0, 8))}">Board</button>
              </div>
            </article>`;
        })
        .join('')
    : '';

  list.innerHTML = slotsHtml + sessionsHtml;

  list.querySelectorAll('[data-open-chat-id]').forEach((btn) => {
    btn.addEventListener('click', () => {
      openLiveTarget({
        runtime: btn.dataset.openChatRt,
        threadId: btn.dataset.openChatId,
        cwd: btn.dataset.openChatCwd,
        name: btn.dataset.openChatName,
        out: btn.dataset.openChatOut || '',
      });
    });
  });

  list.querySelectorAll('[data-copy]').forEach((btn) => {
    btn.addEventListener('click', () => {
      navigator.clipboard?.writeText(btn.dataset.copy);
      showToast('Copied Claude session UUID');
    });
  });

  list.querySelectorAll('[data-diff]').forEach((btn) => {
    btn.addEventListener('click', () => {
      $('diff-cwd').value = btn.dataset.diff;
      switchTab('evidence');
      loadGitDiff();
    });
  });

  list.querySelectorAll('[data-msg-claude]').forEach((btn) => {
    btn.addEventListener('click', () => {
      $('post-to').value = 'claude';
      $('post-topic').value = btn.dataset.msgClaude || 'claude';
      switchTab('board');
      $('post-text').focus();
    });
  });

  list.querySelectorAll('[data-crotate-role]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const cwd = btn.dataset.crotateCwd;
      const role = btn.dataset.crotateRole;
      btn.disabled = true;
      btn.textContent = 'Rotating…';
      try {
        const res = await apiFetch('/api/slot/rotate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ host: state.host, runtime: 'claude', cwd, role }),
        });
        const json = await res.json();
        if (json.ok) {
          showToast(`Rotated claude:${role}`);
          refresh(true);
        } else {
          showToast(`Rotate failed: ${json.error}`);
        }
      } finally {
        btn.disabled = false;
        btn.textContent = 'Rotate';
      }
    });
  });
}

function renderRuns(snap) {
  const runs = snap.runs || [];
  $('count-runs').textContent = runs.length ? `${runs.length}` : '';
  const list = $('runs-list');
  if (!runs.length) {
    list.innerHTML = '<div class="empty">No recorded Hive Bridge runs yet.</div>';
    return;
  }

  list.innerHTML = runs
    .map((r) => {
      const st = r.status || 'unknown';
      const pillClass = st === 'running' ? 'active' : st === 'failed' || st === 'timeout' ? 'failed' : '';
      const declined = r.approvalsDeclinedCount || 0;
      return `
        <article class="card">
          <div class="card-head">
            <div>
              <span class="pill">${esc(r.runtime || 'codex')}</span>
              <strong>${esc(r.name || (r.threadId || '').slice(0, 12) || 'run')}</strong>
              <span class="pill ${pillClass}">${esc(st)}</span>
              ${declined ? `<span class="pill warn">${declined} declined</span>` : ''}
            </div>
            <span class="meta">${fmtTime(r.updatedAt || r.startedAt)}</span>
          </div>
          ${r.preview ? `<div class="body-text">${esc(r.preview)}</div>` : ''}
          <div class="meta">${esc(r.out)}</div>
          <div class="card-actions">
            <button type="button" class="btn sm primary" data-watch-run="${esc(r.out)}" data-watch-rt="${esc(r.runtime || 'codex')}" data-watch-id="${esc(r.threadId || '')}" data-watch-cwd="${esc(r.cwd || '')}" data-watch-name="${esc(r.name || '')}">Open Live View</button>
            <button type="button" class="btn sm" data-inspect="${esc(r.out)}">Inspect summary.json</button>
          </div>
        </article>`;
    })
    .join('');

  list.querySelectorAll('[data-watch-run]').forEach((btn) => {
    btn.addEventListener('click', () => {
      openLiveTarget({
        out: btn.dataset.watchRun,
        runtime: btn.dataset.watchRt,
        threadId: btn.dataset.watchId,
        cwd: btn.dataset.watchCwd,
        name: btn.dataset.watchName,
      });
    });
  });

  list.querySelectorAll('[data-inspect]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const out = btn.dataset.inspect;
      const res = await apiFetch(`/api/evidence?host=${encodeURIComponent(state.host)}&out=${encodeURIComponent(out)}`);
      const json = await res.json();
      const box = $('evidence-detail');
      box.classList.remove('hidden');
      if (!json.ok) {
        box.innerHTML = `<div class="alert">${esc(json.error)}</div>`;
        return;
      }
      const s = json.summary || {};
      const cmds = (s.commands || [])
        .map((c) => `$ ${c.command}  [exit=${c.exitCode}, ${c.durationMs || 0}ms]\n${(c.output || '').slice(-600)}`)
        .join('\n\n');
      box.innerHTML = `
        <div class="card-head">
          <strong>Evidence: ${esc(s.status || 'running')}</strong>
          <button type="button" class="btn sm" id="close-ev">Close</button>
        </div>
        <div class="meta">${esc(json.out)}</div>
        ${s.finalMessage ? `<div class="body-text">${esc(s.finalMessage)}</div>` : ''}
        ${(s.approvalsDeclined || []).length ? `<div class="alert">Approvals declined:\n${esc(JSON.stringify(s.approvalsDeclined, null, 2))}</div>` : ''}
        ${cmds ? `<pre class="code-box" style="margin:6px 0 0">${esc(cmds)}</pre>` : ''}
      `;
      $('close-ev')?.addEventListener('click', () => box.classList.add('hidden'));
    });
  });
}

async function loadGitDiff() {
  const cwd = $('diff-cwd').value.trim();
  if (!cwd) return;
  const box = $('diff-viewer');
  box.classList.remove('hidden');
  box.innerHTML = '<div class="meta">Loading remote git diff…</div>';
  const res = await apiFetch(`/api/diff?host=${encodeURIComponent(state.host)}&cwd=${encodeURIComponent(cwd)}`);
  const json = await res.json();
  if (!json.ok || json.isGit === false) {
    box.innerHTML = `<div class="alert">${esc(json.error || 'Not a git repository')}</div>`;
    return;
  }
  box.innerHTML = `
    <div class="card-head">
      <strong>git @ ${esc(json.branch)}</strong>
      <button type="button" class="btn sm" id="close-diff">Close</button>
    </div>
    <pre class="code-box" style="margin:6px 0 0">${esc((json.status || '(clean)') + '\n' + (json.stat || '') + '\n' + (json.diff || ''))}</pre>
  `;
  $('close-diff')?.addEventListener('click', () => box.classList.add('hidden'));
}

async function refresh(force = false) {
  try {
    const qs = new URLSearchParams();
    if (state.host) qs.set('host', state.host);
    if (force) qs.set('force', '1');
    const res = await apiFetch(`/api/snapshot?${qs.toString()}`);
    const snap = await res.json();
    state.snapshot = snap;
    renderHosts(snap);

    if (!snap.configured) {
      $('empty-config').classList.remove('hidden');
      $('content').classList.add('hidden');
      setStatus('', 'Not configured');
      return;
    }

    $('empty-config').classList.add('hidden');
    $('content').classList.remove('hidden');
    setStatus(snap.online ? 'ok' : 'err', snap.online ? `Live · ${snap.host}` : 'Offline');
    renderLiveRunsDropdown(snap);
    renderBoard(snap);
    renderSessions(snap);
    renderClaude(snap);
    renderRuns(snap);
  } catch (err) {
    setStatus('err', 'Error');
    showToast(`Refresh error: ${err.message}`);
  }
}

document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

$('host-select').addEventListener('change', (e) => {
  state.host = e.target.value;
  stopLivePoll();
  state.live.out = '';
  state.live.threadId = '';
  refresh(true);
});

$('btn-refresh').addEventListener('click', () => refresh(true));
$('filter-topic').addEventListener('change', () => state.snapshot && renderBoard(state.snapshot));
$('filter-to').addEventListener('change', () => state.snapshot && renderBoard(state.snapshot));
$('filter-unread').addEventListener('change', () => state.snapshot && renderBoard(state.snapshot));
$('filter-cwd').addEventListener('input', () => state.snapshot && renderSessions(state.snapshot));
$('filter-claude-cwd').addEventListener('input', () => state.snapshot && renderClaude(state.snapshot));
$('filter-claude-live').addEventListener('change', () => state.snapshot && renderClaude(state.snapshot));
$('btn-diff').addEventListener('click', loadGitDiff);

$('btn-doctor').addEventListener('click', async () => {
  const out = $('doctor-out');
  out.classList.remove('hidden');
  out.textContent = 'Running doctor…';
  const res = await apiFetch(`/api/doctor?host=${encodeURIComponent(state.host)}`);
  const json = await res.json();
  out.textContent = JSON.stringify(json, null, 2);
});

async function runGc(apply) {
  const box = $('gc-box');
  box.classList.remove('hidden');
  box.innerHTML = `<div class="meta">${apply ? 'Cleaning candidate sessions…' : 'Scanning sessions for GC…'}</div>`;
  const res = await apiFetch('/api/gc', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host: state.host, forceEphemeral: true, apply }),
  });
  const json = await res.json();
  if (!json.ok) {
    box.innerHTML = `<div class="alert">${esc(json.error)}</div>`;
    return;
  }
  const cands = json.candidates || [];
  if (apply) {
    showToast(`Deleted ${(json.deleted || []).length} session(s)`);
    refresh(true);
  }
  box.innerHTML = `
    <div class="card-head">
      <strong>GC ${apply ? 'Result' : 'Preview'}: ${cands.length} candidate(s) (${json.protectedCount || 0} protected)</strong>
      <button type="button" class="btn sm" id="close-gc">Close</button>
    </div>
    ${cands.length
      ? `<div class="meta" style="margin:4px 0;">${cands.map((c) => `${esc(c.name || c.id.slice(0, 12))} [${esc(c.reason)}]`).join('<br>')}</div>`
      : '<div class="meta">No rotated or ephemeral sessions to clean. Pinned slots are never touched.</div>'}
    ${!apply && cands.length ? `<div class="card-actions"><button type="button" class="btn sm danger" id="apply-gc">Delete ${cands.length} candidate(s)</button></div>` : ''}
  `;
  $('close-gc')?.addEventListener('click', () => box.classList.add('hidden'));
  $('apply-gc')?.addEventListener('click', () => runGc(true));
}

$('btn-gc').addEventListener('click', () => runGc(false));

$('board-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('post-text').value.trim();
  if (!text) return;
  const res = await apiFetch('/api/board/post', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      host: state.host,
      from: 'owner',
      to: $('post-to').value,
      topic: $('post-topic').value.trim() || 'general',
      text,
    }),
  });
  const json = await res.json();
  if (json.ok) {
    $('post-text').value = '';
    showToast(`Posted #${json.message?.id || ''}`);
    refresh(true);
  } else {
    showToast(`Post failed: ${json.error}`);
  }
});

refresh(true);
state.pollTimer = setInterval(() => refresh(false), 6000);

// ---------------------------------------------------------------------------
// Live Stream Viewer (Claude & Codex real-time output inside Antigravity)
// ---------------------------------------------------------------------------

function stopLivePoll() {
  if (state.live.timer) {
    clearInterval(state.live.timer);
    state.live.timer = null;
  }
}

function renderLiveRunsDropdown(snap) {
  const runs = snap.runs || [];
  const runningCount = runs.filter((r) => r.status === 'running').length;
  $('count-live').textContent = runningCount ? `${runningCount}` : '';

  const sel = $('live-run-select');
  const prev = state.live.out || sel.value;
  sel.innerHTML =
    '<option value="">Select a Codex or Claude run…</option>' +
    runs
      .map((r) => {
        const dot = r.status === 'running' ? '● ' : '';
        const label = `${dot}[${r.runtime || 'codex'}] ${r.name || (r.threadId || '').slice(0, 10) || 'run'} (${r.status || '?'})`;
        return `<option value="${esc(r.out)}">${esc(label)}</option>`;
      })
      .join('');
  if (prev && runs.some((r) => r.out === prev)) {
    sel.value = prev;
  } else if (!state.live.out && !state.live.threadId && runs.length) {
    // Auto-select the latest run on first open so Live tab is immediately useful!
    const top = runs[0];
    sel.value = top.out;
    openLiveTarget({
      out: top.out,
      runtime: top.runtime || 'codex',
      threadId: top.threadId || '',
      cwd: top.cwd || '',
      name: top.name || '',
      switchToTab: false,
    });
  }
}

function consumeStreamEvents(events) {
  const L = state.live;
  for (const ev of events || []) {
    // --- Codex app-server events ---
    if (ev.method === 'item/agentMessage/delta') {
      const delta = ev.params?.delta || '';
      if (delta) L.streamText += delta;
    } else if (ev.method === 'item/completed') {
      const item = ev.params?.item || {};
      if (item.type === 'commandExecution') {
        L.tools.push({
          title: `Bash · ${item.command || ''} [exit=${item.exitCode ?? '?'}]`,
          body: (item.aggregatedOutput || '').slice(-3000),
        });
      } else if (item.type === 'fileChange') {
        const files = (item.changes || []).map((c) => `${c.kind || 'edit'} ${c.path || ''}`).join('\n');
        L.tools.push({ title: `FileChange (${(item.changes || []).length} files)`, body: files });
      } else if (item.type === 'agentMessage' && item.text && !L.streamText) {
        L.streamText = item.text;
      }
    }
    // --- Claude CLI stream-json events ---
    else if (ev.type === 'stream_event') {
      const inner = ev.event || {};
      if (inner.type === 'content_block_delta') {
        const d = inner.delta || {};
        if (d.type === 'text_delta' && d.text) {
          L.streamText += d.text;
        }
      }
    } else if (ev.type === 'assistant') {
      const blocks = ev.message?.content || [];
      for (const b of blocks) {
        if (!b || typeof b !== 'object') continue;
        if (b.type === 'tool_use') {
          const inp = b.input ? JSON.stringify(b.input, null, 2) : '';
          const preview = b.input?.command || b.input?.file_path || b.input?.pattern || '';
          L.tools.push({
            title: `Tool · ${b.name || 'tool'}${preview ? ' · ' + String(preview).slice(0, 60) : ''}`,
            body: inp.slice(0, 3000),
          });
        } else if (b.type === 'text' && b.text && !L.streamText) {
          L.streamText = b.text;
        }
      }
    } else if (ev.type === 'result') {
      if (typeof ev.result === 'string' && ev.result && !L.streamText) {
        L.streamText = ev.result;
      }
    }
  }
}

function renderLiveView() {
  const L = state.live;
  const badge = $('live-badge');
  badge.textContent = L.status || 'idle';
  badge.className = 'pill ' + (L.status === 'running' ? 'active' : L.status === 'failed' || L.status === 'timeout' ? 'failed' : '');

  const metaParts = [];
  if (L.runtime) metaParts.push(L.runtime);
  if (state.host) metaParts.push(`host:${state.host}`);
  if (L.name) metaParts.push(L.name);
  if (L.threadId) metaParts.push(L.threadId.slice(0, 12));
  $('live-meta').textContent = metaParts.join(' · ');

  $('live-target-label').textContent = L.threadId
    ? `Target: ${L.runtime || 'codex'} · ${L.name || L.threadId.slice(0, 12)}`
    : L.cwd
      ? `New ${L.runtime || 'codex'} @ ${L.cwd}`
      : 'Select a session in Codex / Claude tab to chat directly';

  $('live-btn-diff').classList.toggle('hidden', !L.cwd);
  $('live-btn-relay').classList.toggle('hidden', !L.streamText && !L.summary?.finalMessage);
  $('live-btn-interrupt').classList.toggle('hidden', !(L.runtime === 'codex' && L.status === 'running' && L.threadId && L.turnId));
  $('live-send-btn').textContent = L.runtime === 'codex' && L.status === 'running' ? 'Steer' : 'Send';

  const feed = $('live-feed');
  if (!L.out && !L.prompt && !L.streamText) {
    feed.innerHTML = `<div class="empty">${
      L.threadId
        ? `Ready to chat with <strong>${esc(L.runtime)}</strong> (${esc(L.name || L.threadId.slice(0, 12))}). Type a message below to run and stream live here.`
        : 'Pick any run above, or click <strong>Chat / Watch</strong> on a Codex or Claude session to stream its real-time output right here.'
    }</div>`;
    return;
  }

  const pieces = [];
  if (L.prompt) {
    pieces.push(`<div class="bubble-prompt"><div class="meta" style="margin-bottom:4px;">Prompt</div>${esc(L.prompt)}</div>`);
  }
  for (const t of L.tools) {
    pieces.push(
      `<details class="tool-item"><summary>${esc(t.title)}</summary>${t.body ? `<pre>${esc(t.body)}</pre>` : ''}</details>`,
    );
  }
  const textOut = L.streamText || L.summary?.finalMessage || (L.status === 'running' ? 'Thinking / streaming…' : '');
  if (textOut) {
    pieces.push(`<div class="bubble-assistant"><div class="meta" style="margin-bottom:4px;">${esc(L.runtime || 'agent')} · ${esc(L.status)}</div>${esc(textOut)}</div>`);
  }
  if (L.summary?.error) {
    pieces.push(`<div class="alert" style="margin:0;">${esc(L.summary.error)}</div>`);
  }
  if (L.summary && L.status !== 'running') {
    const dur = L.summary.durationMs ? `${(L.summary.durationMs / 1000).toFixed(1)}s` : '';
    pieces.push(`<div class="meta">Finished (${esc(L.status)}) ${dur} · ${esc(L.out)}</div>`);
  }

  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 60;
  feed.innerHTML = pieces.join('');
  if (nearBottom) feed.scrollTop = feed.scrollHeight;
}

async function pollLiveOnce() {
  const L = state.live;
  if (!L.out) return;
  const res = await apiFetch(
    `/api/stream?host=${encodeURIComponent(state.host)}&out=${encodeURIComponent(L.out)}&offset=${L.offset}`,
  );
  const json = await res.json();
  if (!json.ok) {
    showToast(`Stream error: ${json.error}`);
    stopLivePoll();
    return;
  }
  L.offset = json.offset ?? L.offset;
  L.status = json.status || L.status;
  if (json.prompt) L.prompt = json.prompt;
  if (json.turn) {
    L.runtime = json.turn.runtime || L.runtime;
    L.threadId = json.turn.threadId || L.threadId;
    L.turnId = json.turn.turnId || L.turnId;
    L.cwd = json.turn.cwd || L.cwd;
    L.name = json.turn.name || L.name;
  }
  if (json.summary) {
    L.summary = json.summary;
    if (!L.streamText && json.summary.finalMessage) L.streamText = json.summary.finalMessage;
  }
  consumeStreamEvents(json.events || []);
  renderLiveView();

  if (L.status && L.status !== 'running' && L.status !== 'starting' && !json.hasMore) {
    stopLivePoll();
    refresh(true);
  }
}

function openLiveTarget({ out = '', runtime = 'codex', threadId = '', cwd = '', name = '', switchToTab = true }) {
  stopLivePoll();
  Object.assign(state.live, {
    out,
    offset: 0,
    runtime,
    threadId,
    turnId: '',
    cwd,
    name,
    status: out ? 'loading' : 'ready',
    prompt: '',
    streamText: '',
    tools: [],
    summary: null,
  });
  if (out) $('live-run-select').value = out;
  if (switchToTab) switchTab('live');
  renderLiveView();
  if (out) {
    pollLiveOnce();
    state.live.timer = setInterval(pollLiveOnce, 1200);
  } else {
    $('live-input').focus();
  }
}

$('live-run-select').addEventListener('change', (e) => {
  const out = e.target.value;
  if (!out) return;
  const r = (state.snapshot?.runs || []).find((x) => x.out === out) || {};
  openLiveTarget({
    out,
    runtime: r.runtime || 'codex',
    threadId: r.threadId || '',
    cwd: r.cwd || '',
    name: r.name || '',
  });
});

$('live-btn-diff').addEventListener('click', () => {
  if (!state.live.cwd) return;
  $('diff-cwd').value = state.live.cwd;
  switchTab('evidence');
  loadGitDiff();
});

$('live-btn-relay').addEventListener('click', async () => {
  const text = state.live.streamText || state.live.summary?.finalMessage || '';
  if (!text || !window.sidecar?.agent) return;
  try {
    await window.sidecar.agent.sendMessage(
      `[Hive Bridge Live | host=${state.host} | ${state.live.runtime} ${state.live.name || state.live.threadId}]\n${text}`,
    );
    showToast('Relayed stream output to Antigravity chat');
  } catch (e) {
    showToast(`Relay failed: ${e.message}`);
  }
});

$('live-btn-interrupt').addEventListener('click', async () => {
  const L = state.live;
  if (!L.threadId || !L.turnId) return;
  const res = await apiFetch('/api/codex/interrupt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host: state.host, threadId: L.threadId, turnId: L.turnId }),
  });
  const json = await res.json();
  showToast(json.ok ? 'Interrupt sent' : `Interrupt failed: ${json.error}`);
});

$('live-composer').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('live-input').value.trim();
  if (!text) return;
  const L = state.live;
  if (!L.threadId && !L.cwd) {
    showToast('Select a Codex or Claude session first');
    return;
  }

  // If Codex is mid-turn, steer it instead of starting a new turn!
  if (L.runtime === 'codex' && L.status === 'running' && L.threadId && L.turnId) {
    const res = await apiFetch('/api/codex/steer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host: state.host, threadId: L.threadId, turnId: L.turnId, text }),
    });
    const json = await res.json();
    if (json.ok) {
      $('live-input').value = '';
      showToast('Steered running Codex turn');
    } else {
      showToast(`Steer failed: ${json.error}`);
    }
    return;
  }

  const sendBtn = $('live-send-btn');
  sendBtn.disabled = true;
  sendBtn.textContent = 'Starting…';
  try {
    const res = await apiFetch('/api/chat/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: state.host,
        runtime: L.runtime || 'codex',
        threadId: L.threadId,
        cwd: L.cwd,
        name: L.name,
        text,
      }),
    });
    const json = await res.json();
    if (!json.ok) {
      showToast(`Send failed: ${json.error}`);
      return;
    }
    $('live-input').value = '';
    if (json.out) {
      openLiveTarget({
        out: json.out,
        runtime: json.runtime || L.runtime,
        threadId: json.threadId || L.threadId,
        cwd: L.cwd,
        name: L.name,
      });
    } else {
      showToast(json.note || 'Turn started');
      refresh(true);
    }
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = L.runtime === 'codex' && L.status === 'running' ? 'Steer' : 'Send';
  }
});
