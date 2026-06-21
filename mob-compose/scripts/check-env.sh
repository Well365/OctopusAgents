#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MONOREPO_ROOT="$(cd "$COMPOSE_ROOT/.." && pwd)"

[[ -f "$COMPOSE_ROOT/devkit.env" ]] && set -a && source "$COMPOSE_ROOT/devkit.env" && set +a

OK=0; WARN=0; FAIL=0
pass() { echo "  ✓ $1"; OK=$((OK+1)); }
warn() { echo "  ! $1"; WARN=$((WARN+1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }

section() { echo ""; echo "── $1 ──"; }

echo "== devkit environment check =="

section "Python CLIs"
for cmd in tg-notify droid-ctl ioskit; do
  if command -v "$cmd" >/dev/null 2>&1; then pass "$cmd: $(command -v "$cmd")"
  else fail "$cmd not installed"; fi
done

section "Telegram"
ENV_FILE=""
if [[ -f "$MONOREPO_ROOT/.env" ]]; then
  ENV_FILE="$MONOREPO_ROOT/.env"
  pass ".env: $ENV_FILE"
  set -a && source "$ENV_FILE" && set +a
else warn ".env missing (copy .env.example → mobile-agent/.env)"; fi
if command -v tg-notify >/dev/null 2>&1 && [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  pass "tgkit configured"
else warn "run: ./mob-compose/setup"; fi
# python-telegram-bot is the runtime dep ./mob up needs; missing → bot crash-loops.
if python3 -c "import telegram" >/dev/null 2>&1; then
  pass "python-telegram-bot importable (telegram)"
else
  fail "python-telegram-bot missing — ./mob up will crash-loop; run: python3 -m pip install python-telegram-bot"
fi

section "Android"
if command -v droid-ctl >/dev/null 2>&1; then
  pass "adb: $(adbkit which 2>/dev/null || echo '?')"
  if droid-ctl devices 2>/dev/null | awk 'NR>1 && $2=="device" {f=1} END{exit !f}'; then
    pass "android device connected"
  else warn "no android device"; fi
fi

section "iOS USB"
for b in idevice_id idevicescreenshot iproxy; do
  command -v "$b" >/dev/null 2>&1 && pass "$b" || warn "$b missing"
done
if command -v iphone-ctl >/dev/null 2>&1; then
  if iphone-ctl devices 2>/dev/null | grep -q .; then pass "ios device connected"
  else warn "no ios device"; fi
fi

section "iOS WDA"
PIDFILE="$COMPOSE_ROOT/.ios-proxy.pid"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  pass "iproxy running (pid $(cat "$PIDFILE"))"
else warn "iproxy not running — ./mob-compose/scripts/start-ios-proxy.sh"
fi
if command -v iphone-ctl >/dev/null 2>&1 && iphone-ctl wda status --json 2>/dev/null | grep -qE '"ready":\s*true'; then
  pass "WDA ready"
else warn "WDA not ready — Xcode Run WebDriverAgentRunner on device"; fi

section "LLM Skills"
for s in tg-notify adb ios; do
  [[ -f "$HOME/.cursor/skills/$s/SKILL.md" ]] && pass "cursor: $s" || warn "cursor: $s missing"
done

section "TG → terminal pipeline"
# Reuse the single source of truth: term-bridge/pipeline_doctor.py. It includes a
# real macOS automation-permission probe, so first-run users learn they must grant
# permission BEFORE their first phone message silently fails. Best-effort: never
# aborts this script (set -e safe via the if-guard).
DOCTOR="$MONOREPO_ROOT/term-bridge/pipeline_doctor.py"
if [[ -f "$DOCTOR" ]]; then
  if python3 "$DOCTOR" | sed 's/^/  /'; then
    pass "pipeline doctor: all checks passed"
  else
    warn "pipeline doctor reported issues (see above; e.g. grant macOS automation permission)"
  fi
else
  warn "pipeline doctor not found at $DOCTOR"
fi

echo ""
echo "summary: ${OK} ok, ${WARN} warn, ${FAIL} fail"
[[ "$FAIL" -eq 0 ]]
