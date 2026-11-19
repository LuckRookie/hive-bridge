---
name: hive-bridge
description: >-
  Connect local Antigravity (or local Claude Code / Codex) to remote SSH servers
  running Codex (shared app-server daemon, live-visible in Codex Desktop) and Claude
  (Desktop Remote or CLI) via a zero-daemon SSH CLI, slot-based session lifecycle
  governance (anti-sprawl), and shared SQLite blackboard. Use whenever dispatching
  work to remote Codex/Claude, managing pinned session slots (run/rotate/gc),
  checking the shared board/inbox, steering or interrupting a remote Codex turn,
  or verifying remote git diff and summary.json evidence.
---

# Hive Bridge — Local ↔ Remote SSH Agent Orchestration

`hive-bridge` coordinates **local Antigravity** (or local Claude Code / Codex), **remote Codex** (via its shared `app-server` Unix socket daemon so Codex Desktop stays 100% live-visible), and **remote Claude** (Desktop Remote or CLI) with **zero persistent remote HTTP services** and **no MCP tunnel fragility**.

All remote hosts and default permission profiles live in `~/.config/hive-bridge/config.json` (never hardcoded). Local CLI wrapper: `~/.local/bin/hive-bridge`.

## Side-Pane Console

When working with remote sessions or the shared board, surface the native side-pane console pill (only once, when relevant):

`[Hive Bridge Console](sidecar://hive-bridge/console/)`

## Core Rules (Follow Strictly)

1. **Prevent session sprawl — always prefer `hive-bridge run` (pinned slots) over raw `codex new`.**
   - Every `(cwd, runtime, role)` binds to a single persistent slot (e.g., `codex:coder`, `codex:reviewer`, `claude:frontend`).
   - `hive-bridge run` automatically reuses the existing slot session; it only creates a session when the slot is empty.
   - For truly one-off tasks (e.g., a quick blind review), pass `--ephemeral` so it is tagged for automatic `gc` cleanup instead of polluting Desktop lists.
   - When a long-lived slot gets heavy/stale, use `hive-bridge rotate` to hand off context into one clean session and retire the old one.
2. **Permissions are host-profile driven.** Do not add ad-hoc permission restrictions or popups; `hive-bridge` transparently passes the Owner's configured defaults from `~/.config/hive-bridge/config.json` (default: full access for automated slots), and existing sessions retain their configured permissions.
3. **Ask the Owner before choosing or changing model / reasoning effort.** Before creating a brand-new slot session or changing an existing session's `--model` / `--effort`, ask the Owner which model and effort to use.
4. **Never blindly trust self-reports.** Always verify `summary.json` (`status`, `commands` exit codes) and remote `git diff` before reporting completion.
5. **No polling loops for long runs.** Launch `run` / `rotate` / `codex new` / `codex send` as a background command (`WaitMsBeforeAsync: 500`) and wait for the completion notification.

## Quick Command Reference

Pass `--host <alias>` to target a non-default remote host from `~/.config/hive-bridge/config.json`.

### 1. Health & Hosts
```bash
hive-bridge hosts
hive-bridge doctor [--host <alias>]
```

### 2. Slot-Based Dispatch (Recommended — Prevents Session Sprawl)
```bash
# List pinned slots
hive-bridge slots [--cwd /path/to/remote/project]

# Dispatch to a pinned slot (auto-creates once, then reuses forever)
hive-bridge run --runtime codex --cwd /path/to/remote/project --role coder \
  --model <model> --effort <effort> --prompt-file /local/or/remote/prompt.md

# One-off ephemeral task (tagged for GC, does not touch pinned slots)
hive-bridge run --runtime codex --cwd /path/to/remote/project --role quick-review \
  --ephemeral --prompt-file review.md

# Rotate a heavy slot cleanly (handoff summary -> fresh session -> retire/delete old)
hive-bridge rotate --runtime codex --cwd /path/to/remote/project --role coder --delete-old

# Preview or clean rotated/ephemeral sessions (never touches pinned slots or active turns)
hive-bridge gc
hive-bridge gc --apply
```
- If `--prompt-file` points to a local file, `hive-bridge` streams it cleanly over SSH stdin.
- If `--out` is omitted, `hive-bridge` auto-creates `~/.hive-bridge/runs/...` on the remote host and indexes it for the Evidence tab.

### 3. Low-Level Remote Codex Controls (Steer / Interrupt / Inspect)
```bash
hive-bridge codex list --cwd /path/to/remote/project
hive-bridge codex steer --thread <uuid> --turn <turn-id> --text "Adjust step 3..."
hive-bridge codex interrupt --thread <uuid> --turn <turn-id>
```

### 4. Shared Blackboard / Inbox (Three-Way Interop with Claude & Codex)
```bash
hive-bridge bus post --from antigravity --to claude --topic ui-redesign \
  --cwd /path/to/remote/project --text "Please design the settings panel..."
hive-bridge bus inbox --for antigravity --unread
hive-bridge bus ack <msg_id> --by antigravity
hive-bridge bus thread ui-redesign
```

### 5. Evidence & Remote Git Verification
```bash
hive-bridge bus runs --limit 10
hive-bridge bus evidence --out /remote/path/to/run-dir
hive-bridge bus git-diff --cwd /path/to/remote/project
```
