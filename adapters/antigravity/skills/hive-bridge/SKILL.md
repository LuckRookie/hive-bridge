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

## Operating Model & Known Constraints (READ FIRST!)

Different runtimes have different Desktop visibility and control mechanics. **Follow this verified matrix so you always pick the right channel:**

| Target Agent | Transport & Control | Visible Live Where? | Key Constraint / Prerequisite |
| :--- | :--- | :--- | :--- |
| **Remote Linux Codex** | Shared `codex app-server` Unix socket (`threadSource: user`) | **Codex Desktop + Hive Bridge `Live` panel** | Best full-control target (`run`/`send`/`steer`/`interrupt`/`rotate`/`gc`). |
| **Local macOS Codex** | Bundled `ChatGPT.app` `codex` (stdio `app-server`) or `codex queue` | `app-server` → **Hive Bridge `Live` panel**; `codex queue` → **open Codex Desktop window** | macOS Desktop uses a private stdio `app-server` (no shared Unix socket), so external `app-server` runs do not live-sync into Desktop sidebar; `codex queue --thread <uuid>` delivers to an already-open, idle Desktop window. |
| **Remote / Local Claude Code CLI** (`--runtime claude`) | `claude -p --output-format stream-json --include-partial-messages --resume` | **Hive Bridge `Live` panel (real-time token stream)** | **Requires `claude` CLI auth on that host** (`/login` or API env vars). Shares `~/.claude/projects/*.jsonl` with Claude Desktop, but Claude Desktop only shows appended turns after restarting/reopening. |
| **Remote Claude Desktop Remote** (unauthenticated remote CLI) | Shared SQLite blackboard (`bus post`/`inbox`/`wait`) | **Claude Desktop Remote window** | External scripts **cannot** inject directly into an open Claude Desktop Remote `ccd-cli` process (Desktop owns its stdio pipe and auth). Either authenticate remote `claude` CLI and watch in Hive Bridge's `Live` panel, or exchange tasks via the shared blackboard. |

### Golden Rules

1. **Prefer pinned slots and Owner-created sessions over creating stray threads.**
   - Check `hive-bridge slots` and `hive-bridge codex list --cwd <dir>` (or `claude list`) first. Bind existing UUIDs with `hive-bridge bus slot-set` so `hive-bridge run` reuses them instead of causing session sprawl.
   - For remote Claude: check whether remote `claude` CLI is authenticated before dispatching `hive-bridge run --runtime claude`; if it returns `Not logged in`, tell the Owner clearly (they can run `claude` → `/login` or configure API env vars on the remote host, or collaborate via `bus post` with an open Desktop Remote window).
2. **Prevent session sprawl.** Use `hive-bridge run` (reuses pinned slots), `--ephemeral` for one-off runs, `hive-bridge rotate` to refresh heavy slots cleanly, and `hive-bridge gc` to sweep retired/ephemeral sessions.
3. **Watch live in the Side-Pane Console.** Any run started via `hive-bridge run` / `codex` / `claude` streams incrementally to the `Live` tab (`bus stream --out <dir> --offset <N>`). Surface `[Hive Bridge](sidecar://hive-bridge/console/)` when helpful.
4. **Permissions are host-profile driven.** Pass through the Owner's configured defaults in `~/.config/hive-bridge/config.json` (default: full access for automated slots). Existing sessions keep their own permissions.
5. **Ask the Owner before choosing or changing Codex model / reasoning effort.** Never guess when creating a new Codex session or changing an existing one's `--model` / `--effort`.
6. **Never blindly trust self-reports.** Always verify `summary.json` (`status`, `commands` exit codes) and `git diff` before reporting completion.
7. **No polling loops.** Run long `hive-bridge run`, `rotate`, or `bus wait` calls as background commands (`WaitMsBeforeAsync: 500`) and wait for the completion notification.

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
