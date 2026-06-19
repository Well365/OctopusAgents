#!/usr/bin/env bash
# Start/stop full Telegram stack: tg-relay (receive) + iterm-monitor (reply back)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RELAY_DAEMON="$SCRIPT_DIR/tg-relay-daemon.sh"
ITERM_MONITOR="$ROOT/term-bridge/iterm-monitor.py"
ITERM_PIDFILE="$ROOT/inbox/iterm-monitor-daemon.pid"
ITERM_LOG="$ROOT/inbox/iterm-monitor.log"
ENV_FILE="$ROOT/.env"

mkdir -p "$ROOT/inbox"

usage() {
  cat <<EOF
Usage: tg-stack-daemon.sh <command>

Commands:
  start     Start tg-relay + iterm-monitor (background)
  stop      Stop both services
  restart   stop + start
  status    Show status of both + log tails

Examples:
  ./mob up
  ./mob down
  ./mob tg-status
  ./tg-relay/tg-stack-daemon.sh start
EOF
}

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
  export TGKIT_ENV_FILE="${TGKIT_ENV_FILE:-$ENV_FILE}"
}

monitor_pids() {
  pgrep -f "$ITERM_MONITOR" 2>/dev/null || true
}

stop_monitor() {
  echo "▶ stop iterm-monitor"

  if [[ -f "$ITERM_PIDFILE" ]]; then
    local mpid
    mpid="$(cat "$ITERM_PIDFILE" 2>/dev/null || true)"
    if [[ -n "${mpid:-}" ]] && kill -0 "$mpid" 2>/dev/null; then
      echo "  stopping pid=$mpid"
      kill -TERM "$mpid" 2>/dev/null || true
      for _ in $(seq 1 10); do
        kill -0 "$mpid" 2>/dev/null || break
        sleep 0.3
      done
      kill -9 "$mpid" 2>/dev/null || true
    fi
    rm -f "$ITERM_PIDFILE"
  fi

  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    echo "  stopping iterm-monitor.py pid=$pid"
    kill -TERM "$pid" 2>/dev/null || true
  done < <(monitor_pids | sort -u)

  sleep 0.5

  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    kill -9 "$pid" 2>/dev/null || true
  done < <(monitor_pids | sort -u)

  if monitor_pids | grep -q .; then
    echo "  ! some iterm-monitor processes may still be running" >&2
    monitor_pids | sed 's/^/    pid /'
    return 1
  fi
  echo "  ✓ iterm-monitor stopped"
}

start_monitor() {
  stop_monitor || true

  if [[ ! -f "$ENV_FILE" ]]; then
    echo "  ! skip iterm-monitor — missing $ENV_FILE" >&2
    return 1
  fi

  load_env
  if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    echo "  ! skip iterm-monitor — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set" >&2
    return 1
  fi

  echo "▶ iterm-monitor daemon (background)"
  nohup python3 "$ITERM_MONITOR" >>"$ITERM_LOG" 2>&1 </dev/null &
  local mpid=$!
  disown 2>/dev/null || true
  echo "$mpid" >"$ITERM_PIDFILE"

  local waited=0
  while (( waited < 20 )); do
    if kill -0 "$mpid" 2>/dev/null; then
      echo "  ✓ iterm-monitor pid=$mpid"
      echo "  log: $ITERM_LOG"
      return 0
    fi
    sleep 0.25
    waited=$((waited + 1))
  done

  echo "  ✗ iterm-monitor failed to start — see $ITERM_LOG" >&2
  rm -f "$ITERM_PIDFILE"
  return 1
}

monitor_status() {
  echo "iterm-monitor pidfile: $ITERM_PIDFILE"
  echo "iterm-monitor log    : $ITERM_LOG"
  if [[ -f "$ITERM_PIDFILE" ]]; then
    local mpid
    mpid="$(cat "$ITERM_PIDFILE" 2>/dev/null || true)"
    if [[ -n "${mpid:-}" ]] && kill -0 "$mpid" 2>/dev/null; then
      echo "iterm-monitor       : pid=$mpid running"
    else
      echo "iterm-monitor       : pid=$mpid dead"
    fi
  else
    echo "iterm-monitor       : (not running)"
  fi
  local mpids
  mpids="$(monitor_pids | tr '\n' ' ' | sed 's/ $//')"
  if [[ -n "$mpids" ]]; then
    echo "iterm-monitor procs : $mpids"
  fi
  if [[ -f "$ITERM_LOG" ]]; then
    echo ""
    echo "── iterm-monitor log (last 10 lines) ──"
    tail -n 10 "$ITERM_LOG"
  fi
}

start_all() {
  echo "╔══════════════════════════════════════════╗"
  echo "║  mobile-agent — Telegram stack start     ║"
  echo "╚══════════════════════════════════════════╝"
  echo ""

  "$RELAY_DAEMON" start
  echo ""
  start_monitor || true

  echo ""
  echo "╔══════════════════════════════════════════╗"
  echo "║  Stack running                           ║"
  echo "╚══════════════════════════════════════════╝"
  echo "  TG -> iTerm : tg-relay (natural language + /commands)"
  echo "  iTerm -> TG : iterm-monitor (assistant reply only)"
  echo ""
  echo "  status : ./mob tg-status"
  echo "  stop   : ./mob down"
}

stop_all() {
  echo "▶ stop Telegram stack"
  "$RELAY_DAEMON" stop || true
  echo ""
  stop_monitor || true
  echo ""
  echo "✓ stack stopped"
}

status_all() {
  echo "══ tg-relay ══"
  "$RELAY_DAEMON" status
  echo ""
  echo "══ iterm-monitor ══"
  monitor_status
}

cmd="${1:-start}"
shift || true

case "$cmd" in
  start) start_all ;;
  stop) stop_all ;;
  restart) stop_all; start_all ;;
  status) status_all ;;
  -h|--help|help) usage ;;
  *) echo "unknown command: $cmd" >&2; usage; exit 1 ;;
esac
