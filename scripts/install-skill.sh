#!/usr/bin/env bash
# Install mob-remote skills — all or selected modules (composable)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 用函数代替关联数组：declare -A 需要 bash 4+，而 macOS 默认 bash 仍是 3.2
skill_dir() {
  case "$1" in
    tg|tg-notify|tgkit)       echo "tg-notify-skill" ;;
    adb|droid-ctl)            echo "droid-ctl-skill" ;;
    ios|iphone-ctl)           echo "iphone-ctl-skill" ;;
    mob-remote|mobile-agent)  echo "mob-remote-skill" ;;
    *)                        echo "" ;;
  esac
}

usage() {
  cat <<'EOF'
Usage: install-skill.sh [options] [SKILL ...]

Install Agent skills to ~/.cursor/skills and ~/.claude/skills.

Available skills:
  tg-notify, tg     Telegram outbound notify
  droid-ctl, adb    Android device control
  iphone-ctl, ios   iPhone device control + WDA
  mob-remote        Umbrella skill (full workflow)

Also installs the Claude Code "停顿→Telegram 通知" hook (Stop/Notification →
tg-notify) into ~/.claude/settings.json.

Options:
  --only LIST     Comma-separated skills
  --all           Install all skills (default)
  --list          List available skills
  --uninstall     Remove the Claude TG-notify hook + mob-remote skill dirs
  -h, --help

Examples:
  ./mob install-skill
  ./mob install-skill --only tg-notify,droid-ctl
  ./mob install-skill --uninstall
  ./mob install-skill --only mob-remote
EOF
}

list_skills() {
  echo "Available: tg-notify | droid-ctl | iphone-ctl | mob-remote"
  echo "Legacy IDs: tg, adb, ios, mobile-agent"
}

HOOK_SCRIPT="$ROOT/scripts/claude-tg-hook.sh"
HOOK_PY="$ROOT/term-bridge/claude_hook_install.py"

install_claude_hook() {
  [[ -f "$HOOK_SCRIPT" ]] || { echo "skip Claude hook: $HOOK_SCRIPT missing"; return 0; }
  chmod +x "$HOOK_SCRIPT" 2>/dev/null || true
  if python3 "$HOOK_PY" install --script "$HOOK_SCRIPT"; then
    echo "  停顿→TG 通知 hook 已注册（新会话生效；已开的会话 /hooks 或重启）"
  else
    echo "  warn: Claude hook 注册失败（需要 python3）" >&2
  fi
}

uninstall_claude_hook() {
  python3 "$HOOK_PY" uninstall --script "$HOOK_SCRIPT" 2>/dev/null \
    && echo "  Claude TG-notify hook 已移除" \
    || echo "  warn: Claude hook 移除失败" >&2
}

install_mob_remote() {
  local target_base="$1" label="$2"
  if [[ ! -d "$(dirname "$target_base")" ]]; then
    echo "skip $label: parent dir missing"
    return 0
  fi
  mkdir -p "$target_base/mob-remote"
  cp "$ROOT/mob-remote-skill/SKILL.md" "$target_base/mob-remote/SKILL.md"
  cp "$ROOT/docs/SKILL_COMPOSE.md" "$target_base/mob-remote/SKILL_COMPOSE.md" 2>/dev/null || true
  cp "$ROOT/README.md" "$target_base/mob-remote/README.md"
  echo "installed → $target_base/mob-remote/"
}

install_sub_skill() {
  local id="$1"
  local dir
  dir="$(skill_dir "$id")"
  if [[ -z "$dir" ]]; then
    echo "unknown skill: $id" >&2
    return 1
  fi
  local setup="$ROOT/$dir/scripts/install-skill.sh"
  if [[ -x "$setup" ]]; then
    echo "→ $id ($dir)"
    "$setup" | sed 's/^/  /'
  else
    echo "skip $id: $setup not found" >&2
    return 1
  fi
}

normalize_id() {
  # 小写化用 tr，避免 bash 4 的 ${1,,}（macOS bash 3.2 不支持）
  local raw
  raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  raw="${raw/_skill/}"
  raw="${raw/-skill/}"
  case "$raw" in
    tg|tgkit|tg-notify|telegram|notify) echo "tg-notify" ;;
    adb|android|droid|droid-ctl) echo "droid-ctl" ;;
    ios|iphone|iphone-ctl|wda) echo "iphone-ctl" ;;
    mob-remote|mobile-agent|mobileagent|all|umbrella) echo "mob-remote" ;;
    *) echo "$raw" ;;
  esac
}

SELECTED=()
ONLY_MODE=0
INSTALL_ALL=0
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --only)
      ONLY_MODE=1
      IFS=',' read -ra parts <<< "${2:-}"
      for p in "${parts[@]}"; do
        SELECTED+=("$(normalize_id "$p")")
      done
      shift 2
      ;;
    --all) INSTALL_ALL=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --list) list_skills; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    --*) echo "unknown option: $1" >&2; usage; exit 1 ;;
    *)
      SELECTED+=("$(normalize_id "$1")")
      shift
      ;;
  esac
done

if [[ "$UNINSTALL" -eq 1 ]]; then
  echo "Uninstalling..."
  uninstall_claude_hook
  for base in "$HOME/.cursor/skills" "$HOME/.claude/skills"; do
    if [[ -d "$base/mob-remote" ]]; then
      rm -rf "$base/mob-remote" && echo "  removed $base/mob-remote/"
    fi
  done
  echo "Done. 子 skill 目录（tg-notify-skill 等）如需移除请手动删 ~/.claude/skills/<name>。"
  exit 0
fi

if [[ "$INSTALL_ALL" -eq 1 ]] || [[ ${#SELECTED[@]} -eq 0 ]]; then
  SELECTED=(tg-notify droid-ctl iphone-ctl mob-remote)
fi

# 字符串去重，避免关联数组（bash 3.2 兼容）
_seen=" "
UNIQUE=()
for s in "${SELECTED[@]}"; do
  [[ -n "$s" ]] || continue
  case "$_seen" in *" $s "*) continue ;; esac
  _seen+="$s "
  UNIQUE+=("$s")
done
SELECTED=("${UNIQUE[@]}")

echo "Installing skills: ${SELECTED[*]}"
echo ""

FAIL=0
for id in "${SELECTED[@]}"; do
  if [[ "$id" == "mob-remote" ]]; then
    install_mob_remote "$HOME/.cursor/skills" "Cursor"
    install_mob_remote "$HOME/.claude/skills" "Claude Code"
  else
    install_sub_skill "$id" || FAIL=$((FAIL + 1))
  fi
done

echo ""
echo "→ Claude Code 停顿→TG 通知 hook"
install_claude_hook

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "Done. Installed: ${SELECTED[*]}"
  echo "Check: ./mob check"
else
  echo "Completed with $FAIL error(s)" >&2
  exit 1
fi
