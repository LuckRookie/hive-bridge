---
name: hive-bridge
description: >-
  Read and post updates on the shared hive-bridge SQLite blackboard (`hive-relay-bus`)
  to coordinate with local Antigravity and remote Claude sessions on the same server.
---

# Hive Bridge (Remote Codex Adapter)

You share a local SQLite blackboard at `~/.local/bin/hive-relay-bus` with **Antigravity** (running on the Owner's local machine) and **Claude** (running on this server).

## Commands

```bash
# Check messages addressed to codex
~/.local/bin/hive-relay-bus inbox --for codex --unread

# Mark messages read
~/.local/bin/hive-relay-bus ack <id...> --by codex

# Post review findings, progress updates, or questions back to Antigravity / Claude
~/.local/bin/hive-relay-bus post --from codex --to antigravity --topic <topic> \
  --cwd "$(pwd)" --text "Review findings: ..."

# Inspect full topic history
~/.local/bin/hive-relay-bus thread <topic>
```

## Guidelines

- Keep board posts concise and concrete: cite exact file paths, line numbers, command exit codes, or evidence directories.
- Never bypass sandbox or approval constraints; if a task requires elevated permissions, note it clearly in your reply and stop for the Owner.
