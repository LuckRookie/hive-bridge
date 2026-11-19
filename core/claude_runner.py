#!/usr/bin/env python3
"""Optional headless Claude Code CLI runner for hive-bridge.

Wraps `claude -p --output-format stream-json [--resume <session_id>]` and writes
the same turn.json / events.jsonl / summary.json artifact structure as codex_daemon.py,
so both runtimes appear uniformly in the hive-bridge console and Evidence tab.

Note: For interactive Claude Desktop Remote sessions, prefer the shared blackboard
(`relay_bus.py post/inbox`) rather than spawning a separate headless CLI run.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("HIVE_BRIDGE_HOME", Path.home() / ".hive-bridge"))
RUNS_INDEX = STATE_DIR / "runs.jsonl"


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def record_run(entry: dict):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(RUNS_INDEX, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_prompt(args) -> str:
    if getattr(args, "stdin", False):
        return sys.stdin.read()
    if args.prompt_file:
        if args.prompt_file == "-":
            return sys.stdin.read()
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.text:
        return args.text
    sys.exit("give --prompt-file, --text, or --stdin")


def find_claude() -> str:
    override = os.environ.get("CLAUDE_BIN")
    if override and os.path.exists(override):
        return override
    for candidate in (
        str(Path.home() / ".local" / "bin" / "claude"),
        shutil.which("claude") or "",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    sys.exit("claude CLI not found on remote host")


def run_claude(args, resume_id=None):
    claude_bin = find_claude()
    prompt = read_prompt(args)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    Path(out_dir, "prompt.txt").write_text(prompt, encoding="utf-8")
    cwd = os.path.abspath(args.cwd) if args.cwd else os.getcwd()

    cmd = [claude_bin, "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    if resume_id:
        cmd.extend(["--resume", resume_id])
    if args.model:
        cmd.extend(["--model", args.model])
    if args.permission_mode:
        cmd.extend(["--permission-mode", args.permission_mode])
    cmd.append(prompt)

    started_at = time.time()
    session_id = resume_id or ""
    messages = []
    other_items = []
    final_result = None
    error_msg = None
    token_usage = None

    record_run(
        {
            "runtime": "claude",
            "out": out_dir,
            "threadId": session_id,
            "cwd": cwd,
            "name": args.name or "",
            "status": "running",
            "startedAt": started_at,
            "updatedAt": started_at,
        }
    )

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    with open(os.path.join(out_dir, "events.jsonl"), "a", encoding="utf-8") as ev_log:
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                ev_log.write(line + "\n")
                ev_log.flush()
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                sid = ev.get("session_id") or ev.get("sessionId")
                if sid and not session_id:
                    session_id = sid
                    with open(os.path.join(out_dir, "turn.json"), "w", encoding="utf-8") as tf:
                        json.dump(
                            {
                                "runtime": "claude",
                                "threadId": session_id,
                                "turnId": str(proc.pid),
                                "cwd": cwd,
                                "name": args.name or "",
                                "startedAt": started_at,
                            },
                            tf,
                            ensure_ascii=False,
                            indent=2,
                        )
                    emit({"event": "turn_started", "runtime": "claude", "threadId": session_id, "pid": proc.pid, "out": out_dir})
                if ev.get("type") == "assistant":
                    content = (ev.get("message") or {}).get("content") or []
                    text_parts = []
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "text" and c.get("text"):
                            text_parts.append(c["text"])
                        elif c.get("type") == "tool_use":
                            other_items.append(
                                {
                                    "type": "tool_use",
                                    "tool": c.get("name"),
                                    "input": c.get("input"),
                                }
                            )
                    if text_parts:
                        messages.append("\n".join(text_parts))
                elif ev.get("type") == "result":
                    final_result = ev.get("result")
                    if ev.get("is_error"):
                        error_msg = str(ev.get("result") or "claude error")
                    if ev.get("usage"):
                        token_usage = ev.get("usage")
            rc = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = -1
            error_msg = "timeout"

    stderr_text = proc.stderr.read().strip() if proc.stderr else ""
    duration_ms = int((time.time() - started_at) * 1000)
    status = "completed" if rc == 0 and not error_msg else ("timeout" if error_msg == "timeout" else "failed")
    if status == "failed" and not error_msg:
        error_msg = stderr_text or f"exit code {rc}"

    summary = {
        "runtime": "claude",
        "threadId": session_id,
        "turnId": str(proc.pid),
        "cwd": cwd,
        "status": status,
        "error": error_msg,
        "durationMs": duration_ms,
        "finalMessage": final_result if final_result is not None else (messages[-1] if messages else None),
        "messages": messages,
        "commands": [],
        "fileChanges": [],
        "otherItems": other_items,
        "approvalsDeclined": [],
        "tokenUsage": token_usage,
    }
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    record_run(
        {
            "runtime": "claude",
            "out": out_dir,
            "threadId": session_id,
            "cwd": cwd,
            "name": args.name or "",
            "status": status,
            "startedAt": started_at,
            "updatedAt": time.time(),
        }
    )
    emit({"event": "turn_finished", "runtime": "claude", "threadId": session_id, "status": status, "summary": summary_path})
    if rc != 0:
        sys.exit(rc if rc > 0 else 1)


def _pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cmd_list(args):
    """List live Claude sessions from ~/.claude/sessions/*.json (both Desktop Remote & CLI)."""
    claude_dir = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))
    sessions_dir = claude_dir / "sessions"
    cwd_filter = os.path.abspath(os.path.expanduser(args.cwd)) if args.cwd else None

    found = {}
    if sessions_dir.is_dir():
        for p in sorted(sessions_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            sid = data.get("sessionId")
            if not sid:
                continue
            cwd = data.get("cwd") or ""
            if cwd_filter and os.path.abspath(cwd) != cwd_filter:
                continue
            pid = data.get("pid")
            alive = _pid_alive(pid) if isinstance(pid, int) else False
            if not args.all and not alive:
                continue
            entry = {
                "id": sid,
                "name": data.get("name") or Path(cwd).name or sid[:12],
                "cwd": cwd,
                "pid": pid,
                "alive": alive,
                "entrypoint": data.get("entrypoint") or data.get("kind") or "cli",
                "status": data.get("status") if alive else "stopped",
                "startedAt": data.get("startedAt"),
                "updatedAt": data.get("updatedAt") or int(p.stat().st_mtime * 1000),
            }
            # Keep newest / alive record per sessionId
            prev = found.get(sid)
            if not prev or (entry["alive"] and not prev["alive"]) or (entry["updatedAt"] or 0) > (prev["updatedAt"] or 0):
                found[sid] = entry

    items = sorted(found.values(), key=lambda x: (1 if x["alive"] else 0, x.get("updatedAt") or 0), reverse=True)
    for item in items[: args.limit]:
        emit(item)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="List live (or --all) Claude Desktop Remote & CLI sessions")
    p.add_argument("--cwd")
    p.add_argument("--all", action="store_true", help="Include stopped session records as well")
    p.add_argument("--limit", type=int, default=30)

    def common(parser):
        parser.add_argument("--cwd", help="Project working directory")
        parser.add_argument("--name", help="Optional human label")
        parser.add_argument("--prompt-file")
        parser.add_argument("--text")
        parser.add_argument("--stdin", action="store_true")
        parser.add_argument("--model")
        parser.add_argument("--permission-mode", choices=["default", "plan", "acceptEdits", "bypassPermissions"])
        parser.add_argument("--out", required=True)
        parser.add_argument("--timeout", type=int, default=3600)

    p = sub.add_parser("new")
    common(p)

    p = sub.add_parser("send")
    p.add_argument("--thread", required=True, help="Existing Claude session UUID to resume")
    common(p)

    args = ap.parse_args()
    if args.cmd == "list":
        cmd_list(args)
        return
    run_claude(args, resume_id=getattr(args, "thread", None))


if __name__ == "__main__":
    main()
