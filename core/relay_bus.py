#!/usr/bin/env python3
"""Stateless SQLite blackboard / inbox + evidence helper for hive-bridge.

Zero third-party dependencies (Python 3 stdlib only). Runs on the remote host
and is invoked identically by local Antigravity (via SSH), remote Claude
(Desktop Remote or CLI), and remote Codex sessions.

Subcommands:
  post       Post a message / task handoff to the shared board
  inbox      List messages for a recipient (optionally unread only)
  ack        Mark one or more messages as acknowledged / read
  thread     Show all messages in a topic chronologically
  topics     List recent topics with message counts & unread counts
  recent     List recent messages across all topics
  runs       List recent Codex / Claude runs recorded under ~/.hive-bridge/runs.jsonl
  evidence   Read turn.json, summary.json, and tail of events.jsonl from a run dir
  git-diff   Inspect git status --short and git diff for a remote directory
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
    cwd = os.path.abspath(args.cwd) if args.cwd else ""
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


def _load_json_safe(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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
    # Deduplicate by outDir keeping latest entry
    by_out = {}
    for e in entries:
        out_dir = e.get("out")
        if out_dir:
            by_out[out_dir] = {**by_out.get(out_dir, {}), **e}
    items = sorted(by_out.values(), key=lambda x: x.get("updatedAt") or x.get("startedAt") or 0, reverse=True)
    if args.cwd:
        target = os.path.abspath(args.cwd)
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
            "turn": turn,
            "summary": summary,
            "eventsTail": events_tail,
        }
    )


def cmd_git_diff(args):
    cwd = os.path.abspath(os.path.expanduser(args.cwd))
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

    p = sub.add_parser("post", help="Post a message to the shared board")
    p.add_argument("--from", dest="sender", required=True, help="Sender agent (e.g., antigravity, claude, codex, owner)")
    p.add_argument("--to", dest="recipient", default="all", help="Recipient agent or 'all'")
    p.add_argument("--topic", default="general", help="Topic / thread name")
    p.add_argument("--text", help="Inline message text")
    p.add_argument("--body-file", help="Read message body from file (or '-' for stdin)")
    p.add_argument("--stdin", action="store_true", help="Read message body from stdin")
    p.add_argument("--cwd", help="Related project directory")
    p.add_argument("--artifact", help="Related evidence/file path on remote host")
    p.add_argument("--meta", help="Optional JSON object with extra metadata")

    p = sub.add_parser("inbox", help="Read inbox messages")
    p.add_argument("--for", dest="recipient", help="Filter for recipient (includes 'all')")
    p.add_argument("--topic", help="Filter by topic")
    p.add_argument("--unread", action="store_true", help="Only unread messages")
    p.add_argument("--since-id", type=int, help="Only messages with id > since-id")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--brief", action="store_true", help="Human-readable compact summary (for hooks)")

    p = sub.add_parser("ack", help="Mark messages acknowledged")
    p.add_argument("ids", nargs="*", type=int)
    p.add_argument("--all", action="store_true", help="Ack all unread messages for --for")
    p.add_argument("--for", dest="recipient")
    p.add_argument("--by", help="Who acknowledged")

    p = sub.add_parser("thread", help="Show messages in a topic")
    p.add_argument("topic")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("topics", help="List recent topics")
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("recent", help="List recent messages across topics")
    p.add_argument("--topic")
    p.add_argument("--since-id", type=int)
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("runs", help="List recent Codex/Claude run records")
    p.add_argument("--cwd")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("evidence", help="Read turn.json, summary.json, and events tail from a run dir")
    p.add_argument("--out", required=True)
    p.add_argument("--tail", type=int, default=30)

    p = sub.add_parser("git-diff", help="Inspect git status and diff in a directory")
    p.add_argument("--cwd", required=True)
    p.add_argument("--max-bytes", type=int, default=24000)

    args = ap.parse_args()
    fn = globals()[f"cmd_{args.cmd.replace('-', '_')}"]
    fn(args)


if __name__ == "__main__":
    main()
