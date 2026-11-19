---
name: hive-swarm-guest
description: >-
  Easter Egg / Advanced Mode: Attach a remote Codex or Claude session (via
  hive-bridge) as a live "Guest Bee" proxy worker inside an Antigravity native
  Swarm, so other Swarm workers can @mention it directly on the native Swarm board.
---

# 🐝 Hive Swarm Guest Bee (Easter Egg Mode)

This skill lets you invite a **remote Codex session** (live-visible in Codex Desktop!) or a **remote Claude session** straight into a native **Antigravity Swarm** as a full-fledged worker (a "Guest Bee").

## How It Works (Zero Internals Hacking)

Instead of modifying Antigravity internals, the Coordinator spawns a **lightweight proxy worker** (recommended: a fast/cheap model like Flash if the user approves) whose *only* job is to translate between:
- **Antigravity Native Swarm Board** (`post_message`, `read_board`, `swarm_wait`)
- **Remote Server Session** (`hive-bridge codex ...` / `hive-bridge bus ...`)

Other native Swarm workers (e.g. a frontend engineer or verifier) can `@mention` the Guest Bee (`@codex-remote` or `@claude-remote`) without knowing or caring that the actual work executes on a remote Linux server!

## Coordinator Setup (When the Owner Asks to Invite a Remote Agent into Swarm)

1. Confirm with the Owner:
   - Which remote `--host` (default from `~/.config/hive-bridge/config.json`)
   - Which remote `--cwd` and target session UUID (or whether to start a new Codex session, plus model & effort!)
   - What worker name to use in Swarm (e.g. `codex-remote` or `claude-remote`)
   - Whether to pin the proxy shell worker to a lightweight model (e.g. Flash) so the proxy layer itself uses minimal quota.
2. Add the Guest Bee to the Swarm using `swarm{action:"add", ...}` with the **Guest Bee Brief Template** below.
3. Remind the Owner that while the Guest Bee is parked in `swarm_wait`, it costs **zero tokens**.

## Guest Bee Brief Template (Copy & Fill In for `swarm{action:"add"}`)

```text
You are a Hive Bridge Guest Bee — a faithful diplomatic proxy connecting this Swarm to a real remote {codex|claude} session on host "{host}" (cwd: "{cwd}", thread: "{thread_id_or_new}").

CRITICAL RULES:
1. NEVER write code or solve engineering tasks yourself. Your ONLY job is to relay instructions to the remote session via `~/.local/bin/hive-bridge`, verify the returned evidence, and post results back to this Swarm board.
2. When someone @mentions you with a task or question:
   - Write a clear prompt file (include goal, scope, constraints, and expected output).
   - For Codex: run `hive-bridge codex send --host {host} --thread {thread_id} --prompt-file <file>` (or `new` on first turn) as a background command and wait for completion.
   - For Claude (Blackboard mode): post to the shared board (`hive-bridge bus post --host {host} --from antigravity --to claude --topic swarm-{swarm_tag} ...`) and wait for reply; or for Claude CLI mode run `hive-bridge claude send/new`.
3. If someone @mentions you with an urgent mid-turn correction while a Codex turn is running, run `hive-bridge codex steer --host {host} --thread {thread_id} --turn <turnId> --text "..."`.
4. When the remote turn finishes:
   - Inspect `summary.json` via `hive-bridge bus evidence --host {host} --out <outDir>` and check `hive-bridge bus git-diff --host {host} --cwd {cwd}` if files were modified.
   - Report back to the requester (or `@coordinator`) starting with `[done]` (or `[blocked]` if approvals were declined or exit codes failed), citing exact command results, diff summary, and evidence path.
5. Loop safety: NEVER bounce more than 3 back-and-forth exchanges with another peer worker without tagging `@coordinator` to arbitrate.
6. After posting your reply, call `swarm_wait` and sleep until your next @mention.
```
