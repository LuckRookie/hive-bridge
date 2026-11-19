# Hive Bridge

**Zero-daemon, three-way bridge connecting local AI orchestrators ([Google Antigravity](https://antigravity.google), local Claude Code, or local Codex) with remote SSH servers running OpenAI Codex (live Desktop-visible) and Claude (Desktop Remote or CLI).**

- **No remote HTTP daemons, no fragile SSH port forwarding, no MCP disconnects after laptop sleep.** Everything rides over multiplexed OpenSSH (`ControlMaster`) and a stateless SQLite blackboard (`WAL` mode).
- **Codex Desktop stays 100% live-visible.** Connects directly to Codex's shared `app-server` Unix domain socket (`/tmp/codex-daemon-<uid>/*`) with `threadSource: "user"`.
- **Anti-Sprawl Session Governance (`run` / `slots` / `rotate` / `gc`).** Bind one persistent slot per `(project, runtime, role)`, rotate cleanly with an automatic handoff packet when context gets heavy, and garbage-collect rotated or ephemeral sessions without touching pinned slots.
- **Host-Configured Permission Pass-Through.** Configure your preferred sandbox/permission defaults once per host in `~/.config/hive-bridge/config.json` (defaults to full access for automated slots; fully editable per host).
- **Native Antigravity Side-Pane Console.** Built-in Sidecar UI (`Board` / `Codex & Slots` / `Evidence & Git Diff`) with one-click **Rotate**, **GC Preview/Clean**, mid-turn **Steer**, **Interrupt**, and **Relay to Chat**.
- **🐝 Easter Egg — Swarm Guest Bee.** Invite a remote Codex or Claude session straight into an Antigravity native **Swarm** as a proxy worker so other Swarm bees can `@mention` it directly.

---

## Architecture

```text
Local Machine (Antigravity / Claude Code)      Remote SSH Server (any host)
┌──────────────────────────────────┐           ┌──────────────────────────────────────┐
│ Antigravity Plugin (hive-bridge) │           │ ~/.local/bin/hive-relay-bus (SQLite) │
│  • SKILL.md + Swarm Guest Bee    │  OpenSSH  │   ├─ Shared Board / Inbox / Evidence │
│  • Sidecar Console (Aux Pane)    │◄─────────►│   └─ Pinned Slots & Lifecycle Meta   │
│  • ~/.local/bin/hive-bridge      │ Control-  │                                      │
│  • ~/.config/hive-bridge/        │  Master   │ ~/.local/bin/hive-codex-daemon       │
│      config.json (host profiles) │           │   └─ /tmp/codex-daemon-<uid>/*       │
│  • Auto-links local Claude/Codex │           │      (live in Codex Desktop!)        │
└──────────────────────────────────┘           │                                      │
                                               │ ~/.local/bin/hive-claude-runner      │
                                               │ Remote Skills:                       │
                                               │  • ~/.claude/skills/hive-bridge      │
                                               │  • ~/.codex/skills/hive-bridge       │
                                               └──────────────────────────────────────┘
```

## Quick Start

```bash
git clone https://github.com/LuckRookie/hive-bridge.git
cd hive-bridge

# 1. Install locally (links CLI + Antigravity plugin/console + detects local Claude/Codex)
./install.sh local

# 2. Deploy to any passwordless SSH host (e.g. my-dev-server)
./install.sh remote my-dev-server

# 3. Verify everything is green
./install.sh doctor my-dev-server
```

All host profiles and permission defaults live locally in `~/.config/hive-bridge/config.json` (never committed to git):

```json
{
  "default_host": "my-dev-server",
  "hosts": {
    "my-dev-server": {
      "ssh": "my-dev-server",
      "codex_python": "/path/to/python3",
      "default_cwd": "/home/user",
      "codex_sandbox": "danger-full-access",
      "codex_approval_policy": "never",
      "claude_permission_mode": "bypassPermissions"
    }
  }
}
```

## Core Commands

```bash
# Slot-based dispatch (reuses pinned session per cwd + runtime + role — prevents session sprawl!)
hive-bridge slots
hive-bridge run --runtime codex --cwd /path/to/repo --role coder --prompt-file prompt.md
hive-bridge run --runtime claude --cwd /path/to/repo --role frontend --prompt-file ui.md

# Ephemeral one-off session (tagged for GC, never touches pinned slots)
hive-bridge run --runtime codex --cwd /path/to/repo --role review --ephemeral --prompt-file review.md

# Clean rotation (builds handoff packet from latest summary + git status -> opens fresh slot -> retires old)
hive-bridge rotate --runtime codex --cwd /path/to/repo --role coder --delete-old

# Safe garbage collection (dry-run by default; never deletes pinned slots or active turns)
hive-bridge gc
hive-bridge gc --apply

# Mid-turn steer & interrupt
hive-bridge codex list --cwd /path/to/repo
hive-bridge codex steer --thread <uuid> --turn <turn-id> --text "Focus on tests first"
hive-bridge codex interrupt --thread <uuid> --turn <turn-id>

# Shared Three-Way Blackboard (Antigravity ↔ Claude ↔ Codex)
hive-bridge bus post --from antigravity --to claude --topic ui --text "Please review layout"
hive-bridge bus inbox --for antigravity --unread
hive-bridge bus ack <id> --by antigravity
hive-bridge bus thread ui

# Evidence & Remote Git Diff
hive-bridge bus runs --limit 10
hive-bridge bus evidence --out /remote/path/to/run-dir
hive-bridge bus git-diff --cwd /path/to/repo
```

## 🐝 Easter Egg: Swarm Guest Bee

Load the bundled `hive-swarm-guest` skill inside Antigravity to wrap any remote Codex or Claude session in a lightweight proxy worker ("Guest Bee") inside a native Antigravity Swarm. Other Swarm workers can `@mention` `@codex-remote` directly on the native Swarm board, while execution runs live on your remote server and streams into Codex Desktop.

## License

MIT
