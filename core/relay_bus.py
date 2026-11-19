#!/usr/bin/env python3
"""Stateless SQLite blackboard / inbox + session slot registry + evidence helper for hive-bridge.

Zero third-party dependencies (Python 3 stdlib only). Runs on the remote host
and is invoked identically by local Antigravity (via SSH), remote Claude
(Desktop Remote or CLI), and remote Codex sessions.

Subcommands:
  Board / Inbox:
    post          Post a message / task handoff to the shared board
    inbox         List messages for a recipient (optionally unread only)
    ack           Mark one or more messages as acknowledged / read
    thread        Show all messages in a topic chronologically
    topics        List recent topics with message counts & unread counts
    recent        List recent messages across all topics

  Session Lifecycle (anti-sprawl):
    slot-list     List pinned project/role session slots
    slot-get      Get one slot by (cwd, runtime, role)
    slot-set      Bind or update a slot
    slot-clear    Remove a slot binding (optionally marking old session 'rotated')
    meta-set      Tag a session as slot / ephemeral / rotated with TTL
    meta-list     List session lifecycle tags
    meta-del      Remove a session_meta entry
    handoff-build Build a compact Markdown handoff packet from latest run + git status

  Evidence & Verification:
    runs          List recent Codex / Claude runs recorded under ~/.hive-bridge/runs.jsonl
    evidence      Read turn.json, summary.json, and tail of events.jsonl from a run dir
    git-diff      Inspect git status --short and git diff for a remote directory
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_STATE_DIR = Path(os.environ.get("HIVE_BRIDGE_HOME", Path.home() / ".hive-bridge"))
DEFAULT_DB = Path(os.environ.get("HIVE_BRIDGE_DB", DEFAULT_STATE_DIR / "bus.db"))
RUNS_INDEX = DEFAULT_STATE_DIR / "runs.jsonl"


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def connect_db(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  REAL    NOT NULL,
            sender      TEXT    NOT NULL,
            recipient   TEXT    NOT NULL,
            topic       TEXT    NOT NULL,
            body        TEXT    NOT NULL,
            cwd         TEXT    DEFAULT '',
            artifact    TEXT    DEFAULT '',
            meta_json   TEXT    DEFAULT '{}',
            acked_at    REAL    DEFAULT NULL,
            acked_by    TEXT    DEFAULT NULL
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient, acked_at, id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic, id);")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS slots (
            cwd         TEXT    NOT NULL,
            runtime     TEXT    NOT NULL,
            role        TEXT    NOT NULL,
            thread_id   TEXT    NOT NULL,
            name        TEXT    DEFAULT '',
            turns       INTEGER DEFAULT 0,
            last_out    TEXT    DEFAULT '',
            created_at  REAL    NOT NULL,
            updated_at  REAL    NOT NULL,
            PRIMARY KEY (cwd, runtime, role)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_meta (
            runtime     TEXT    NOT NULL,
            thread_id   TEXT    NOT NULL,
            cwd         TEXT    DEFAULT '',
            role        TEXT    DEFAULT '',
            kind        TEXT    NOT NULL,
            ttl_hours   REAL    DEFAULT 24.0,
            created_at  REAL    NOT NULL,
            updated_at  REAL    NOT NULL,
            PRIMARY KEY (runtime, thread_id)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workers (
            agent       TEXT    PRIMARY KEY,
            runtime     TEXT    DEFAULT '',
            cwd         TEXT    DEFAULT '',
            session_id  TEXT    DEFAULT '',
            status      TEXT    NOT NULL,
            pid         INTEGER DEFAULT 0,
            updated_at  REAL    NOT NULL
        );
        """
    )
    conn.commit()
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except Exception:
        meta = {}
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "from": row["sender"],
        "to": row["recipient"],
        "topic": row["topic"],
        "body": row["body"],
        "cwd": row["cwd"] or "",
        "artifact": row["artifact"] or "",
        "meta": meta,
        "ackedAt": row["acked_at"],
        "ackedBy": row["acked_by"],
    }


def slot_to_dict(row: sqlite3.Row) -> dict:
    return {
        "cwd": row["cwd"],
        "runtime": row["runtime"],
        "role": row["role"],
        "threadId": row["thread_id"],
        "name": row["name"] or "",
        "turns": row["turns"] or 0,
        "lastOut": row["last_out"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def meta_to_dict(row: sqlite3.Row) -> dict:
    return {
        "runtime": row["runtime"],
        "threadId": row["thread_id"],
        "cwd": row["cwd"] or "",
        "role": row["role"] or "",
        "kind": row["kind"],
        "ttlHours": row["ttl_hours"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def read_body(args) -> str:
    if getattr(args, "stdin", False):
        return sys.stdin.read()
    if getattr(args, "body_file", None):
        if args.body_file == "-":
            return sys.stdin.read()
        return Path(args.body_file).read_text(encoding="utf-8")
    if getattr(args, "text", None) is not None:
        return args.text
    sys.exit("provide --text, --body-file, or --stdin")


def norm_cwd(cwd: str) -> str:
    return os.path.abspath(os.path.expanduser(cwd)) if cwd else ""


# ---------------------------------------------------------------------------
# Board / Inbox commands
# ---------------------------------------------------------------------------

def cmd_post(args):
    body = read_body(args).strip()
    if not body:
        sys.exit("message body cannot be empty")
    meta = {}
    if args.meta:
        try:
            meta = json.loads(args.meta)
        except json.JSONDecodeError as e:
            sys.exit(f"invalid --meta JSON: {e}")
    cwd = norm_cwd(args.cwd)
    now = time.time()
    with connect_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages (created_at, sender, recipient, topic, body, cwd, artifact, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now, args.sender, args.recipient, args.topic, body, cwd, args.artifact or "", json.dumps(meta, ensure_ascii=False)),
        )
        msg_id = cur.lastrowid
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
    emit(row_to_dict(row))


def cmd_inbox(args):
    clauses = []
    params = []
    if args.recipient:
        clauses.append("(recipient = ? OR recipient = 'all')")
        params.append(args.recipient)
    if args.topic:
        clauses.append("topic = ?")
        params.append(args.topic)
    if args.unread:
        clauses.append("acked_at IS NULL")
    if args.since_id is not None:
        clauses.append("id > ?")
        params.append(args.since_id)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM messages {where} ORDER BY id DESC LIMIT ?"
    params.append(args.limit)

    with connect_db() as conn:
        rows = list(reversed(conn.execute(sql, params).fetchall()))

    if args.brief:
        if not rows:
            return
        lines = [f"[hive-bridge] {len(rows)} unread message(s) for {args.recipient or 'all'}:"]
        for r in rows:
            preview = " ".join((r["body"] or "").split())[:120]
            lines.append(f"  #{r['id']} [{r['topic']}] {r['sender']} -> {r['recipient']}: {preview}")
        print("\n".join(lines))
        return

    for r in rows:
        emit(row_to_dict(r))


def cmd_ack(args):
    now = time.time()
    by = args.by or ""
    with connect_db() as conn:
        if args.all:
            if not args.recipient:
                sys.exit("--all requires --for <agent>")
            cur = conn.execute(
                "UPDATE messages SET acked_at = ?, acked_by = ? WHERE (recipient = ? OR recipient = 'all') AND acked_at IS NULL",
                (now, by or args.recipient, args.recipient),
            )
            emit({"acked": cur.rowcount, "for": args.recipient})
            return
        if not args.ids:
            sys.exit("provide message id(s) or --all --for <agent>")
        placeholders = ",".join("?" for _ in args.ids)
        cur = conn.execute(
            f"UPDATE messages SET acked_at = ?, acked_by = ? WHERE id IN ({placeholders})",
            [now, by, *args.ids],
        )
        emit({"acked": cur.rowcount, "ids": args.ids})


def cmd_thread(args):
    with connect_db() as conn:
        rows = list(
            reversed(
                conn.execute(
                    "SELECT * FROM messages WHERE topic = ? ORDER BY id DESC LIMIT ?",
                    (args.topic, args.limit),
                ).fetchall()
            )
        )
    for r in rows:
        emit(row_to_dict(r))


def cmd_topics(args):
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT topic,
                   COUNT(*) AS count,
                   SUM(CASE WHEN acked_at IS NULL THEN 1 ELSE 0 END) AS unread,
                   MAX(created_at) AS last_at,
                   MAX(id) AS last_id
            FROM messages
            GROUP BY topic
            ORDER BY last_id DESC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    for r in rows:
        emit(dict(r))


def cmd_recent(args):
    clauses = []
    params = []
    if args.since_id is not None:
        clauses.append("id > ?")
        params.append(args.since_id)
    if args.topic:
        clauses.append("topic = ?")
        params.append(args.topic)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect_db() as conn:
        rows = list(
            reversed(
                conn.execute(
                    f"SELECT * FROM messages {where} ORDER BY id DESC LIMIT ?",
                    [*params, args.limit],
                ).fetchall()
            )
        )
    for r in rows:
        emit(row_to_dict(r))


def _upsert_worker(conn: sqlite3.Connection, agent: str, status: str, *, runtime: str = "", cwd: str = "", session_id: str = "", pid: int = 0):
    conn.execute(
        """
        INSERT INTO workers (agent, runtime, cwd, session_id, status, pid, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent) DO UPDATE SET
            runtime = COALESCE(NULLIF(excluded.runtime, ''), workers.runtime),
            cwd = COALESCE(NULLIF(excluded.cwd, ''), workers.cwd),
            session_id = COALESCE(NULLIF(excluded.session_id, ''), workers.session_id),
            status = excluded.status,
            pid = excluded.pid,
            updated_at = excluded.updated_at
        """,
        (agent, runtime, cwd, session_id, status, pid, time.time()),
    )
    conn.commit()


def cmd_wait(args):
    """Block until an unread message arrives for --for <agent> (zero tokens while waiting).

    Intended to be run as a background Bash command inside an already-open
    Claude Desktop Remote / local Claude / local Codex window so Antigravity
    can wake that live Desktop session automatically by posting to the board.
    """
    agent = args.recipient
    cwd = norm_cwd(args.cwd) if args.cwd else ""
    pid = os.getpid()
    deadline = time.time() + args.timeout
    last_hb = 0.0

    def fetch_unread(conn: sqlite3.Connection):
        clauses = ["acked_at IS NULL"]
        params = []
        if args.include_all:
            clauses.append("(recipient = ? OR recipient = 'all')")
            params.append(agent)
        else:
            clauses.append("recipient = ?")
            params.append(agent)
        if args.topic:
            clauses.append("topic = ?")
            params.append(args.topic)
        if args.since_id is not None:
            clauses.append("id > ?")
            params.append(args.since_id)
        where = "WHERE " + " AND ".join(clauses)
        return conn.execute(f"SELECT * FROM messages {where} ORDER BY id ASC LIMIT ?", [*params, args.limit]).fetchall()

    try:
        while True:
            now = time.time()
            with connect_db() as conn:
                rows = fetch_unread(conn)
                if rows:
                    _upsert_worker(conn, agent, "busy", runtime=args.runtime or "", cwd=cwd, session_id=args.session_id or "", pid=pid)
                    emit({"event": "messages_received", "for": agent, "count": len(rows), "messages": [row_to_dict(r) for r in rows]})
                    return
                if now - last_hb >= 5.0:
                    _upsert_worker(conn, agent, "waiting", runtime=args.runtime or "", cwd=cwd, session_id=args.session_id or "", pid=pid)
                    last_hb = now
            if now >= deadline:
                with connect_db() as conn:
                    _upsert_worker(conn, agent, "idle", runtime=args.runtime or "", cwd=cwd, session_id=args.session_id or "", pid=pid)
                emit({"event": "timeout", "for": agent, "waitedSec": args.timeout})
                return
            time.sleep(min(args.interval, max(0.1, deadline - time.time())))
    except KeyboardInterrupt:
        with connect_db() as conn:
            _upsert_worker(conn, agent, "offline", runtime=args.runtime or "", cwd=cwd, session_id=args.session_id or "", pid=0)
        emit({"event": "interrupted", "for": agent})


def cmd_workers(args):
    """List seated workers and their live waiting/busy status."""
    now = time.time()
    with connect_db() as conn:
        rows = conn.execute("SELECT * FROM workers ORDER BY updated_at DESC").fetchall()
    for r in rows:
        age = now - (r["updated_at"] or 0)
        status = r["status"]
        # Waiting workers refresh heartbeat every 5s; >25s stale means process ended
        if status == "waiting" and age > 25:
            status = "offline"
        emit(
            {
                "agent": r["agent"],
                "runtime": r["runtime"] or "",
                "cwd": r["cwd"] or "",
                "sessionId": r["session_id"] or "",
                "status": status,
                "pid": r["pid"] or 0,
                "updatedAt": r["updated_at"],
                "ageSec": round(age, 1),
            }
        )


# ---------------------------------------------------------------------------
# Session Slot & Lifecycle Meta commands
# ---------------------------------------------------------------------------

def cmd_slot_list(args):
    clauses = []
    params = []
    if args.cwd:
        clauses.append("cwd = ?")
        params.append(norm_cwd(args.cwd))
    if args.runtime:
        clauses.append("runtime = ?")
        params.append(args.runtime)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect_db() as conn:
        rows = conn.execute(f"SELECT * FROM slots {where} ORDER BY updated_at DESC", params).fetchall()
    for r in rows:
        emit(slot_to_dict(r))


def cmd_slot_get(args):
    cwd = norm_cwd(args.cwd)
    with connect_db() as conn:
        row = conn.execute(
            "SELECT * FROM slots WHERE cwd = ? AND runtime = ? AND role = ?",
            (cwd, args.runtime, args.role),
        ).fetchone()
    emit(slot_to_dict(row) if row else {"found": False, "cwd": cwd, "runtime": args.runtime, "role": args.role})


def cmd_slot_set(args):
    cwd = norm_cwd(args.cwd)
    now = time.time()
    with connect_db() as conn:
        existing = conn.execute(
            "SELECT * FROM slots WHERE cwd = ? AND runtime = ? AND role = ?",
            (cwd, args.runtime, args.role),
        ).fetchone()
        old_thread = existing["thread_id"] if existing else None
        if existing and old_thread == args.thread:
            turns = (existing["turns"] or 0) + (1 if args.inc_turns else 0)
            name = args.name if args.name is not None else existing["name"]
            last_out = args.out if args.out is not None else existing["last_out"]
            conn.execute(
                "UPDATE slots SET name = ?, turns = ?, last_out = ?, updated_at = ? WHERE cwd = ? AND runtime = ? AND role = ?",
                (name, turns, last_out, now, cwd, args.runtime, args.role),
            )
        else:
            turns = 1 if args.inc_turns else 0
            conn.execute(
                """
                INSERT INTO slots (cwd, runtime, role, thread_id, name, turns, last_out, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cwd, runtime, role) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    name = excluded.name,
                    turns = excluded.turns,
                    last_out = excluded.last_out,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (cwd, args.runtime, args.role, args.thread, args.name or "", turns, args.out or "", now, now),
            )
            if old_thread and old_thread != args.thread:
                conn.execute(
                    """
                    INSERT INTO session_meta (runtime, thread_id, cwd, role, kind, ttl_hours, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'rotated', 0, ?, ?)
                    ON CONFLICT(runtime, thread_id) DO UPDATE SET kind = 'rotated', updated_at = excluded.updated_at
                    """,
                    (args.runtime, old_thread, cwd, args.role, now, now),
                )
        conn.execute(
            """
            INSERT INTO session_meta (runtime, thread_id, cwd, role, kind, ttl_hours, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'slot', 0, ?, ?)
            ON CONFLICT(runtime, thread_id) DO UPDATE SET
                cwd = excluded.cwd, role = excluded.role, kind = 'slot', updated_at = excluded.updated_at
            """,
            (args.runtime, args.thread, cwd, args.role, now, now),
        )
        row = conn.execute(
            "SELECT * FROM slots WHERE cwd = ? AND runtime = ? AND role = ?",
            (cwd, args.runtime, args.role),
        ).fetchone()
    out = slot_to_dict(row)
    if old_thread and old_thread != args.thread:
        out["previousThreadId"] = old_thread
    emit(out)


def cmd_slot_clear(args):
    cwd = norm_cwd(args.cwd)
    now = time.time()
    with connect_db() as conn:
        row = conn.execute(
            "SELECT * FROM slots WHERE cwd = ? AND runtime = ? AND role = ?",
            (cwd, args.runtime, args.role),
        ).fetchone()
        if not row:
            emit({"cleared": False, "cwd": cwd, "runtime": args.runtime, "role": args.role})
            return
        conn.execute(
            "DELETE FROM slots WHERE cwd = ? AND runtime = ? AND role = ?",
            (cwd, args.runtime, args.role),
        )
        if args.mark_rotated:
            conn.execute(
                """
                INSERT INTO session_meta (runtime, thread_id, cwd, role, kind, ttl_hours, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'rotated', 0, ?, ?)
                ON CONFLICT(runtime, thread_id) DO UPDATE SET kind = 'rotated', updated_at = excluded.updated_at
                """,
                (args.runtime, row["thread_id"], cwd, args.role, now, now),
            )
    emit({"cleared": True, "previous": slot_to_dict(row)})


def cmd_meta_set(args):
    now = time.time()
    cwd = norm_cwd(args.cwd) if args.cwd else ""
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO session_meta (runtime, thread_id, cwd, role, kind, ttl_hours, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(runtime, thread_id) DO UPDATE SET
                cwd = COALESCE(NULLIF(excluded.cwd, ''), session_meta.cwd),
                role = COALESCE(NULLIF(excluded.role, ''), session_meta.role),
                kind = excluded.kind,
                ttl_hours = excluded.ttl_hours,
                updated_at = excluded.updated_at
            """,
            (args.runtime, args.thread, cwd, args.role or "", args.kind, args.ttl_hours, now, now),
        )
        row = conn.execute(
            "SELECT * FROM session_meta WHERE runtime = ? AND thread_id = ?",
            (args.runtime, args.thread),
        ).fetchone()
    emit(meta_to_dict(row))


def cmd_meta_list(args):
    clauses = []
    params = []
    if args.runtime:
        clauses.append("runtime = ?")
        params.append(args.runtime)
    if args.kind:
        clauses.append("kind = ?")
        params.append(args.kind)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect_db() as conn:
        rows = conn.execute(f"SELECT * FROM session_meta {where} ORDER BY updated_at DESC", params).fetchall()
    for r in rows:
        emit(meta_to_dict(r))


def cmd_meta_del(args):
    with connect_db() as conn:
        cur = conn.execute(
            "DELETE FROM session_meta WHERE runtime = ? AND thread_id = ?",
            (args.runtime, args.thread),
        )
    emit({"deleted": cur.rowcount, "runtime": args.runtime, "threadId": args.thread})


def _load_json_safe(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cmd_handoff_build(args):
    cwd = norm_cwd(args.cwd)
    sections = [
        f"# Session Handoff ({args.role or 'worker'} @ {cwd})",
        "",
        "You are taking over a rotated Hive Bridge session slot with a fresh context window.",
        "Review the summary and current workspace state below, then wait for (or execute) the next task.",
    ]
    if args.note:
        sections.extend(["", "## Owner / Coordinator Note", args.note.strip()])

    if args.out:
        summary = _load_json_safe(Path(args.out).expanduser() / "summary.json")
        if summary:
            sections.extend(
                [
                    "",
                    "## Previous Session Last Turn Summary",
                    f"- Status: `{summary.get('status')}`",
                    f"- Evidence dir: `{args.out}`",
                ]
            )
            final_msg = (summary.get("finalMessage") or "").strip()
            if final_msg:
                sections.extend(["", "### Last Final Message", final_msg[:3000]])
            cmds = summary.get("commands") or []
            if cmds:
                cmd_lines = [
                    f"- `{c.get('command')}` (exit={c.get('exitCode')})" for c in cmds[-8:]
                ]
                sections.extend(["", "### Recent Commands", *cmd_lines])

    if cwd and os.path.isdir(cwd):
        rc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True)
        if rc.returncode == 0:
            branch = rc.stdout.strip()
            st = subprocess.run(["git", "status", "--short"], cwd=cwd, capture_output=True, text=True).stdout.strip()
            diff_stat = subprocess.run(["git", "diff", "--stat"], cwd=cwd, capture_output=True, text=True).stdout.strip()
            sections.extend(
                [
                    "",
                    f"## Current Git State (`{branch}`)",
                    "```text",
                    st or "(clean working tree)",
                    "```",
                ]
            )
            if diff_stat:
                sections.extend(["", "```text", diff_stat[:2000], "```"])

    sections.extend(["", "Acknowledge concisely that you have absorbed this handoff and are ready."])
    print("\n".join(sections))


# ---------------------------------------------------------------------------
# Evidence & Git verification
# ---------------------------------------------------------------------------

def cmd_runs(args):
    entries = []
    if RUNS_INDEX.exists():
        for line in RUNS_INDEX.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    by_out = {}
    for e in entries:
        out_dir = e.get("out")
        if out_dir:
            by_out[out_dir] = {**by_out.get(out_dir, {}), **e}
    items = sorted(by_out.values(), key=lambda x: x.get("updatedAt") or x.get("startedAt") or 0, reverse=True)
    if args.cwd:
        target = norm_cwd(args.cwd)
        items = [i for i in items if i.get("cwd") == target]
    items = items[: args.limit]
    for item in items:
        out_path = Path(item["out"])
        turn = _load_json_safe(out_path / "turn.json")
        summary = _load_json_safe(out_path / "summary.json")
        if turn:
            item["threadId"] = item.get("threadId") or turn.get("threadId")
            item["turnId"] = turn.get("turnId")
        if summary:
            item["status"] = summary.get("status") or item.get("status")
            item["durationMs"] = summary.get("durationMs")
            item["commandsCount"] = len(summary.get("commands") or [])
            item["fileChangesCount"] = len(summary.get("fileChanges") or [])
            item["approvalsDeclinedCount"] = len(summary.get("approvalsDeclined") or [])
            msg = summary.get("finalMessage") or ""
            item["preview"] = msg[:200]
        else:
            item["status"] = item.get("status") or "running"
        emit(item)


def cmd_evidence(args):
    out_path = Path(args.out).expanduser().resolve()
    if not out_path.exists():
        sys.exit(f"run directory not found: {out_path}")
    turn = _load_json_safe(out_path / "turn.json")
    summary = _load_json_safe(out_path / "summary.json")
    prompt_file = out_path / "prompt.txt"
    prompt_text = prompt_file.read_text(encoding="utf-8", errors="replace") if prompt_file.exists() else ""
    events_tail = []
    ev_file = out_path / "events.jsonl"
    if ev_file.exists():
        lines = ev_file.read_text(encoding="utf-8", errors="replace").splitlines()[-args.tail :]
        for ln in lines:
            try:
                events_tail.append(json.loads(ln))
            except Exception:
                events_tail.append({"raw": ln})
    emit(
        {
            "out": str(out_path),
            "prompt": prompt_text,
            "turn": turn,
            "summary": summary,
            "eventsTail": events_tail,
        }
    )


def cmd_stream(args):
    """Incrementally read newly appended JSONL lines from <out>/events.jsonl by byte offset."""
    out_path = Path(args.out).expanduser().resolve()
    if not out_path.exists():
        sys.exit(f"run directory not found: {out_path}")
    turn = _load_json_safe(out_path / "turn.json")
    summary = _load_json_safe(out_path / "summary.json")
    prompt_file = out_path / "prompt.txt"
    prompt_text = prompt_file.read_text(encoding="utf-8", errors="replace") if prompt_file.exists() else ""

    ev_file = out_path / "events.jsonl"
    offset = max(0, int(args.offset))
    new_offset = offset
    file_size = 0
    events = []
    if ev_file.exists():
        file_size = ev_file.stat().st_size
        if offset < file_size:
            with open(ev_file, "rb") as f:
                f.seek(offset)
                chunk = f.read(args.max_bytes)
            last_nl = chunk.rfind(b"\n")
            if last_nl != -1:
                complete = chunk[: last_nl + 1]
                new_offset = offset + len(complete)
                for raw in complete.decode("utf-8", errors="replace").splitlines():
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        events.append(json.loads(raw))
                    except Exception:
                        pass

    status = (summary or {}).get("status") or ("running" if turn else "starting")
    emit(
        {
            "out": str(out_path),
            "status": status,
            "offset": new_offset,
            "size": file_size,
            "hasMore": new_offset < file_size,
            "prompt": prompt_text if offset == 0 else None,
            "turn": turn,
            "summary": summary,
            "events": events,
        }
    )


def cmd_git_diff(args):
    cwd = norm_cwd(args.cwd)
    if not os.path.isdir(cwd):
        sys.exit(f"directory not found: {cwd}")

    def run_git(git_args):
        p = subprocess.run(["git", *git_args], cwd=cwd, capture_output=True, text=True, timeout=15)
        return p.returncode, p.stdout, p.stderr

    rc_top, top, err_top = run_git(["rev-parse", "--show-toplevel"])
    if rc_top != 0:
        emit({"isGit": False, "cwd": cwd, "error": err_top.strip()})
        return
    _, branch, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    _, status, _ = run_git(["status", "--short"])
    _, stat, _ = run_git(["diff", "--stat"])
    _, diff, _ = run_git(["diff"])
    truncated = len(diff) > args.max_bytes
    emit(
        {
            "isGit": True,
            "cwd": cwd,
            "root": top.strip(),
            "branch": branch.strip(),
            "status": status,
            "stat": stat,
            "diff": diff[: args.max_bytes],
            "truncated": truncated,
        }
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    # Board / Inbox
    p = sub.add_parser("post")
    p.add_argument("--from", dest="sender", required=True)
    p.add_argument("--to", dest="recipient", default="all")
    p.add_argument("--topic", default="general")
    p.add_argument("--text")
    p.add_argument("--body-file")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--cwd")
    p.add_argument("--artifact")
    p.add_argument("--meta")

    p = sub.add_parser("inbox")
    p.add_argument("--for", dest="recipient")
    p.add_argument("--topic")
    p.add_argument("--unread", action="store_true")
    p.add_argument("--since-id", type=int)
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--brief", action="store_true")

    p = sub.add_parser("ack")
    p.add_argument("ids", nargs="*", type=int)
    p.add_argument("--all", action="store_true")
    p.add_argument("--for", dest="recipient")
    p.add_argument("--by")

    p = sub.add_parser("thread")
    p.add_argument("topic")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("topics")
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("recent")
    p.add_argument("--topic")
    p.add_argument("--since-id", type=int)
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("wait", help="Block until an unread message arrives for --for <agent>")
    p.add_argument("--for", dest="recipient", required=True, help="Worker seat name (e.g. claude, claude-ui)")
    p.add_argument("--topic", help="Optional topic filter")
    p.add_argument("--since-id", type=int)
    p.add_argument("--include-all", action="store_true", help="Also wake on messages addressed to 'all'")
    p.add_argument("--runtime", default="", help="Optional runtime label (claude/codex/antigravity)")
    p.add_argument("--cwd", help="Working directory of this seated worker")
    p.add_argument("--session-id", help="Optional session UUID for display")
    p.add_argument("--interval", type=float, default=1.0, help="Poll interval seconds (default 1.0)")
    p.add_argument("--timeout", type=int, default=600, help="Max seconds to wait before clean timeout (default 600)")
    p.add_argument("--limit", type=int, default=10)

    sub.add_parser("workers", help="List seated workers and their waiting/busy status")

    # Slots & session lifecycle
    p = sub.add_parser("slot-list")
    p.add_argument("--cwd")
    p.add_argument("--runtime", choices=["codex", "claude"])

    p = sub.add_parser("slot-get")
    p.add_argument("--cwd", required=True)
    p.add_argument("--runtime", required=True, choices=["codex", "claude"])
    p.add_argument("--role", required=True)

    p = sub.add_parser("slot-set")
    p.add_argument("--cwd", required=True)
    p.add_argument("--runtime", required=True, choices=["codex", "claude"])
    p.add_argument("--role", required=True)
    p.add_argument("--thread", required=True)
    p.add_argument("--name")
    p.add_argument("--out")
    p.add_argument("--inc-turns", action="store_true")

    p = sub.add_parser("slot-clear")
    p.add_argument("--cwd", required=True)
    p.add_argument("--runtime", required=True, choices=["codex", "claude"])
    p.add_argument("--role", required=True)
    p.add_argument("--mark-rotated", action="store_true")

    p = sub.add_parser("meta-set")
    p.add_argument("--runtime", required=True, choices=["codex", "claude"])
    p.add_argument("--thread", required=True)
    p.add_argument("--kind", required=True, choices=["slot", "ephemeral", "rotated"])
    p.add_argument("--cwd")
    p.add_argument("--role")
    p.add_argument("--ttl-hours", type=float, default=24.0)

    p = sub.add_parser("meta-list")
    p.add_argument("--runtime", choices=["codex", "claude"])
    p.add_argument("--kind", choices=["slot", "ephemeral", "rotated"])

    p = sub.add_parser("meta-del")
    p.add_argument("--runtime", required=True, choices=["codex", "claude"])
    p.add_argument("--thread", required=True)

    p = sub.add_parser("handoff-build")
    p.add_argument("--cwd", required=True)
    p.add_argument("--out")
    p.add_argument("--role")
    p.add_argument("--note")

    # Evidence & git
    p = sub.add_parser("runs")
    p.add_argument("--cwd")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("evidence")
    p.add_argument("--out", required=True)
    p.add_argument("--tail", type=int, default=30)

    p = sub.add_parser("stream", help="Incrementally stream events.jsonl by byte offset")
    p.add_argument("--out", required=True)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--max-bytes", type=int, default=131072)

    p = sub.add_parser("git-diff")
    p.add_argument("--cwd", required=True)
    p.add_argument("--max-bytes", type=int, default=24000)

    args = ap.parse_args()
    fn = globals()[f"cmd_{args.cmd.replace('-', '_')}"]
    fn(args)


if __name__ == "__main__":
    main()
