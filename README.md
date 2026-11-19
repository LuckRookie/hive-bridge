# Hive Bridge

**Zero-daemon, three-way bridge connecting local AI orchestrators ([Google Antigravity](https://antigravity.google), local Claude Code, or local Codex) with local and remote SSH servers running OpenAI Codex and Claude.**

- **No remote HTTP daemons, no fragile SSH port forwarding, no MCP disconnects after laptop sleep.** Everything rides over multiplexed OpenSSH (`ControlMaster`) and a stateless SQLite blackboard (`WAL` mode) — plus first-class `local` host support with zero SSH overhead.
- **Native Antigravity Side-Pane `Live` Stream Console.** Watch both **Claude** (`stream-json` partial `text_delta` + collapsible tool calls) and **Codex** (`item/agentMessage/delta` + commands/diffs) stream in real time right inside Antigravity's side pane, with direct follow-up chat, mid-turn **Steer**, **Interrupt**, **Git Diff**, and **Relay to Chat**.
- **Remote Codex Desktop Stays 100% Live-Visible.** Connects directly to Codex's shared Linux `app-server` Unix domain socket (`/tmp/codex-daemon-<uid>/*`) with `threadSource: "user"`.
- **Anti-Sprawl Session Governance (`run` / `slots` / `rotate` / `gc`).** Bind one persistent slot per `(project, runtime, role)`, rotate cleanly with an automatic handoff packet when context gets heavy, and garbage-collect rotated or ephemeral sessions without touching pinned slots.
- **Host-Configured Permission Pass-Through.** Configure your preferred sandbox/permission defaults once per host in `~/.config/hive-bridge/config.json` (defaults to full access for automated slots; fully editable per host).
- **🐝 Easter Egg — Swarm Guest Bee.** Invite a remote Codex or Claude session straight into an Antigravity native **Swarm** as a proxy worker so other Swarm bees can `@mention` it directly.

---

## Runtime Matrix & Known Limitations (Verified Behavior)

Different runtimes and Desktop apps have fundamentally different process architectures. Keep these verified facts in mind when designing your workflow:

| Target Environment | Automation Control | Live in Hive Bridge `Live` Panel? | Live in Vendor Desktop App? | Notes & Prerequisites |
| :--- | :--- | :---: | :---: | :--- |
| **Remote Linux Codex** (shared `app-server` daemon) | Full (`run`, `send`, `steer`, `interrupt`, `rotate`, `gc`) | **Yes** | **Yes (100% live)** | Connects to `/tmp/codex-daemon-<uid>/*` with `threadSource: "user"`. Requires remote Python with `websockets >= 13` (auto-detected by `install.sh`). |
| **Local macOS Codex** | `codex_daemon.py` (via bundled `app-server` stdio) or `codex queue` | **Yes** | **Only via `codex queue`** on an already-open window | macOS `ChatGPT.app` spawns its own private stdio `app-server` rather than a shared Unix socket. External `app-server` runs stream live in Hive Bridge `Live` panel but do not live-sync into the Desktop sidebar; `codex queue --thread <uuid>` delivers directly into an open, idle Desktop session. |
| **Remote / Local Claude Code CLI** (`claude -p --resume`) | Full (`run`, `send`, `rotate`) | **Yes (token-by-token live stream)** | **No (restart/reload Desktop to see appended turns)** | Shares the exact same `~/.claude/projects/<cwd>/<uuid>.jsonl` transcripts as Claude Desktop (including sessions originally created in Claude Desktop). **Prerequisite:** `claude` CLI on that host must be authenticated (`claude` → `/login`, or configured API gateway env vars). |
| **Remote Claude Desktop Remote** (GUI SSH session, no CLI auth) | Blackboard only (`bus post` / `inbox` / `wait`) | Board messages only | **Yes** (inside the open Desktop window) | Claude Desktop Remote owns its remote `ccd-cli` stdio pipe and forwards auth from macOS. External scripts **cannot** directly inject prompts into an open Claude Desktop Remote process without either (a) logging in remote `claude` CLI and viewing live output in Hive Bridge's `Live` panel, or (b) letting the open window pull from the shared blackboard. |

### Key Takeaways
1. **Why remote Claude automation requires Claude Code CLI:** External tools cannot hijack a Claude Desktop Remote window's private stdio stream or macOS-forwarded auth. Authenticate `claude` CLI on the remote server once (via `/login` using your existing Claude subscription, or your API env vars), and use **Hive Bridge's `Live` panel** as your live viewport instead of waiting on Claude Desktop to refresh.
2. **Transcripts are still shared:** Running `hive-bridge claude send --thread <uuid>` against a session originally opened in Claude Desktop appends directly to the same `.jsonl` history — restarting or reopening Claude Desktop later will show the full conversation.
3. **Prevent session sprawl:** Always prefer `hive-bridge run --cwd <dir> --role <role>` (reuses pinned slots) over repeatedly creating new threads; tag one-off tasks with `--ephemeral` and clean them with `hive-bridge gc --apply`.

---

## Architecture

```text
Local Machine (Antigravity / Claude Code)      Remote SSH Server (any host)
┌──────────────────────────────────┐           ┌──────────────────────────────────────┐
│ Antigravity Plugin (hive-bridge) │           │ ~/.local/bin/hive-relay-bus (SQLite) │
│  • SKILL.md + Swarm Guest Bee    │  OpenSSH  │   ├─ Shared Board / Inbox / Workers  │
│  • Sidecar Console (Aux Pane)    │◄─────────►│   ├─ Pinned Slots & Lifecycle Meta   │
│    └─ Live / Board / Codex /     │ Control-  │   └─ Byte-Offset Event Streaming     │
│       Claude / Evidence          │  Master   │                                      │
│  • ~/.local/bin/hive-bridge      │           │ ~/.local/bin/hive-codex-daemon       │
│  • ~/.config/hive-bridge/        │           │   └─ /tmp/codex-daemon-<uid>/*       │
│      config.json (host profiles) │           │      (live in Codex Desktop!)        │
│  • Built-in 'local' host profile │           │                                      │
└──────────────────────────────────┘           │ ~/.local/bin/hive-claude-runner      │
                                               │ Remote Skills:                       │
                                               │  • ~/.claude/skills/hive-bridge      │
                                               │  • ~/.codex/skills/hive-bridge       │
                                               └──────────────────────────────────────┘
```

## Quick Start

```bash
git clone https://github.com/LuckRookie/hive-bridge.git
cd hive-bridge

# 1. Install locally (links CLIs + Antigravity plugin/console + registers 'local' host + detects local Claude/Codex)
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
    "local": {
      "transport": "local",
      "ssh": "local",
      "default_cwd": "/Users/you",
      "codex_sandbox": "danger-full-access",
      "codex_approval_policy": "never",
      "claude_permission_mode": "bypassPermissions"
    },
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
hive-bridge slots [--host local|my-dev-server]
hive-bridge run --host my-dev-server --runtime codex --cwd /path/to/repo --role coder --prompt-file prompt.md
hive-bridge run --host local --runtime claude --cwd /path/to/repo --role frontend --prompt-file ui.md

# Ephemeral one-off session (tagged for GC, never touches pinned slots)
hive-bridge run --runtime codex --cwd /path/to/repo --role review --ephemeral --prompt-file review.md

# Clean rotation (builds handoff packet from latest summary + git status -> opens fresh slot -> retires old)
hive-bridge rotate --runtime codex --cwd /path/to/repo --role coder --delete-old

# Safe garbage collection (dry-run by default; never deletes pinned slots or active turns)
hive-bridge gc
hive-bridge gc --apply

# Mid-turn steer, interrupt & live discovery
hive-bridge codex list --cwd /path/to/repo
hive-bridge claude list [--all]
hive-bridge codex steer --thread <uuid> --turn <turn-id> --text "Focus on tests first"
hive-bridge codex interrupt --thread <uuid> --turn <turn-id>

# Shared Three-Way Blackboard (Antigravity ↔ Claude ↔ Codex)
hive-bridge bus post --from antigravity --to claude --topic ui --text "Please review layout"
hive-bridge bus inbox --for antigravity --unread
hive-bridge bus ack <id> --by antigravity
hive-bridge bus thread ui

# Live Byte-Offset Stream, Evidence & Remote Git Diff
hive-bridge bus runs --limit 10
hive-bridge bus stream --out /path/to/run-dir --offset 0
hive-bridge bus evidence --out /path/to/run-dir
hive-bridge bus git-diff --cwd /path/to/repo
```

## 🐝 Easter Egg: Swarm Guest Bee

Load the bundled `hive-swarm-guest` skill inside Antigravity to wrap any remote Codex or Claude session in a lightweight proxy worker ("Guest Bee") inside a native Antigravity Swarm. Other Swarm workers can `@mention` `@codex-remote` directly on the native Swarm board, while execution runs live on your remote server and streams into both Codex Desktop and the Hive Bridge `Live` panel.

## License

MIT
