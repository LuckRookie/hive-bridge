#!/usr/bin/env bash
# Optional Claude Code UserPromptSubmit hook: prints a compact one-line alert
# if there are unread hive-bridge blackboard messages for Claude.
set -euo pipefail
BUS="${HOME}/.local/bin/hive-relay-bus"
if [ -x "$BUS" ]; then
  "$BUS" inbox --for claude --unread --brief 2>/dev/null || true
fi
