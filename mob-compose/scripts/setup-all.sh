#!/usr/bin/env bash
# devkit — one-click setup for tg-notify + droid-ctl + iphone-ctl + all skills
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MONOREPO_ROOT="$(cd "$COMPOSE_ROOT/.." && pwd)"

DEFAULT_PROJECT_DIR="$MONOREPO_ROOT"
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
WITH_IOS_WDA=0
WITH_TEST=0
NON_INTERACTIVE=0
TOKEN=""
CHAT_ID=""
ONLY=""  # comma-separated: tg,adb,ios

usage() {
  cat <<'EOF'
Usage: setup-all.sh [options]

One-click install: tgkit, adbkit, iphone-ctl + Cursor/Claude skills + .env
Skills and packages are composable — use --only to install a subset.

Options:
  --project-dir PATH     .env directory (default: mobile-agent root)
  --only LIST            Comma-separated: tg,adb,ios (default: all)
  --with-ios-wda         Clone WDA + xcodebuild if IOS_DEVELOPMENT_TEAM set
  --test                 Send Telegram test + device smoke checks
  --token TOKEN          TELEGRAM_BOT_TOKEN
  --chat-id ID           TELEGRAM_CHAT_ID
  --non-interactive      No prompts
  -h, --help

Examples:
  ./mob setup
  ./mob setup --only tg,adb --test
  ./mob setup --only ios --with-ios-wda
EOF
}

want() {
  local id="$1"
  [[ -z "$ONLY" ]] && return 0
  [[ ",$ONLY," == *",$id,"* ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir) PROJECT_DIR="${2:-}"; shift 2 ;;
    --only) ONLY="${2:-}"; shift 2 ;;
    --with-ios-wda) WITH_IOS_WDA=1; shift ;;
    --test) WITH_TEST=1; shift ;;
    --token) TOKEN="${2:-}"; shift 2 ;;
    --chat-id) CHAT_ID="${2:-}"; shift 2 ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown: $1" >&2; usage; exit 1 ;;
  esac
done

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# Load devkit local config
if [[ -f "$COMPOSE_ROOT/devkit.env" ]]; then
  # shellcheck disable=SC1091
  set -a && source "$COMPOSE_ROOT/devkit.env" && set +a
fi

echo "╔══════════════════════════════════════════╗"
echo "║  devkit — one-click mobile ops setup     ║"
echo "╚══════════════════════════════════════════╝"
echo "monorepo : $MONOREPO_ROOT"
echo "project  : $PROJECT_DIR"
echo ""

step() { echo ""; echo "▶ $1"; }

# --- 1. Python packages (composable) ---
PKGS=()
want tg && PKGS+=(tg-notify)
want adb && PKGS+=(droid-ctl)
want ios && PKGS+=(iphone-ctl)
if [[ ${#PKGS[@]} -eq 0 ]]; then
  PKGS=(tg-notify droid-ctl iphone-ctl)
fi

step "Install Python packages: ${PKGS[*]}"
for pkg in "${PKGS[@]}"; do
  dir="$MONOREPO_ROOT/$pkg"
  if [[ -f "$dir/pyproject.toml" ]]; then
    echo "  pip install -e $dir"
    python3 -m pip install -e "$dir" -q
  else
    echo "  skip $pkg (not found at $dir)"
  fi
done

# tg-relay runtime dep: python-telegram-bot. `./mob up` imports `telegram`,
# so without this the bot crash-loops even though setup "succeeded".
# Mirror tg-relay/setup-telegram.sh: prefer v20-21, fall back to legacy 12.x.
if want tg; then
  echo "  pip install python-telegram-bot (for ./mob up / tg-relay)"
  python3 -m pip install "python-telegram-bot>=20,<22" -q 2>/dev/null \
    || python3 -m pip install "python-telegram-bot>=12.8,<13" -q \
    || echo "  ! python-telegram-bot install failed — ./mob up will crash-loop; run: python3 -m pip install python-telegram-bot"
fi

# droid-ctl vendor platform-tools
if want adb && command -v droid-ctl >/dev/null 2>&1; then
  droid-ctl install-tools 2>/dev/null || droid-ctl which
fi

# --- 2. libimobiledevice (iOS USB) ---
if want ios; then
  step "libimobiledevice (iOS USB)"
  if command -v idevice_id >/dev/null 2>&1; then
    echo "  ✓ already installed"
  elif command -v brew >/dev/null 2>&1; then
    echo "  brew install libimobiledevice"
    brew install libimobiledevice
  else
    echo "  ! install manually: brew install libimobiledevice"
  fi
fi

# --- 3. Telegram .env ---
if want tg; then
  step "Telegram (tg_skill)"
  TG_SETUP="$MONOREPO_ROOT/tg-notify-skill/scripts/setup-all.sh"
  if [[ -x "$TG_SETUP" ]]; then
    ARGS=(--project-dir "$PROJECT_DIR")
    [[ -n "$TOKEN" ]] && ARGS+=(--token "$TOKEN")
    [[ -n "$CHAT_ID" ]] && ARGS+=(--chat-id "$CHAT_ID")
    [[ "$NON_INTERACTIVE" -eq 1 ]] && ARGS+=(--non-interactive)
    [[ "$WITH_TEST" -eq 1 ]] && ARGS+=(--test)
    "$TG_SETUP" "${ARGS[@]}" || true
  else
    echo "  skip tg_skill setup"
  fi
fi

# --- 4. Skills (composable) ---
SKILL_LIST=""
want tg && SKILL_LIST+="tg,"
want adb && SKILL_LIST+="adb,"
want ios && SKILL_LIST+="ios,"
SKILL_LIST="${SKILL_LIST}mob-remote"
SKILL_LIST="${SKILL_LIST%,}"

step "Install LLM skills ($SKILL_LIST)"
MA_INSTALL="$MONOREPO_ROOT/scripts/install-skill.sh"
if [[ -x "$MA_INSTALL" ]]; then
  "$MA_INSTALL" --only "$SKILL_LIST" | sed 's/^/  /'
fi

# --- 5. Optional WDA build ---
if [[ "$WITH_IOS_WDA" -eq 1 ]] && want ios; then
  step "WebDriverAgent (iOS)"
  "$SCRIPT_DIR/build-wda.sh" || echo "  ! WDA build skipped — see docs/WDA_SETUP.md"
fi

# --- 6. Check ---
step "Environment check"
"$SCRIPT_DIR/check-env.sh" || true

# --- 7. Optional device tests ---
if [[ "$WITH_TEST" -eq 1 ]]; then
  step "Device smoke tests"
  want adb && command -v droid-ctl >/dev/null 2>&1 && droid-ctl shot --json 2>/dev/null | head -3 || echo "  android: skip"
  want ios && command -v iphone-ctl >/dev/null 2>&1 && iphone-ctl shot --json 2>/dev/null | head -3 || echo "  ios: skip"
fi

cat <<EOF

╔══════════════════════════════════════════╗
║  setup complete                          ║
╚══════════════════════════════════════════╝

Installed: ${PKGS[*]:-all packages}
Skills:    $SKILL_LIST

Combos:    ./mob install-skill --list
           docs/SKILL_COMPOSE.md
  ./mob-compose/scripts/check-env.sh          # 检查全部环境
  ./mob-compose/scripts/start-ios-proxy.sh    # 启动 iOS WDA 端口转发
  ./mob-compose/scripts/shot-android.sh       # Android 截图发 TG
  ./mob-compose/scripts/shot-ios.sh           # iOS 截图发 TG

Agent: skills installed → $SKILL_LIST

Daily one-liners:
  ./mob check
  ./mob ios-start
  ./mob shot-android -c "验收"
  ./mob shot-ios -c "验收"

EOF
