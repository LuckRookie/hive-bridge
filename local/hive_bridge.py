#!/usr/bin/env python3
"""Local SSH wrapper CLI for hive-bridge.

Reads host profiles from ~/.config/hive-bridge/config.json (never hardcoded)
and communicates with remote hosts over multiplexed OpenSSH ControlMaster
connections — zero persistent remote HTTP daemons, zero port forwarding,
automatic reconnect after laptop sleep or network changes.
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
    result = {"host": name, "ssh": profile.get("ssh"), "ok": p.returncode == 0, "checks": {}}
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


def cmd_codex(args):
    _, profile = resolve_host(args.host)
    extra = list(args.extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not extra:
        sys.exit("usage: hive_bridge.py codex [--host H] <list|new|send|steer|interrupt|delete> [args...]")
    subcmd = extra[0]
    extra, stdin_data = _extract_local_file_flag(extra, "--prompt-file")
    if subcmd in ("new", "send") and "--out" not in extra:
        extra.extend(["--out", default_out_dir("codex")])

    remote_bin = profile.get("codex_daemon_bin") or "$HOME/.local/bin/hive-codex-daemon"
    rebuilt = []
    for idx, tok in enumerate(extra):
        if idx > 0 and extra[idx - 1] == "--out" and tok.startswith("~/.hive-bridge/"):
            rebuilt.append('"$HOME/' + tok[2:] + '"')
        else:
            rebuilt.append(shlex.quote(tok))
    remote_cmd = f"{remote_bin} " + " ".join(rebuilt)
    p = run_remote(profile, remote_cmd, stdin_data=stdin_data)
    sys.exit(p.returncode)


def cmd_claude(args):
    _, profile = resolve_host(args.host)
    extra = list(args.extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not extra:
        sys.exit("usage: hive_bridge.py claude [--host H] <new|send> [args...]")
    subcmd = extra[0]
    extra, stdin_data = _extract_local_file_flag(extra, "--prompt-file")
    if subcmd in ("new", "send") and "--out" not in extra:
        extra.extend(["--out", default_out_dir("claude")])

    remote_bin = profile.get("claude_runner_bin") or "$HOME/.local/bin/hive-claude-runner"
    rebuilt = []
    for idx, tok in enumerate(extra):
        if idx > 0 and extra[idx - 1] == "--out" and tok.startswith("~/.hive-bridge/"):
            rebuilt.append('"$HOME/' + tok[2:] + '"')
        else:
            rebuilt.append(shlex.quote(tok))
    remote_cmd = f"{remote_bin} " + " ".join(rebuilt)
    p = run_remote(profile, remote_cmd, stdin_data=stdin_data)
    sys.exit(p.returncode)


def cmd_bus(args):
    _, profile = resolve_host(args.host)
    extra = list(args.extra)
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not extra:
        sys.exit("usage: hive_bridge.py bus [--host H] <post|inbox|ack|thread|topics|recent|runs|evidence|git-diff> [args...]")
    extra, stdin_data = _extract_local_file_flag(extra, "--body-file")
    remote_bin = profile.get("relay_bus_bin") or "$HOME/.local/bin/hive-relay-bus"
    remote_cmd = f"{remote_bin} {quote_cmd(extra)}"
    p = run_remote(profile, remote_cmd, stdin_data=stdin_data)
    sys.exit(p.returncode)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("hosts", help="Show configured host profiles")

    p = sub.add_parser("doctor", help="Verify remote host connectivity and components")
    p.add_argument("--host", help="Host alias from config.json")

    for name in ("codex", "claude", "bus"):
        p = sub.add_parser(name, help=f"Forward command to remote {name}")
        p.add_argument("--host", help="Host alias from config.json")
        p.add_argument("extra", nargs=argparse.REMAINDER)

    args = ap.parse_args()
    globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    main()
