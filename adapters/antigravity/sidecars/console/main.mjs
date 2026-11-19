/**
 * Hive Bridge Console sidecar backend.
 *
 * Runs locally on the user's machine under Antigravity's sidecar lifecycle.
 * Reads remote host state on demand over OpenSSH ControlMaster via the local
 * `hive-bridge` CLI (~/.local/bin/hive-bridge). Zero persistent remote daemons.
 */
import { execFile } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SidecarApp, Response } from 'sidecar_sdk';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH =
  process.env.HIVE_BRIDGE_CONFIG || join(homedir(), '.config', 'hive-bridge', 'config.json');
const BRIDGE_BIN =
  process.env.HIVE_BRIDGE_BIN || join(homedir(), '.local', 'bin', 'hive-bridge');

const STATIC = {
  '/styles.css': ['styles.css', 'text/css; charset=utf-8'],
  '/app.js': ['app.js', 'application/javascript; charset=utf-8'],
};

const CACHE_TTL_MS = 3500;
const snapshotCache = new Map(); // key -> { ts, data, inflight }

function loadConfig() {
  if (!existsSync(CONFIG_PATH)) {
    return { default_host: '', hosts: {} };
  }
  try {
    return JSON.parse(readFileSync(CONFIG_PATH, 'utf-8'));
  } catch (err) {
    return { default_host: '', hosts: {}, error: err.message };
  }
}

function runBridge(args, { timeout = 15000, stdin } = {}) {
  return new Promise((resolve) => {
    const child = execFile(
      BRIDGE_BIN,
      args,
      { timeout, maxBuffer: 4 * 1024 * 1024, encoding: 'utf-8' },
      (error, stdout, stderr) => {
        resolve({
          ok: !error,
          code: error ? error.code ?? 1 : 0,
          stdout: stdout || '',
          stderr: stderr || (error ? error.message : ''),
        });
      },
    );
    if (stdin !== undefined && child.stdin) {
      child.stdin.write(stdin);
      child.stdin.end();
    }
  });
}

function parseJsonLines(text) {
  const out = [];
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    try {
      out.push(JSON.parse(line));
    } catch {
      // ignore non-JSON diagnostic lines
    }
  }
  return out;
}

function parseJsonSingle(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

async function fetchSnapshot(host, cwd) {
  const cfg = loadConfig();
  const hostNames = Object.keys(cfg.hosts || {});
  const resolvedHost = host || cfg.default_host || hostNames[0] || '';
  if (!resolvedHost) {
    return {
      configured: false,
      hosts: hostNames,
      host: '',
      defaultCwd: '',
      sessions: [],
      messages: [],
      runs: [],
      errors: {},
      fetchedAt: Date.now(),
    };
  }

  const profile = (cfg.hosts && cfg.hosts[resolvedHost]) || {};
  const hostArgs = ['--host', resolvedHost];
  const codexArgs = ['codex', ...hostArgs, 'list', '--limit', '30'];
  if (cwd) codexArgs.push('--cwd', cwd);

  const [codexRes, busRes, runsRes, slotsRes, metaRes] = await Promise.all([
    runBridge(codexArgs, { timeout: 12000 }),
    runBridge(['bus', ...hostArgs, 'recent', '--limit', '60'], { timeout: 12000 }),
    runBridge(['bus', ...hostArgs, 'runs', '--limit', '20'], { timeout: 12000 }),
    runBridge(['bus', ...hostArgs, 'slot-list'], { timeout: 12000 }),
    runBridge(['bus', ...hostArgs, 'meta-list'], { timeout: 12000 }),
  ]);

  const errors = {};
  if (!codexRes.ok) errors.codex = (codexRes.stderr || codexRes.stdout).trim();
  if (!busRes.ok) errors.bus = (busRes.stderr || busRes.stdout).trim();
  if (!runsRes.ok) errors.runs = (runsRes.stderr || runsRes.stdout).trim();

  const runs = runsRes.ok ? parseJsonLines(runsRes.stdout) : [];
  const slots = slotsRes.ok ? parseJsonLines(slotsRes.stdout) : [];
  const metas = metaRes.ok ? parseJsonLines(metaRes.stdout) : [];
  const metaByThread = {};
  for (const m of metas) {
    if (m.runtime === 'codex' && m.threadId) metaByThread[m.threadId] = m;
  }
  const slotByThread = {};
  for (const sl of slots) {
    if (sl.runtime === 'codex' && sl.threadId) slotByThread[sl.threadId] = sl;
  }

  // Map active threadId -> latest running turnId from runs index so Sessions tab can steer/interrupt
  const activeTurns = {};
  for (const r of runs) {
    if (r.runtime === 'codex' && r.threadId && r.turnId && r.status === 'running' && !activeTurns[r.threadId]) {
      activeTurns[r.threadId] = { turnId: r.turnId, out: r.out };
    }
  }

  const sessions = (codexRes.ok ? parseJsonLines(codexRes.stdout) : []).map((s) => ({
    ...s,
    activeTurn: activeTurns[s.id] || null,
    slot: slotByThread[s.id] || null,
    lifecycle: metaByThread[s.id]?.kind || (slotByThread[s.id] ? 'slot' : ''),
  }));

  return {
    configured: true,
    hosts: hostNames,
    host: resolvedHost,
    defaultCwd: profile.default_cwd || '',
    online: codexRes.ok || busRes.ok,
    codexOnline: codexRes.ok,
    busOnline: busRes.ok,
    slots,
    sessions,
    messages: busRes.ok ? parseJsonLines(busRes.stdout) : [],
    runs,
    errors,
    fetchedAt: Date.now(),
  };
}

async function getCachedSnapshot(host, cwd, force) {
  const key = `${host || ''}|${cwd || ''}`;
  const entry = snapshotCache.get(key);
  const now = Date.now();
  if (!force && entry && entry.data && now - entry.ts < CACHE_TTL_MS) {
    return entry.data;
  }
  if (entry && entry.inflight) {
    return entry.inflight;
  }
  const inflight = fetchSnapshot(host, cwd)
    .then((data) => {
      snapshotCache.set(key, { ts: Date.now(), data, inflight: null });
      return data;
    })
    .catch((err) => {
      snapshotCache.delete(key);
      throw err;
    });
  snapshotCache.set(key, { ts: entry?.ts || 0, data: entry?.data || null, inflight });
  return inflight;
}

const app = new SidecarApp();

app.page('/', () => readFileSync(join(__dirname, 'index.html'), 'utf-8'));

for (const [route, [file, contentType]] of Object.entries(STATIC)) {
  app.api(
    route,
    () => new Response(readFileSync(join(__dirname, file), 'utf-8'), { contentType }),
    'GET',
  );
}

app.api('/api/config', () => loadConfig(), 'GET');

app.api(
  '/api/snapshot',
  async (data) => {
    const force = data.force === '1' || data.force === 'true';
    return getCachedSnapshot(data.host || '', data.cwd || '', force);
  },
  'GET',
);

app.api(
  '/api/doctor',
  async (data) => {
    const args = ['doctor'];
    if (data.host) args.push('--host', data.host);
    const res = await runBridge(args, { timeout: 15000 });
    return parseJsonSingle(res.stdout) || { ok: res.ok, error: (res.stderr || res.stdout).trim() };
  },
  'GET',
);

app.api(
  '/api/evidence',
  async (data) => {
    if (!data.out) return { ok: false, error: 'missing out directory' };
    const args = ['bus'];
    if (data.host) args.push('--host', data.host);
    args.push('evidence', '--out', data.out);
    const res = await runBridge(args, { timeout: 15000 });
    if (!res.ok) return { ok: false, error: (res.stderr || res.stdout).trim() };
    return { ok: true, ...(parseJsonSingle(res.stdout) || {}) };
  },
  'GET',
);

app.api(
  '/api/diff',
  async (data) => {
    if (!data.cwd) return { ok: false, error: 'missing cwd' };
    const args = ['bus'];
    if (data.host) args.push('--host', data.host);
    args.push('git-diff', '--cwd', data.cwd);
    const res = await runBridge(args, { timeout: 15000 });
    if (!res.ok) return { ok: false, error: (res.stderr || res.stdout).trim() };
    return { ok: true, ...(parseJsonSingle(res.stdout) || {}) };
  },
  'GET',
);

app.api(
  '/api/board/post',
  async (data) => {
    const text = String(data.text || '').trim();
    if (!text) return { ok: false, error: 'message text is required' };
    const args = ['bus'];
    if (data.host) args.push('--host', data.host);
    args.push(
      'post',
      '--from',
      String(data.from || 'owner'),
      '--to',
      String(data.to || 'all'),
      '--topic',
      String(data.topic || 'general').trim() || 'general',
      '--stdin',
    );
    if (data.cwd) args.push('--cwd', String(data.cwd));
    const res = await runBridge(args, { timeout: 12000, stdin: text });
    snapshotCache.clear();
    if (!res.ok) return { ok: false, error: (res.stderr || res.stdout).trim() };
    return { ok: true, message: parseJsonSingle(res.stdout) };
  },
  'POST',
);

app.api(
  '/api/board/ack',
  async (data) => {
    const id = Number(data.id);
    if (!Number.isInteger(id) || id <= 0) return { ok: false, error: 'valid id required' };
    const args = ['bus'];
    if (data.host) args.push('--host', data.host);
    args.push('ack', String(id), '--by', String(data.by || 'owner'));
    const res = await runBridge(args, { timeout: 12000 });
    snapshotCache.clear();
    if (!res.ok) return { ok: false, error: (res.stderr || res.stdout).trim() };
    return { ok: true, result: parseJsonSingle(res.stdout) };
  },
  'POST',
);

app.api(
  '/api/codex/steer',
  async (data) => {
    const { host, threadId, turnId, text } = data;
    if (!threadId || !turnId || !String(text || '').trim()) {
      return { ok: false, error: 'threadId, turnId, and text are required' };
    }
    const args = ['codex'];
    if (host) args.push('--host', host);
    args.push('steer', '--thread', String(threadId), '--turn', String(turnId), '--stdin');
    const res = await runBridge(args, { timeout: 15000, stdin: String(text) });
    snapshotCache.clear();
    if (!res.ok) return { ok: false, error: (res.stderr || res.stdout).trim() };
    return { ok: true, result: parseJsonSingle(res.stdout) };
  },
  'POST',
);

app.api(
  '/api/codex/interrupt',
  async (data) => {
    const { host, threadId, turnId } = data;
    if (!threadId || !turnId) {
      return { ok: false, error: 'threadId and turnId are required' };
    }
    const args = ['codex'];
    if (host) args.push('--host', host);
    args.push('interrupt', '--thread', String(threadId), '--turn', String(turnId));
    const res = await runBridge(args, { timeout: 15000 });
    snapshotCache.clear();
    if (!res.ok) return { ok: false, error: (res.stderr || res.stdout).trim() };
    return { ok: true, result: parseJsonSingle(res.stdout) };
  },
  'POST',
);

app.api(
  '/api/slot/rotate',
  async (data) => {
    const { host, runtime = 'codex', cwd, role, deleteOld, note } = data;
    if (!cwd || !role) return { ok: false, error: 'cwd and role are required' };
    const args = ['rotate'];
    if (host) args.push('--host', host);
    args.push('--runtime', String(runtime), '--cwd', String(cwd), '--role', String(role));
    if (deleteOld) args.push('--delete-old');
    if (note) args.push('--note', String(note));
    const res = await runBridge(args, { timeout: 180000 });
    snapshotCache.clear();
    if (!res.ok) return { ok: false, error: (res.stderr || res.stdout).trim() };
    const lines = parseJsonLines(res.stdout);
    return { ok: true, result: lines[lines.length - 1] || null };
  },
  'POST',
);

app.api(
  '/api/gc',
  async (data) => {
    const args = ['gc'];
    if (data.host) args.push('--host', data.host);
    if (data.days) args.push('--days', String(data.days));
    if (data.includeStale) args.push('--include-stale');
    if (data.forceEphemeral) args.push('--force-ephemeral');
    if (data.apply) args.push('--apply');
    const res = await runBridge(args, { timeout: 30000 });
    if (data.apply) snapshotCache.clear();
    if (!res.ok) return { ok: false, error: (res.stderr || res.stdout).trim() };
    return { ok: true, ...(parseJsonSingle(res.stdout) || {}) };
  },
  'POST',
);

app.run();
