#!/usr/bin/env bash
# Hive Bridge installer (idempotent, zero hardcoded hosts).
#
# Usage:
#   ./install.sh local                  Install local CLI (~/.local/bin/hive-bridge) + Antigravity plugin & console
#   ./install.sh remote <ssh-host>      Deploy core tools + Claude/Codex skills to <ssh-host> and register in config
#   ./install.sh doctor [host]          Verify connectivity and remote components
#   ./install.sh uninstall local        Remove local Antigravity plugin link and CLI wrapper
#   ./install.sh uninstall remote <h>   Remove deployed binaries and skills from remote host (keeps SQLite DB)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN_DIR="${HOME}/.local/bin"
LOCAL_CONFIG_DIR="${HOME}/.config/hive-bridge"
LOCAL_CONFIG_FILE="${LOCAL_CONFIG_DIR}/config.json"
AGY_PLUGIN_DIR="${HOME}/.gemini/config/plugins/hive-bridge"
SSH_CM_PATH="${HOME}/.ssh/cm-hive-%C"

ssh_opts=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ControlMaster=auto
  -o "ControlPath=${SSH_CM_PATH}"
  -o ControlPersist=10m
)

info() { printf '[hive-bridge] %s\n' "$*"; }
die()  { printf '[hive-bridge] ERROR: %s\n' "$*" >&2; exit 1; }

chmod_scripts() {
  chmod +x \
    "${REPO_DIR}/install.sh" \
    "${REPO_DIR}/core/relay_bus.py" \
    "${REPO_DIR}/core/codex_daemon.py" \
    "${REPO_DIR}/core/claude_runner.py" \
    "${REPO_DIR}/local/hive_bridge.py" \
    "${REPO_DIR}/adapters/claude/hooks/check_inbox.sh"
}

install_local() {
  chmod_scripts
  mkdir -p "${LOCAL_BIN_DIR}" "${LOCAL_CONFIG_DIR}" "${HOME}/.gemini/config/plugins" "${HOME}/.ssh"

  ln -sfn "${REPO_DIR}/local/hive_bridge.py" "${LOCAL_BIN_DIR}/hive-bridge"
  info "Linked CLI -> ${LOCAL_BIN_DIR}/hive-bridge"

  if [ -e "${AGY_PLUGIN_DIR}" ] && [ ! -L "${AGY_PLUGIN_DIR}" ]; then
    die "${AGY_PLUGIN_DIR} already exists and is not a symlink; remove or back it up first."
  fi
  ln -sfn "${REPO_DIR}/adapters/antigravity" "${AGY_PLUGIN_DIR}"
  info "Linked Antigravity plugin + side-pane console -> ${AGY_PLUGIN_DIR}"

  # Also link local orchestrator skills for users who run Claude Code or Codex locally
  if [ -d "${HOME}/.claude" ]; then
    mkdir -p "${HOME}/.claude/skills"
    ln -sfn "${REPO_DIR}/adapters/antigravity/skills/hive-bridge" "${HOME}/.claude/skills/hive-bridge-local"
    info "Detected local ~/.claude -> linked local skill at ~/.claude/skills/hive-bridge-local"
  fi
  if [ -d "${HOME}/.codex" ]; then
    mkdir -p "${HOME}/.codex/skills"
    ln -sfn "${REPO_DIR}/adapters/antigravity/skills/hive-bridge" "${HOME}/.codex/skills/hive-bridge-local"
    info "Detected local ~/.codex -> linked local skill at ~/.codex/skills/hive-bridge-local"
  fi

  if [ ! -f "${LOCAL_CONFIG_FILE}" ]; then
    cat > "${LOCAL_CONFIG_FILE}" <<'EOF'
{
  "default_host": "",
  "hosts": {}
}
EOF
    info "Initialized config -> ${LOCAL_CONFIG_FILE}"
  else
    info "Kept existing config -> ${LOCAL_CONFIG_FILE}"
  fi

  case ":${PATH}:" in
    *":${LOCAL_BIN_DIR}:"*) ;;
    *) info "Tip: add ${LOCAL_BIN_DIR} to your PATH (e.g. export PATH=\"\$HOME/.local/bin:\$PATH\")" ;;
  esac
  info "Local install complete. Next: ./install.sh remote <ssh-host>"
}

install_remote() {
  local host="${1:-}"
  local alias_name="${2:-$host}"
  [ -n "$host" ] || die "Usage: ./install.sh remote <ssh-host> [profile-alias]"
  chmod_scripts

  info "Checking SSH connectivity to ${host}..."
  ssh "${ssh_opts[@]}" "${host}" 'mkdir -p "$HOME/.local/bin" "$HOME/.hive-bridge" "$HOME/.claude/skills/hive-bridge" "$HOME/.codex/skills/hive-bridge"'

  info "Detecting remote Python with websockets >= 13..."
  local codex_py
  codex_py="$(ssh "${ssh_opts[@]}" "${host}" '
    find_py() {
      for p in \
        "$HOME/miniforge3/bin/python3" \
        "$HOME/miniconda3/bin/python3" \
        "$HOME/mambaforge/bin/python3" \
        "$HOME/software/miniforge3/bin/python3" \
        "$(bash -ic "which python3" 2>/dev/null || true)" \
        "$(command -v python3 2>/dev/null || true)"; do
        if [ -n "$p" ] && [ -x "$p" ] && "$p" -c "import websockets" >/dev/null 2>&1; then
          echo "$p"
          return 0
        fi
      done
      command -v python3 || echo "/usr/bin/python3"
    }
    find_py
  ')"
  info "Selected remote Python for Codex daemon: ${codex_py}"

  local remote_home
  remote_home="$(ssh "${ssh_opts[@]}" "${host}" 'printf "%s" "$HOME"')"

  info "Uploading core scripts and Claude/Codex skills to ${host}..."
  scp -q "${ssh_opts[@]}" "${REPO_DIR}/core/relay_bus.py" "${host}:~/.hive-bridge/relay_bus.py"
  scp -q "${ssh_opts[@]}" "${REPO_DIR}/core/codex_daemon.py" "${host}:~/.hive-bridge/codex_daemon.py"
  scp -q "${ssh_opts[@]}" "${REPO_DIR}/core/claude_runner.py" "${host}:~/.hive-bridge/claude_runner.py"
  scp -q "${ssh_opts[@]}" "${REPO_DIR}/adapters/claude/skills/hive-bridge/SKILL.md" "${host}:~/.claude/skills/hive-bridge/SKILL.md"
  scp -q "${ssh_opts[@]}" "${REPO_DIR}/adapters/claude/hooks/check_inbox.sh" "${host}:~/.hive-bridge/claude_check_inbox.sh"
  scp -q "${ssh_opts[@]}" "${REPO_DIR}/adapters/codex/skills/hive-bridge/SKILL.md" "${host}:~/.codex/skills/hive-bridge/SKILL.md"

  # Create thin wrapper shims in ~/.local/bin with resolved Python paths (works in non-interactive SSH!)
  ssh "${ssh_opts[@]}" "${host}" "CODEX_PY='${codex_py}' bash -s" <<'REMOTE_SHIMS'
set -euo pipefail
chmod +x "$HOME/.hive-bridge/relay_bus.py" "$HOME/.hive-bridge/codex_daemon.py" "$HOME/.hive-bridge/claude_runner.py" "$HOME/.hive-bridge/claude_check_inbox.sh"

cat > "$HOME/.local/bin/hive-relay-bus" <<EOF
#!/usr/bin/env bash
exec python3 "\$HOME/.hive-bridge/relay_bus.py" "\$@"
EOF

cat > "$HOME/.local/bin/hive-codex-daemon" <<EOF
#!/usr/bin/env bash
exec "${CODEX_PY}" "\$HOME/.hive-bridge/codex_daemon.py" "\$@"
EOF

cat > "$HOME/.local/bin/hive-claude-runner" <<EOF
#!/usr/bin/env bash
exec python3 "\$HOME/.hive-bridge/claude_runner.py" "\$@"
EOF

chmod +x "$HOME/.local/bin/hive-relay-bus" "$HOME/.local/bin/hive-codex-daemon" "$HOME/.local/bin/hive-claude-runner"
"$HOME/.local/bin/hive-relay-bus" topics >/dev/null
REMOTE_SHIMS

  mkdir -p "${LOCAL_CONFIG_DIR}"
  python3 - "${LOCAL_CONFIG_FILE}" "${alias_name}" "${host}" "${codex_py}" "${remote_home}" <<'PY'
import json, sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
alias, ssh_host, codex_py, remote_home = sys.argv[2:6]
if cfg_path.exists():
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
else:
    cfg = {"default_host": "", "hosts": {}}

cfg.setdefault("hosts", {})
existing = cfg["hosts"].get(alias, {})
cfg["hosts"][alias] = {
    "ssh": ssh_host,
    "codex_python": codex_py,
    "default_cwd": existing.get("default_cwd", remote_home),
    "codex_sandbox": existing.get("codex_sandbox", "danger-full-access"),
    "codex_approval_policy": existing.get("codex_approval_policy", "never"),
    "claude_permission_mode": existing.get("claude_permission_mode", "bypassPermissions"),
}
if not cfg.get("default_host"):
    cfg["default_host"] = alias

cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

  info "Registered host profile '${alias_name}' -> ${LOCAL_CONFIG_FILE}"
  info "Running doctor check..."
  "${REPO_DIR}/local/hive_bridge.py" doctor --host "${alias_name}"
}

uninstall_local() {
  rm -f "${LOCAL_BIN_DIR}/hive-bridge"
  if [ -L "${AGY_PLUGIN_DIR}" ]; then
    rm -f "${AGY_PLUGIN_DIR}"
  fi
  info "Removed local CLI symlink and Antigravity plugin symlink (kept ${LOCAL_CONFIG_FILE})."
}

uninstall_remote() {
  local host="${1:-}"
  [ -n "$host" ] || die "Usage: ./install.sh uninstall remote <ssh-host>"
  ssh "${ssh_opts[@]}" "${host}" '
    rm -f "$HOME/.local/bin/hive-relay-bus" "$HOME/.local/bin/hive-codex-daemon" "$HOME/.local/bin/hive-claude-runner"
    rm -rf "$HOME/.claude/skills/hive-bridge" "$HOME/.codex/skills/hive-bridge"
    rm -f "$HOME/.hive-bridge/relay_bus.py" "$HOME/.hive-bridge/codex_daemon.py" "$HOME/.hive-bridge/claude_runner.py" "$HOME/.hive-bridge/claude_check_inbox.sh"
  '
  info "Removed remote shims and skills from ${host} (kept ~/.hive-bridge/bus.db and runs/)."
}

cmd="${1:-}"
case "$cmd" in
  local)
    install_local
    ;;
  remote)
    shift
    install_remote "$@"
    ;;
  doctor)
    shift
    if [ -n "${1:-}" ]; then
      "${REPO_DIR}/local/hive_bridge.py" doctor --host "$1"
    else
      "${REPO_DIR}/local/hive_bridge.py" doctor
    fi
    ;;
  uninstall)
    sub="${2:-}"
    case "$sub" in
      local) uninstall_local ;;
      remote) uninstall_remote "${3:-}" ;;
      *) die "Usage: ./install.sh uninstall <local|remote <ssh-host>>" ;;
    esac
    ;;
  *)
    cat <<'USAGE'
Hive Bridge Installer
Usage:
  ./install.sh local                        Install local CLI + Antigravity plugin & side-pane console
  ./install.sh remote <ssh-host> [alias]    Deploy core CLI + Claude/Codex skills to remote host
  ./install.sh doctor [alias]               Verify SSH, Python/websockets, Codex daemon, and Claude CLI
  ./install.sh uninstall local              Remove local symlinks
  ./install.sh uninstall remote <ssh-host>  Remove remote binaries and skills (preserves DB & runs)
USAGE
    exit 1
    ;;
esac
