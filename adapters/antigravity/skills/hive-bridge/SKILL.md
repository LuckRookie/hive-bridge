---
name: hive-bridge
description: >-
  Connect local Antigravity to remote SSH servers running Codex (shared app-server
  daemon, live-visible in Codex Desktop) and Claude (Desktop Remote or CLI) via a
  zero-daemon SSH CLI and shared SQLite blackboard. Use whenever dispatching work
  to remote Codex/Claude, checking the shared board/inbox, steering or interrupting
  a remote Codex turn, or verifying remote git diff and summary.json evidence.
---

# Hive Bridge — Local Antigravity ↔ Remote SSH Agents

`hive-bridge` coordinates **local Antigravity**, **remote Codex** (via its shared `app-server` Unix socket daemon so Codex Desktop stays 100% live-visible), and **remote Claude** (Desktop Remote or CLI) with **zero persistent remote HTTP services** and **no MCP tunnel fragility**.

All remote hosts are configured in `~/.config/hive-bridge/config.json` (never hardcoded). Local CLI wrapper: `~/.local/bin/hive-bridge`.

## Side-Pane Console

When working with remote sessions or the shared board, surface the native side-pane console pill (only once, when relevant):

`[Hive Bridge Console](sidecar://hive-bridge/console/)`

## Core Rules (Follow Strictly)

1. **Ask the Owner before choosing model or reasoning effort.** Before creating a new Codex session or changing an existing session's `--model` / `--effort`, ask the Owner which model and effort level to use. For an existing session that does not need a change, omit `--model` and `--effort`.
2. **Never blindly trust self-reports.** Codex/Claude replies are claims, not proof. Always verify `summary.json` (`status`, `commands` exit codes, `approvalsDeclined`) and remote `git diff` before reporting completion to the Owner.
3. **Never escalate sandboxes silently.** Default Codex sandbox is `workspace-write` (use `read-only` for pure review/discussion). Escalation approvals are automatically declined and recorded in `approvalsDeclined`; if something needs elevated permissions, ask the Owner to handle it in Codex Desktop.
4. **One session per project.** Keep each remote project mapped to its own Codex session UUID (check `hive-bridge codex list --cwd <dir>` first; if ambiguous, ask the Owner rather than guessing).
5. **No polling loops for long runs.** Launch `codex new` / `codex send` / `claude new` / `claude send` as a background command (`WaitMsBeforeAsync: 500`) and wait for the system completion notification.

## Quick Command Reference

Pass `--host <alias>` to target a non-default remote host from `~/.config/hive-bridge/config.json`.

### 1. Health & Hosts
```bash
hive-bridge hosts
hive-bridge doctor [--host <alias>]
```

### 2. Remote Codex (Live-Visible in Codex Desktop)
```bash
# List sessions for a remote project directory
hive-bridge codex list --cwd /path/to/remote/project

# Create a new Codex session (background this for real tasks!)
hive-bridge codex new --cwd /path/to/remote/project --name "short-task-name" \
  --model <model> --effort <effort> --sandbox workspace-write \
  --prompt-file /local/or/remote/prompt.md

# Send a follow-up turn to an existing Codex session
hive-bridge codex send --thread <uuid> --prompt-file /local/or/remote/prompt.md

# Mid-turn steer or interrupt (get turnId from turn.json or turn_started stdout)
hive-bridge codex steer --thread <uuid> --turn <turn-id> --text "Adjust step 3..."
hive-bridge codex interrupt --thread <uuid> --turn <turn-id>
```
- If `--prompt-file` points to a local file on your Mac, `hive-bridge` automatically streams it over SSH stdin — no manual SCP needed.
- If `--out` is omitted, `hive-bridge` auto-creates `~/.hive-bridge/runs/codex-<timestamp>-<rand>` on the remote host and records it in the Evidence index.

### 3. Shared Blackboard / Inbox (Three-Way Interop with Claude & Codex)
```bash
# Post a task or message for remote Claude (Desktop Remote or CLI) or Codex
hive-bridge bus post --from antigravity --to claude --topic ui-redesign \
  --cwd /path/to/remote/project --text "Please design the settings panel..."

# Read unread messages addressed to Antigravity
hive-bridge bus inbox --for antigravity --unread

# Acknowledge processed messages
hive-bridge bus ack <msg_id> --by antigravity

# View full chronological topic thread
hive-bridge bus thread ui-redesign
```

### 4. Optional Headless Remote Claude CLI
Use this only when the Owner wants unattended Claude execution instead of collaborating with an open Claude Desktop Remote window via the shared board:
```bash
hive-bridge claude new --cwd /path/to/remote/project --prompt-file prompt.md
hive-bridge claude send --thread <claude-session-uuid> --cwd /path/to/remote/project --prompt-file followup.md
```

### 5. Evidence & Remote Git Verification
```bash
# List recent runs (both Codex & Claude)
hive-bridge bus runs --limit 10

# Inspect summary.json, turn.json, and events tail
hive-bridge bus evidence --out /home/user/.hive-bridge/runs/codex-...

# Inspect actual remote git status & diff
hive-bridge bus git-diff --cwd /path/to/remote/project
```
