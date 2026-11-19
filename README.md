# Hive Bridge

**Zero-daemon, three-way bridge connecting local [Google Antigravity](https://antigravity.google) with remote SSH servers running OpenAI Codex (live Desktop-visible) and Claude (Desktop Remote or CLI).**

- **No remote HTTP daemons, no fragile SSH port forwarding, no MCP disconnects after laptop sleep.** Everything rides over multiplexed OpenSSH (`ControlMaster`) and a stateless SQLite blackboard (`WAL` mode).
- **Codex Desktop stays 100% live-visible.** Connects directly to Codex's shared `app-server` Unix domain socket (`/tmp/codex-daemon-<uid>/*`) with `threadSource: "user"`.
- **Native Antigravity Side-Pane Console.** Includes a built-in Antigravity Sidecar UI (`Board` / `Codex` / `Evidence & Git Diff`) that lives right next to your chat, with one-click mid-turn **Steer**, **Interrupt**, and **Relay to Chat**.
- **Unified Three-Way Adapters.** One installer wires up Antigravity (local plugin), Claude (remote `~/.claude/skills/hive-bridge`), and Codex (remote `~/.codex/skills/hive-bridge`).
- **🐝 Easter Egg — Swarm Guest Bee.** Invite a remote Codex or Claude session straight into an Antigravity native **Swarm** as a proxy worker so other Swarm bees can `@mention` it directly.

---

## Architecture

```text
Local Machine (Antigravity)                    Remote SSH Server (any host)
┌──────────────────────────────────┐           ┌──────────────────────────────────────┐
│ Antigravity Plugin (hive-bridge) │           │ ~/.local/bin/hive-relay-bus (SQLite) │
│  • SKILL.md + Swarm Guest Bee    │  OpenSSH  │   └─ Shared Board / Inbox / Evidence │
│  • Sidecar Console (Aux Pane)    │◄─────────►│                                      │
│  • ~/.local/bin/hive-bridge      │ Control-  │ ~/.local/bin/hive-codex-daemon       │
│  • ~/.config/hive-bridge/        │  Master   │   └─ /tmp/codex-daemon-<uid>/*       │
│      config.json (host profiles) │           │      (live in Codex Desktop!)        │
└──────────────────────────────────┘           │                                      │
                                               │ Remote Skills:                       │
                                               │  • ~/.claude/skills/hive-bridge      │
                                               │  • ~/.codex/skills/hive-bridge       │
                                               └──────────────────────────────────────┘
```

## Quick Start

```bash
git clone https://github.com/LuckRookie/hive-bridge.git
cd hive-bridge

# 1. Install locally (links CLI + Antigravity plugin & side-pane console)
./install.sh local

# 2. Deploy to any passwordless SSH host (e.g. my-dev-server)
./install.sh remote my-dev-server

# 3. Verify everything is green
./install.sh doctor my-dev-server
```

Add more servers anytime by running `./install.sh remote <another-ssh-host>`. No hostnames or paths are hardcoded in the repository; all profiles live in `~/.config/hive-bridge/config.json`.

### Optional: Claude Code Inbox Hook (Remote)
To get an automatic one-line unread blackboard reminder before every prompt in remote Claude Code / Claude Desktop Remote, add `~/.hive-bridge/claude_check_inbox.sh` to your remote Claude `UserPromptSubmit` hook configuration.

## Core Commands

```bash
# Remote Codex (live in Codex Desktop)
hive-bridge codex list --cwd /path/to/repo
hive-bridge codex new --cwd /path/to/repo --name "api-refactor" --model <model> --effort <effort> --prompt-file prompt.md
hive-bridge codex send --thread <uuid> --prompt-file followup.md
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
