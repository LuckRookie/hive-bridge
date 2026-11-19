const apiFetch = (url, opts) =>
  window.sidecar && window.sidecar.fetch ? window.sidecar.fetch(url, opts) : fetch(url, opts);

const state = {
  host: '',
  tab: 'board',
  snapshot: null,
  pollTimer: null,
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
  if (!filtered.length) {
    list.innerHTML = '<div class="empty">No blackboard messages match the filter.</div>';
    return;
  }

  list.innerHTML = filtered
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
                <button type="button" class="btn sm" data-copy="${esc(s.id)}">Copy UUID</button>
                ${s.cwd ? `<button type="button" class="btn sm" data-diff="${esc(s.cwd)}">View Diff</button>` : ''}
                <button type="button" class="btn sm primary" data-msg-claude="${esc(s.name || s.id.slice(0, 8))}">Message on Board</button>
              </div>
            </article>`;
        })
        .join('')
    : '';

  list.innerHTML = slotsHtml + sessionsHtml;

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
            <button type="button" class="btn sm" data-inspect="${esc(r.out)}">Inspect summary.json</button>
          </div>
        </article>`;
    })
    .join('');

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
