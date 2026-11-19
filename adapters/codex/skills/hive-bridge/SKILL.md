---
name: hive-bridge
description: >-
  Read and post updates on the shared hive-bridge SQLite blackboard (`hive-relay-bus`)
  to coordinate with local Antigravity and pre-opened remote Claude Desktop Remote
  sessions on the same server.
---

# Hive Bridge (Remote Codex Adapter)

You are running on the remote server inside Codex's shared `app-server` daemon (live-visible in the Owner's Codex Desktop app).
You share a stateless SQLite blackboard at `~/.local/bin/hive-relay-bus` with **local Antigravity** and **remote Claude** (typically running in the Owner's pre-opened Claude Desktop Remote window).

## Commands

```bash
# See which seated Claude/Codex workers are currently waiting on the board
~/.local/bin/hive-relay-bus workers

# Check unread messages addressed to codex
~/.local/bin/hive-relay-bus inbox --for codex --unread

# Mark messages acknowledged
~/.local/bin/hive-relay-bus ack <id...> --by codex

# Post review findings, progress updates, or handoffs back to Antigravity / Claude
~/.local/bin/hive-relay-bus post --from codex --to antigravity --topic <topic> \
  --cwd "$(pwd)" --text "Review findings: ..."

# Inspect full topic history
~/.local/bin/hive-relay-bus thread <topic>
```

## Guidelines

- Keep board posts concise and concrete: cite exact file paths, line numbers, command exit codes, or evidence directories.
- To hand off UI/design tasks to the Owner's live Claude Desktop Remote window, post `--to claude` (or the active seat name shown in `hive-relay-bus workers`) rather than spawning a separate headless `claude` CLI process.
- Avoid infinite peer loops: after at most 3 back-and-forth exchanges with another worker, post `--to antigravity` or `--to owner` for arbitration.
