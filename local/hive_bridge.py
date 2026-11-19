#!/usr/bin/env python3
"""Local SSH wrapper CLI for hive-bridge.

Reads host profiles from ~/.config/hive-bridge/config.json (never hardcoded)
and communicates with remote hosts over multiplexed OpenSSH ControlMaster
connections — zero persistent remote HTTP daemons, zero port forwarding,
automatic reconnect after laptop sleep or network changes.

High-level session-governance commands (anti-sprawl):
  run        Dispatch to a pinned (cwd, runtime, role) slot — reuses existing
             session by default; only creates a new session if slot is empty or
             --fresh is passed (or --ephemeral for one-off tasks).
  slots      List pinned session slots.
  rotate     Build handoff summary from current slot, open a fresh session,
             rebind the slot, and mark (or --delete-old) the previous session.
  gc         Preview (default dry-run) or --apply deletion of rotated/ephemeral
             (and optionally --include-stale) Codex sessions while protecting
             every pinned slot.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("HIVE_BRIDGE_CONFIG", Path.home() / ".config" / "hive-bridge" / "config.json"))
CONTROL_PATH = str(Path.home() / ".ssh" / "cm-hive-%C")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"default_host": "", "hosts": {}}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"failed to parse {CONFIG_PATH}: {e}")


def resolve_host(host_name: str | None) -> tuple[str, dict]:
    cfg = load_config()
    hosts = cfg.get("hosts") or {}
    name = host_name or cfg.get("default_host") or (next(iter(hosts)) if hosts else "")
    if not name:
        sys.exit(
            f"no remote host configured in {CONFIG_PATH}. "
            "Run `./install.sh remote <ssh-host>` first or pass --host <ssh-alias>."
        )
    profile = hosts.get(name) or {"ssh": name}
    return name, profile


def ssh_base_args(profile: dict) -> list[str]:
    ssh_target = profile.get("ssh") or ""
    if not ssh_target:
        sys.exit("host profile missing 'ssh' target")
    Path.home().joinpath(".ssh").mkdir(mode=0o700, exist_ok=True)
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ControlMaster=auto",
        "-o",
        f"ControlPath={CONTROL_PATH}",
        "-o",
        "ControlPersist=10m",
        ssh_target,
    ]


def run_remote(profile: dict, remote_cmd: str, stdin_data: str | None = None, capture: bool = False):
    cmd = [*ssh_base_args(profile), remote_cmd]
    if capture:
        return subprocess.run(cmd, input=stdin_data, capture_output=True, text=True)
    return subprocess.run(cmd, input=stdin_data, text=True)


def quote_cmd(parts: list[str]) -> str:
    return " ".join(shlex.quote(p) for p in parts)


def default_out_dir(prefix: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"~/.hive-bridge/runs/{prefix}-{ts}-{uuid.uuid4().hex[:6]}"


def expand_remote_out_args(extra: list[str]) -> str:
    rebuilt = []
    for idx, tok in enumerate(extra):
        if idx > 0 and extra[idx - 1] == "--out" and tok.startswith("~/.hive-bridge/"):
            rebuilt.append('"$HOME/' + tok[2:] + '"')
        else:
            rebuilt.append(shlex.quote(tok))
    return " ".join(rebuilt)


def parse_json_lines(text: str) -> list[dict]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def parse_json_single(text: str):
    trimmed = (text or "").strip()
    if not trimmed:
        return None
    try:
        return json.loads(trimmed)
    except Exception:
        return None


def _extract_local_file_flag(extra: list[str], flag_name: str) -> tuple[list[str], str | None]:
    """If flag_name points to an existing local file, read it and swap for --stdin."""
    out = []
    stdin_data = None
    i = 0
    while i < len(extra):
        tok = extra[i]
        if tok == flag_name and i + 1 < len(extra):
            val = extra[i + 1]
            p = Path(val).expanduser()
            if val != "-" and p.is_file():
                stdin_data = p.read_text(encoding="utf-8")
                out.append("--stdin")
                i += 2
                continue
        out.append(tok)
        i += 1
    return out, stdin_data


def _read_local_or_text(prompt_file: str | None, text: str | None) -> str:
    if prompt_file:
        if prompt_file == "-":
            return sys.stdin.read()
        return Path(prompt_file).expanduser().read_text(encoding="utf-8")
    if text is not None:
        return text
    sys.exit("provide --prompt-file or --text")


def _bus_call(profile: dict, bus_args: list[str], stdin_data: str | None = None):
    remote_bin = profile.get("relay_bus_bin") or "$HOME/.local/bin/hive-relay-bus"
    return run_remote(profile, f"{remote_bin} {quote_cmd(bus_args)}", stdin_data=stdin_data, capture=True)


def _run_remote_streaming(profile: dict, remote_cmd: str, stdin_data: str | None = None) -> tuple[int, list[dict]]:
    """Run remote command streaming stdout to our stdout while also parsing JSON lines."""
    cmd = [*ssh_base_args(profile), remote_cmd]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin_data is not None else None,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    )
    if stdin_data is not None and proc.stdin is not None:
        proc.stdin.write(stdin_data)
        proc.stdin.close()
    events = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        s = line.strip()
        if s:
            try:
                events.append(json.loads(s))
            except Exception:
                pass
    rc = proc.wait()
    return rc, events


# ---------------------------------------------------------------------------
# Basic / passthrough commands
# ---------------------------------------------------------------------------

def cmd_hosts(_args):
    cfg = load_config()
    print(json.dumps(cfg, ensure_ascii=False, indent=2))


def cmd_doctor(args):
    name, profile = resolve_host(args.host)
    codex_py = profile.get("codex_python") or "python3"
    remote_bin_dir = profile.get("remote_bin_dir") or "$HOME/.local/bin"
    script = f"""
set -u
echo "SSH=ok"
echo "HOST=$(hostname)"
echo "USER=$(whoami)"
if [ -x "{remote_bin_dir}/hive-relay-bus" ]; then echo "RELAY_BUS=ok"; else echo "RELAY_BUS=missing"; fi
if [ -x "{remote_bin_dir}/hive-codex-daemon" ]; then echo "CODEX_DAEMON_BIN=ok"; else echo "CODEX_DAEMON_BIN=missing"; fi
if [ -x "{remote_bin_dir}/hive-claude-runner" ]; then echo "CLAUDE_RUNNER_BIN=ok"; else echo "CLAUDE_RUNNER_BIN=missing"; fi
SOCKS=$(ls /tmp/codex-daemon-$(id -u)/ 2>/dev/null | grep -v '\\.lock$' | wc -l | tr -d ' ')
echo "CODEX_DAEMON_SOCKETS=$SOCKS"
if "{codex_py}" -c "import websockets; print(websockets.__version__)" >/dev/null 2>&1; then
  WSVER=$("{codex_py}" -c "import websockets; print(websockets.__version__)" 2>/dev/null)
  echo "CODEX_PYTHON=ok ($WSVER @ {codex_py})"
else
  echo "CODEX_PYTHON=missing_websockets ({codex_py})"
fi
if [ -x "$HOME/.local/bin/claude" ] || command -v claude >/dev/null 2>&1; then
  echo "CLAUDE_CLI=ok"
else
  echo "CLAUDE_CLI=missing"
fi
"""
    p = run_remote(profile, script, capture=True)
    result = {
        "host": name,
        "ssh": profile.get("ssh"),
        "ok": p.returncode == 0,
        "defaults": {
            "codex_sandbox": profile.get("codex_sandbox", "danger-full-access"),
            "codex_approval_policy": profile.get("codex_approval_policy", "never"),
            "claude_permission_mode": profile.get("claude_permission_mode", "bypassPermissions"),
        },
        "checks": {},
    }
    if p.returncode != 0:
        result["error"] = (p.stderr or p.stdout).strip()
    else:
        for line in p.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                result["checks"][k.strip()] = v.strip()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if p.returncode != 0:
        sys.exit(p.returncode)


def _apply_codex_defaults(profile: dict, subcmd: str, extra: list[str]) -> list[str]:
    out = list(extra)
    if subcmd == "new":
        if "--sandbox" not in out:
            out.extend(["--sandbox", profile.get("codex_sandbox", "danger-full-access")])
        if "--approval-policy" not in out:
            out.extend(["--approval-policy", profile.get("codex_approval_policy", "never")])
    elif subcmd == "send":
        if "--approval-policy" not in out:
            out.extend(["--approval-policy", profile.get("codex_approval_policy", "never")])
    return out


def _apply_claude_defaults(profile: dict, subcmd: str, extra: list[str]) -> list[str]:
    out = list(extra)
    if subcmd in ("new", "send") and "--permission-mode" not in out:
        perm = profile.get("claude_permission_mode", "bypassPermissions")
        if perm:
            out.extend(["--permission-mode", perm])
    return out


def cmd_codex(args):
    _, profile = resolve_host(args.host)
    extra = list(args.extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not extra:
        sys.exit("usage: hive-bridge codex [--host H] <list|new|send|steer|interrupt|delete> [args...]")
    subcmd = extra[0]
    extra, stdin_data = _extract_local_file_flag(extra, "--prompt-file")
    extra = _apply_codex_defaults(profile, subcmd, extra)
    if subcmd in ("new", "send") and "--out" not in extra:
        extra.extend(["--out", default_out_dir("codex")])

    remote_bin = profile.get("codex_daemon_bin") or "$HOME/.local/bin/hive-codex-daemon"
    remote_cmd = f"{remote_bin} {expand_remote_out_args(extra)}"
    p = run_remote(profile, remote_cmd, stdin_data=stdin_data)
    sys.exit(p.returncode)


def cmd_claude(args):
    _, profile = resolve_host(args.host)
    extra = list(args.extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not extra:
        sys.exit("usage: hive-bridge claude [--host H] <list|new|send> [args...]")
    subcmd = extra[0]
    extra, stdin_data = _extract_local_file_flag(extra, "--prompt-file")
    extra = _apply_claude_defaults(profile, subcmd, extra)
    if subcmd in ("new", "send") and "--out" not in extra:
        extra.extend(["--out", default_out_dir("claude")])

    remote_bin = profile.get("claude_runner_bin") or "$HOME/.local/bin/hive-claude-runner"
    remote_cmd = f"{remote_bin} {expand_remote_out_args(extra)}"
    p = run_remote(profile, remote_cmd, stdin_data=stdin_data)
    sys.exit(p.returncode)


def cmd_bus(args):
    _, profile = resolve_host(args.host)
    extra = list(args.extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not extra:
        sys.exit("usage: hive-bridge bus [--host H] <subcommand> [args...]")
    extra, stdin_data = _extract_local_file_flag(extra, "--body-file")
    remote_bin = profile.get("relay_bus_bin") or "$HOME/.local/bin/hive-relay-bus"
    remote_cmd = f"{remote_bin} {quote_cmd(extra)}"
    p = run_remote(profile, remote_cmd, stdin_data=stdin_data)
    sys.exit(p.returncode)


# ---------------------------------------------------------------------------
# High-level Session Governance: slots / run / rotate / gc
# ---------------------------------------------------------------------------

def cmd_slots(args):
    _, profile = resolve_host(args.host)
    bus_args = ["slot-list"]
    if args.cwd:
        bus_args.extend(["--cwd", args.cwd])
    if args.runtime:
        bus_args.extend(["--runtime", args.runtime])
    p = _bus_call(profile, bus_args)
    if p.returncode != 0:
        sys.exit((p.stderr or p.stdout).strip())
    sys.stdout.write(p.stdout)


def _dispatch_turn(profile: dict, runtime: str, mode: str, *, cwd: str, thread_id: str | None,
                   name: str, prompt: str, model: str | None, effort: str | None,
                   sandbox: str | None, approval_policy: str | None,
                   permission_mode: str | None, out_dir: str, timeout: int | None):
    if runtime == "codex":
        remote_bin = profile.get("codex_daemon_bin") or "$HOME/.local/bin/hive-codex-daemon"
        extra = [mode]
        if mode == "new":
            extra.extend(["--cwd", cwd])
            if name:
                extra.extend(["--name", name])
            extra.extend(["--sandbox", sandbox or profile.get("codex_sandbox", "danger-full-access")])
        else:
            assert thread_id
            extra.extend(["--thread", thread_id])
        extra.extend(["--approval-policy", approval_policy or profile.get("codex_approval_policy", "never")])
        extra.extend(["--stdin", "--out", out_dir])
        if model:
            extra.extend(["--model", model])
        if effort:
            extra.extend(["--effort", effort])
        if timeout:
            extra.extend(["--timeout", str(timeout)])
        remote_cmd = f"{remote_bin} {expand_remote_out_args(extra)}"
        return _run_remote_streaming(profile, remote_cmd, stdin_data=prompt)

    # claude runtime
    remote_bin = profile.get("claude_runner_bin") or "$HOME/.local/bin/hive-claude-runner"
    extra = [mode, "--cwd", cwd, "--stdin", "--out", out_dir]
    if mode == "send":
        assert thread_id
        extra.extend(["--thread", thread_id])
    if name:
        extra.extend(["--name", name])
    if model:
        extra.extend(["--model", model])
    perm = permission_mode or profile.get("claude_permission_mode", "bypassPermissions")
    if perm:
        extra.extend(["--permission-mode", perm])
    if timeout:
        extra.extend(["--timeout", str(timeout)])
    remote_cmd = f"{remote_bin} {expand_remote_out_args(extra)}"
    return _run_remote_streaming(profile, remote_cmd, stdin_data=prompt)


def cmd_run(args):
    """Slot-aware dispatch: reuses pinned (cwd, runtime, role) session unless --fresh or --ephemeral."""
    _, profile = resolve_host(args.host)
    prompt = _read_local_or_text(args.prompt_file, args.text)
    cwd = args.cwd or profile.get("default_cwd")
    if not cwd:
        sys.exit("--cwd is required (or set default_cwd in host profile)")

    proj_short = Path(cwd.rstrip("/")).name or "project"
    session_name = args.name or (f"tmp:{args.role}@{proj_short}" if args.ephemeral else f"{args.role}@{proj_short}")
    out_dir = args.out or default_out_dir(f"{args.runtime}-{args.role}")

    existing_thread = None
    if not args.ephemeral and not args.fresh:
        r = _bus_call(profile, ["slot-get", "--cwd", cwd, "--runtime", args.runtime, "--role", args.role])
        if r.returncode == 0:
            slot_obj = parse_json_single(r.stdout)
            if slot_obj and slot_obj.get("threadId"):
                existing_thread = slot_obj["threadId"]

    mode = "send" if existing_thread else "new"
    rc, events = _dispatch_turn(
        profile,
        args.runtime,
        mode,
        cwd=cwd,
        thread_id=existing_thread,
        name=session_name,
        prompt=prompt,
        model=args.model,
        effort=args.effort,
        sandbox=args.sandbox,
        approval_policy=args.approval_policy,
        permission_mode=args.permission_mode,
        out_dir=out_dir,
        timeout=args.timeout,
    )

    resolved_thread = existing_thread
    resolved_out = None
    for ev in events:
        if ev.get("threadId") and not resolved_thread:
            resolved_thread = ev["threadId"]
        if ev.get("out"):
            resolved_out = ev["out"]
        elif ev.get("summary"):
            resolved_out = str(Path(ev["summary"]).parent)

    if resolved_thread:
        if args.ephemeral:
            _bus_call(
                profile,
                [
                    "meta-set",
                    "--runtime",
                    args.runtime,
                    "--thread",
                    resolved_thread,
                    "--kind",
                    "ephemeral",
                    "--cwd",
                    cwd,
                    "--role",
                    args.role,
                    "--ttl-hours",
                    str(args.ttl_hours),
                ],
            )
        else:
            slot_args = [
                "slot-set",
                "--cwd",
                cwd,
                "--runtime",
                args.runtime,
                "--role",
                args.role,
                "--thread",
                resolved_thread,
                "--name",
                session_name,
                "--inc-turns",
            ]
            if resolved_out:
                slot_args.extend(["--out", resolved_out])
            _bus_call(profile, slot_args)

    sys.exit(rc)


def cmd_rotate(args):
    """Rotate a pinned slot cleanly: build handoff -> open fresh session -> rebind slot -> mark/delete old."""
    _, profile = resolve_host(args.host)
    cwd = args.cwd or profile.get("default_cwd")
    if not cwd:
        sys.exit("--cwd is required")

    r = _bus_call(profile, ["slot-get", "--cwd", cwd, "--runtime", args.runtime, "--role", args.role])
    if r.returncode != 0:
        sys.exit((r.stderr or r.stdout).strip())
    slot_obj = parse_json_single(r.stdout) or {}
    old_thread = slot_obj.get("threadId")
    last_out = slot_obj.get("lastOut") or ""

    hb_args = ["handoff-build", "--cwd", cwd, "--role", args.role]
    if last_out:
        hb_args.extend(["--out", last_out])
    if args.note:
        hb_args.extend(["--note", args.note])
    hb = _bus_call(profile, hb_args)
    if hb.returncode != 0:
        sys.exit(f"handoff-build failed: {(hb.stderr or hb.stdout).strip()}")
    handoff_prompt = hb.stdout

    proj_short = Path(cwd.rstrip("/")).name or "project"
    session_name = args.name or slot_obj.get("name") or f"{args.role}@{proj_short}"
    out_dir = default_out_dir(f"rotate-{args.runtime}-{args.role}")

    rc, events = _dispatch_turn(
        profile,
        args.runtime,
        "new",
        cwd=cwd,
        thread_id=None,
        name=session_name,
        prompt=handoff_prompt,
        model=args.model,
        effort=args.effort,
        sandbox=args.sandbox,
        approval_policy=args.approval_policy,
        permission_mode=args.permission_mode,
        out_dir=out_dir,
        timeout=args.timeout,
    )
    if rc != 0:
        sys.exit(rc)

    new_thread = None
    resolved_out = None
    for ev in events:
        if ev.get("threadId") and not new_thread:
            new_thread = ev["threadId"]
        if ev.get("out"):
            resolved_out = ev["out"]
        elif ev.get("summary"):
            resolved_out = str(Path(ev["summary"]).parent)

    if not new_thread:
        sys.exit("rotation completed without capturing a new threadId")

    slot_args = [
        "slot-set",
        "--cwd",
        cwd,
        "--runtime",
        args.runtime,
        "--role",
        args.role,
        "--thread",
        new_thread,
        "--name",
        session_name,
        "--inc-turns",
    ]
    if resolved_out:
        slot_args.extend(["--out", resolved_out])
    _bus_call(profile, slot_args)

    deleted_old = False
    if old_thread and old_thread != new_thread and args.delete_old and args.runtime == "codex":
        codex_bin = profile.get("codex_daemon_bin") or "$HOME/.local/bin/hive-codex-daemon"
        dp = run_remote(profile, f"{codex_bin} delete --thread {shlex.quote(old_thread)}", capture=True)
        if dp.returncode == 0:
            deleted_old = True
            _bus_call(profile, ["meta-del", "--runtime", "codex", "--thread", old_thread])

    print(
        json.dumps(
            {
                "event": "slot_rotated",
                "runtime": args.runtime,
                "cwd": cwd,
                "role": args.role,
                "oldThreadId": old_thread,
                "newThreadId": new_thread,
                "deletedOld": deleted_old,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def cmd_gc(args):
    """Dry-run (default) or --apply cleanup of rotated, expired ephemeral, and optionally stale Codex sessions."""
    _, profile = resolve_host(args.host)
    codex_bin = profile.get("codex_daemon_bin") or "$HOME/.local/bin/hive-codex-daemon"

    p_list = run_remote(profile, f"{codex_bin} list --limit 200", capture=True)
    if p_list.returncode != 0:
        sys.exit(f"failed to list Codex sessions: {(p_list.stderr or p_list.stdout).strip()}")
    sessions = parse_json_lines(p_list.stdout)

    p_slots = _bus_call(profile, ["slot-list", "--runtime", "codex"])
    slots = parse_json_lines(p_slots.stdout) if p_slots.returncode == 0 else []
    pinned_ids = {s["threadId"] for s in slots if s.get("threadId")}

    p_meta = _bus_call(profile, ["meta-list", "--runtime", "codex"])
    metas = {m["threadId"]: m for m in (parse_json_lines(p_meta.stdout) if p_meta.returncode == 0 else [])}

    now = time.time()
    stale_cutoff = now - args.days * 86400
    candidates = []
    protected = []

    for s in sessions:
        tid = s.get("id")
        if not tid:
            continue
        status = s.get("status")
        is_active = isinstance(status, dict) and status.get("type") == "active"
        if tid in pinned_ids or is_active:
            protected.append({"id": tid, "name": s.get("name"), "reason": "active" if is_active else "pinned_slot"})
            continue
        m = metas.get(tid)
        reason = None
        if m and m.get("kind") == "rotated":
            reason = "rotated"
        elif m and m.get("kind") == "ephemeral":
            ttl_sec = float(m.get("ttlHours") or 24.0) * 3600.0
            if args.force_ephemeral or (now - float(m.get("updatedAt") or 0)) >= ttl_sec:
                reason = "ephemeral_expired"
        elif args.include_stale:
            upd = s.get("updatedAt") or 0
            if isinstance(upd, (int, float)) and upd > 1e12:
                upd = upd / 1000.0
            if isinstance(upd, (int, float)) and upd > 0 and upd < stale_cutoff:
                reason = f"stale>{args.days}d"

        if reason:
            candidates.append({"id": tid, "name": s.get("name"), "cwd": s.get("cwd"), "reason": reason})

    deleted = []
    errors = []
    if args.apply:
        for c in candidates:
            dp = run_remote(profile, f"{codex_bin} delete --thread {shlex.quote(c['id'])}", capture=True)
            if dp.returncode == 0:
                deleted.append(c["id"])
                _bus_call(profile, ["meta-del", "--runtime", "codex", "--thread", c["id"]])
            else:
                errors.append({"id": c["id"], "error": (dp.stderr or dp.stdout).strip()})

    print(
        json.dumps(
            {
                "dryRun": not args.apply,
                "totalSessions": len(sessions),
                "protectedCount": len(protected),
                "candidates": candidates,
                "deleted": deleted,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("hosts", help="Show configured host profiles")

    p = sub.add_parser("doctor", help="Verify remote host connectivity and components")
    p.add_argument("--host", help="Host alias from config.json")

    p = sub.add_parser("slots", help="List pinned project/role session slots")
    p.add_argument("--host")
    p.add_argument("--cwd")
    p.add_argument("--runtime", choices=["codex", "claude"])

    p = sub.add_parser("run", help="Slot-aware dispatch (reuses pinned session by default)")
    p.add_argument("--host")
    p.add_argument("--runtime", default="codex", choices=["codex", "claude"])
    p.add_argument("--cwd", help="Remote project directory")
    p.add_argument("--role", default="coder", help="Slot role name (e.g. coder, reviewer, frontend)")
    p.add_argument("--name", help="Optional display name for new session")
    p.add_argument("--fresh", action="store_true", help="Force a brand-new session for this slot (marks previous rotated)")
    p.add_argument("--ephemeral", action="store_true", help="One-off session not bound to a slot (auto-tagged for GC)")
    p.add_argument("--ttl-hours", type=float, default=24.0, help="TTL hours before ephemeral session is GC-eligible")
    p.add_argument("--prompt-file")
    p.add_argument("--text")
    p.add_argument("--model")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max", "ultra"])
    p.add_argument("--sandbox", choices=["read-only", "workspace-write", "danger-full-access"])
    p.add_argument("--approval-policy", choices=["never", "on-request", "on-failure", "untrusted"])
    p.add_argument("--permission-mode", choices=["default", "plan", "acceptEdits", "bypassPermissions"])
    p.add_argument("--out")
    p.add_argument("--timeout", type=int)

    p = sub.add_parser("rotate", help="Rotate a pinned slot cleanly with a handoff packet")
    p.add_argument("--host")
    p.add_argument("--runtime", default="codex", choices=["codex", "claude"])
    p.add_argument("--cwd", help="Remote project directory")
    p.add_argument("--role", default="coder")
    p.add_argument("--name")
    p.add_argument("--note", help="Extra handoff note for the fresh session")
    p.add_argument("--delete-old", action="store_true", help="Delete the previous Codex session immediately")
    p.add_argument("--model")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max", "ultra"])
    p.add_argument("--sandbox", choices=["read-only", "workspace-write", "danger-full-access"])
    p.add_argument("--approval-policy", choices=["never", "on-request", "on-failure", "untrusted"])
    p.add_argument("--permission-mode", choices=["default", "plan", "acceptEdits", "bypassPermissions"])
    p.add_argument("--timeout", type=int)

    p = sub.add_parser("gc", help="Preview or clean rotated/ephemeral/stale Codex sessions (never touches pinned slots)")
    p.add_argument("--host")
    p.add_argument("--days", type=float, default=7.0, help="Age threshold for --include-stale")
    p.add_argument("--include-stale", action="store_true", help="Also include unpinned sessions older than --days")
    p.add_argument("--force-ephemeral", action="store_true", help="Include all non-active ephemeral sessions regardless of TTL")
    p.add_argument("--apply", action="store_true", help="Actually delete candidate sessions (default is dry-run)")

    for name in ("codex", "claude", "bus"):
        p = sub.add_parser(name, help=f"Direct low-level command forwarding to remote {name}")
        p.add_argument("--host", help="Host alias from config.json")
        p.add_argument("extra", nargs=argparse.REMAINDER)

    args = ap.parse_args()
    globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    main()
