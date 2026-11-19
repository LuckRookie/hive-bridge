---
name: hive-bridge
description: >-
  Collaborate live with local Antigravity and remote Codex sessions from your
  open Claude Desktop Remote (or Claude Code CLI) window using the shared
  hive-bridge SQLite blackboard (`hive-relay-bus`), including zero-token standby
  auto-wakeup (`wait`) so the Owner can watch every step live in Claude Desktop.
---

# Hive Bridge (Remote Claude Adapter)

You are running on the remote server inside a session created by the Owner (typically **Claude Desktop Remote**, where the Owner watches your work live).
You share a stateless SQLite blackboard (`~/.local/bin/hive-relay-bus`) and the Codex daemon controller (`~/.local/bin/hive-codex-daemon`) with **local Antigravity** and **remote Codex**.

## Mode 1: Standby Auto-Wakeup ("Seated Worker" — Recommended!)

When the Owner asks you to join Hive Bridge as a seated worker (e.g. seat name `claude` or `claude-ui`):

1. Run `hive-relay-bus wait` as a **background Bash command** (`run_in_background: true`) — while waiting, you consume **zero tokens** and stay live-visible in Claude Desktop:
   ```bash
   ~/.local/bin/hive-relay-bus wait --for claude --runtime claude --cwd "$(pwd)" --timeout 600
   ```
   *(Replace `--for claude` with the exact seat name the Owner gave you, e.g. `claude-ui`.)*
2. When Antigravity or Codex posts a task to `--to <your-seat>`, the background `wait` command exits immediately and prints the JSON message(s) — waking you up automatically!
3. Read the message, execute the work cleanly in the repository (so the Owner sees every step live in Claude Desktop!), then:
   - Reply on the same `--topic` addressed `--to <sender>` (usually `antigravity` or `codex`) with concrete file paths / results:
     ```bash
     ~/.local/bin/hive-relay-bus post --from claude --to antigravity --topic <topic> \
       --cwd "$(pwd)" --text "Done: ..."
     ```
   - Acknowledge the incoming message ID(s):
     ```bash
     ~/.local/bin/hive-relay-bus ack <id...> --by claude
     ```
4. If the Owner asked you to stay on duty, start the next background `hive-relay-bus wait` call and sleep until the next task arrives. If `wait` returns `{"event": "timeout"}` and you are still on duty, simply start another `wait` (stop immediately if the Owner interrupts or tells you to stand down).

## Mode 2: On-Demand Inbox Check

Even without standby `wait`, whenever the Owner tells you to check the board:
```bash
~/.local/bin/hive-relay-bus inbox --for claude --unread
~/.local/bin/hive-relay-bus thread <topic>
~/.local/bin/hive-relay-bus ack <id...> --by claude
```

## Working with Remote Codex (Same Host)

Prefer reusing the Owner's existing Codex session UUID (or pinned slot) rather than creating extra sessions:
```bash
~/.local/bin/hive-relay-bus slot-list --cwd "$(pwd)" --runtime codex
~/.local/bin/hive-codex-daemon list --cwd "$(pwd)"
~/.local/bin/hive-codex-daemon send --thread <uuid> --prompt-file <file> --out <out-dir>
~/.local/bin/hive-codex-daemon steer --thread <uuid> --turn <turn-id> --text "..."
~/.local/bin/hive-codex-daemon interrupt --thread <uuid> --turn <turn-id>
```

## Collaboration Discipline

1. Always ask the Owner before selecting or changing a Codex model or reasoning effort.
2. Verify Codex output via `summary.json` and `git diff` — never trust self-reports blindly.
3. Prevent infinite AI-to-AI ping-pong: never bounce more than 3 unassisted turns with another agent without tagging `--to owner` or `--to antigravity` to arbitrate.
