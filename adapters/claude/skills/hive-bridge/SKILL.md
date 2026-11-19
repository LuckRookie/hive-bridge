---
name: hive-bridge
description: >-
  Collaborate with local Antigravity and remote Codex sessions through the shared
  hive-bridge SQLite blackboard (`hive-relay-bus`) and Codex app-server daemon
  (`hive-codex-daemon`). Use whenever checking inbox messages from Antigravity/Codex,
  posting handoffs or design updates, or dispatching tasks to Codex.
---

# Hive Bridge (Remote Claude Adapter)

You are running on the remote server (via Claude Desktop Remote or Claude Code CLI).
You share a stateless SQLite blackboard (`~/.local/bin/hive-relay-bus`) and the Codex
daemon driver (`~/.local/bin/hive-codex-daemon`) with **local Antigravity** and **remote Codex**.

## Shared Blackboard Commands

```bash
# Check unread messages addressed to you (or 'all')
~/.local/bin/hive-relay-bus inbox --for claude --unread

# Acknowledge messages once handled
~/.local/bin/hive-relay-bus ack <id...> --by claude

# Reply or hand off work to Antigravity, Codex, or everyone
~/.local/bin/hive-relay-bus post --from claude --to antigravity --topic <topic> \
  --cwd "$(pwd)" --text "Done: updated UI components..."

# Read a full discussion thread
~/.local/bin/hive-relay-bus thread <topic>
```

## Working with Remote Codex (Same Host)

```bash
~/.local/bin/hive-codex-daemon list --cwd "$(pwd)"
~/.local/bin/hive-codex-daemon new --cwd "$(pwd)" --name "<short-name>" \
  --model <model> --effort <effort> --prompt-file <file> --out <out-dir>
~/.local/bin/hive-codex-daemon send --thread <uuid> --prompt-file <file> --out <out-dir>
~/.local/bin/hive-codex-daemon steer --thread <uuid> --turn <turn-id> --text "..."
~/.local/bin/hive-codex-daemon interrupt --thread <uuid> --turn <turn-id>
```

## Collaboration Discipline

1. Always ask the Owner before selecting or changing a Codex model or reasoning effort.
2. When finishing a task from the blackboard, post a concise summary plus the exact changed file paths or evidence directory back to the same `--topic`, then `ack` the incoming message.
3. Verify Codex output via `summary.json` and `git diff` — never take prose claims at face value.
4. Do not trigger infinite peer loops with Antigravity/Codex; if blocked or facing an architectural decision, address the message `--to owner` or `--to antigravity` and pause.
