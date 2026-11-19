---
name: hive-bridge
description: >-
  Connect local Antigravity (or local Claude Code / Codex) to remote SSH servers
  running Codex (shared app-server daemon, live-visible in Codex Desktop) and Claude
  (pre-opened Desktop Remote sessions via blackboard wait/post, or headless CLI).
  Use whenever dispatching work to remote Codex/Claude, managing pinned session slots
  (run/rotate/gc), checking seated workers or the shared board/inbox, steering or
  interrupting a remote Codex turn, or verifying remote git diff and summary.json evidence.
---

# Hive Bridge — Local ↔ Remote SSH Agent Orchestration

`hive-bridge` coordinates **local Antigravity** (or local Claude Code / Codex), **remote Codex**, and **remote Claude** with **zero persistent remote HTTP services** and **no MCP tunnel fragility**.

All remote hosts and default permission profiles live in `~/.config/hive-bridge/config.json` (never hardcoded). Local CLI wrapper: `~/.local/bin/hive-bridge`.

## Side-Pane Console

When working with remote sessions or the shared board, surface the native side-pane console pill (only once, when relevant):

`[Hive Bridge](sidecar://hive-bridge/console/)`

## Operating Model — Owner-Created Sessions & Live Desktop Visibility (READ FIRST!)

Different runtimes have different Desktop visibility mechanics. **Always follow this exact model so the Owner can watch every agent work live in their Desktop apps and never suffers session sprawl:**

| Target Agent | How It Connects | Live-Visible in Owner's Desktop App? | Default Dispatch Method |
| :--- | :--- | :--- | :--- |
| **Remote Codex** (e.g. Linux server) | Shared `codex app-server` Unix socket (`threadSource: user`) | **YES — 100% live**, whether created by Owner or by `hive-bridge` | Bind Owner's existing UUID to a slot (`bus slot-set`) OR use `hive-bridge run --runtime codex --cwd <dir> --role <role>` |
| **Remote Claude Desktop Remote** (Owner's primary Claude workflow!) | Owner pre-opens a Remote session in Claude Desktop and puts it on standby (`hive-relay-bus wait --for <seat>`) | **YES — 100% live** inside the Owner's open Claude Desktop window! | **Post to the shared board**: `hive-bridge bus post --from antigravity --to <seat> ...`, then wait for reply via `hive-bridge bus wait --for antigravity` |
| **Local Claude / Local Codex Desktop** | Private per-app processes (external CLI runs do NOT appear in Desktop sidebar) | **Only if Owner pre-opened the window** and seated it on the board | Do NOT spawn external headless local CLI expecting it to appear in Desktop; coordinate only with Owner's pre-opened seated windows |
| **Remote Headless Claude CLI** | Independent `claude -p --resume` subprocess (requires remote `claude /login`) | **NO** (disk-only `.jsonl`, not shown in Claude Desktop Remote sidebar) | **ONLY use `hive-bridge run --runtime claude` when the Owner explicitly asks for unattended headless CLI** |

### Golden Rules

1. **Prefer Owner-created sessions over creating new ones.**
   - Before spawning a new Codex session, check `hive-bridge slots` and `hive-bridge codex list --cwd <dir>`. If the Owner already created a session for that project (or gave you its UUID), bind it once via `hive-bridge bus slot-set --cwd <dir> --runtime codex --role <role> --thread <uuid>` and reuse it!
   - For **Claude Desktop Remote**, check `hive-bridge bus workers` and `hive-bridge claude list` first. Dispatch by posting to the worker's seat name (e.g., `--to claude` or `--to claude-ui`), **never** by calling `hive-bridge claude new` unless the Owner explicitly requested headless CLI.
2. **Prevent session sprawl.** Use `hive-bridge run` (reuses pinned slots), `--ephemeral` for one-off Codex runs, `hive-bridge rotate` to refresh heavy slots cleanly, and `hive-bridge gc` to sweep retired/ephemeral sessions.
3. **Permissions are host-profile driven.** Pass through the Owner's configured defaults in `~/.config/hive-bridge/config.json` (default: full access for automated slots). Existing sessions keep their own permissions.
4. **Ask the Owner before choosing or changing Codex model / reasoning effort.** Never guess when creating a new Codex session or changing an existing one's `--model` / `--effort`.
5. **Never blindly trust self-reports.** Always verify `summary.json` (`status`, `commands` exit codes) and remote `git diff` before reporting completion.
6. **No polling loops.** Run long `hive-bridge run`, `rotate`, or `bus wait` calls as background commands (`WaitMsBeforeAsync: 500`) and wait for the completion notification.

## Quick Command Reference

Pass `--host <alias>` to target a non-default remote host from `~/.config/hive-bridge/config.json`.

### 1. Health, Hosts & Seated Workers
```bash
hive-bridge hosts
hive-bridge doctor [--host <alias>]
hive-bridge bus workers          # See which pre-opened Claude/Codex seats are waiting
hive-bridge claude list          # See live remote Claude Desktop Remote & CLI sessions
hive-bridge codex list --cwd /path/to/project
```

### 2. Dispatching to Pre-Opened Claude Desktop Remote (Live-Visible!)
```bash
# 1. Check seated workers (e.g. claude or claude-ui in 'waiting' status)
hive-bridge bus workers

# 2. Send task to the seated Claude Desktop Remote window (wakes it immediately if waiting!)
hive-bridge bus post --from antigravity --to claude --topic ui-design \
  --cwd /path/to/remote/project --body-file /local/prompt.md

# 3. Wait cleanly in background for Claude's reply on the board (zero polling!)
hive-bridge bus wait --for antigravity --topic ui-design --timeout 600

# 4. Acknowledge once processed
hive-bridge bus ack <msg_id> --by antigravity
```

### 3. Dispatching to Remote Codex (Live-Visible in Codex Desktop!)
```bash
# Bind an Owner-created Codex session UUID to a reusable slot (recommended!)
hive-bridge bus slot-set --cwd /path/to/project --runtime codex --role coder --thread <uuid>

# Dispatch to the pinned slot (reuses existing session; only creates if slot is empty)
hive-bridge run --runtime codex --cwd /path/to/project --role coder --prompt-file prompt.md

# Mid-turn steer or interrupt
hive-bridge codex steer --thread <uuid> --turn <turn-id> --text "Adjust step 3..."
hive-bridge codex interrupt --thread <uuid> --turn <turn-id>

# Rotate a heavy slot cleanly / clean up ephemeral & rotated sessions
hive-bridge rotate --runtime codex --cwd /path/to/project --role coder --delete-old
hive-bridge gc [--apply]
```

### 4. Evidence & Remote Git Verification
```bash
hive-bridge bus runs --limit 10
hive-bridge bus evidence --out /remote/path/to/run-dir
hive-bridge bus git-diff --cwd /path/to/remote/project
```
