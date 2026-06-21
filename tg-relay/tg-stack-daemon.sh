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

# iterm-monitor 看护：崩溃自动重启 + 防爆（窗口内重启过多则停手）
MON_RESTART_DELAY="${TG_MONITOR_RESTART_DELAY:-3}"
MON_MAX_BURST="${TG_MONITOR_MAX_BURST:-10}"
MON_BURST_WINDOW="${TG_MONITOR_BURST_WINDOW:-60}"
MON_LOG_MAX_BYTES="${TG_MONITOR_LOG_MAX_BYTES:-2097152}"   # rotate log if > 2 MB

mkdir -p "$ROOT/inbox"

# Print the resolved restart/backoff knobs so the user can see what's in effect.
print_monitor_knobs() {
  echo "  knobs: MON_RESTART_DELAY=${MON_RESTART_DELAY}s MON_MAX_BURST=${MON_MAX_BURST} MON_BURST_WINDOW=${MON_BURST_WINDOW}s"
}

# Rotate the log once at startup if it grew past MON_LOG_MAX_BYTES (keeps one .1).
# POSIX-ish: `wc -c` + mv works in bash on macOS; never fatal under set -e.
rotate_log() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local size
  size="$(wc -c <"$f" 2>/dev/null | tr -d ' ')" || return 0
  if [[ -n "$size" ]] && (( size > MON_LOG_MAX_BYTES )); then
    mv -f "$f" "$f.1" 2>/dev/null || : >"$f"
    echo "▶ rotated log ($size bytes > ${MON_LOG_MAX_BYTES}) → $f.1"
  fi
}

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
  # Keep the token OUT of child process environments (visible in `ps -eww`).
  # The shell var is retained for the start-time check below; children
  # (iterm-monitor) re-read the token from .env via their own _load_env().
  export -n TELEGRAM_BOT_TOKEN 2>/dev/null || true
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

  rotate_log "$ITERM_LOG"

  echo "▶ iterm-monitor daemon (background, auto-restart)"
  print_monitor_knobs
  # 后台拉起看护循环（自身崩溃/被杀后自动重启 python）
  nohup "$0" _monitor-loop >>"$ITERM_LOG" 2>&1 </dev/null &
  disown 2>/dev/null || true

  local waited=0
  while (( waited < 20 )); do
    if [[ -f "$ITERM_PIDFILE" ]]; then
      local dpid
      dpid="$(cat "$ITERM_PIDFILE" 2>/dev/null || true)"
      if [[ -n "${dpid:-}" ]] && kill -0 "$dpid" 2>/dev/null; then
        verify_monitor_alive "$dpid"
        return $?
      fi
    fi
    sleep 0.25
    waited=$((waited + 1))
  done

  echo "  ✗ iterm-monitor failed to start — see $ITERM_LOG" >&2
  rm -f "$ITERM_PIDFILE"
  return 1
}

# The supervisor wrapper being alive doesn't mean the python worker survived —
# it may crash-loop immediately (e.g. missing dep / permission). Wait ~2s and
# confirm a real iterm-monitor.py worker is running before printing success;
# otherwise print FAILURE + the last log lines so the user sees the real error.
verify_monitor_alive() {
  local dpid="$1"
  sleep 2
  local pids
  pids="$(monitor_pids | tr '\n' ' ' | sed 's/ $//')"
  if [[ -n "$pids" ]]; then
    echo "  ✓ iterm-monitor supervisor pid=$dpid"
    echo "  ✓ worker alive (pids: $pids)"
    echo "  log: $ITERM_LOG"
    return 0
  fi
  echo "  ✗ FAILURE: iterm-monitor worker died right after start (crash-loop)" >&2
  if ! python3 -c "import telegram" >/dev/null 2>&1; then
    echo "  ✗ python-telegram-bot not importable — run: python3 -m pip install python-telegram-bot" >&2
  fi
  if [[ -f "$ITERM_LOG" ]]; then
    echo "  ── last 15 log lines ($ITERM_LOG) ──" >&2
    tail -n 15 "$ITERM_LOG" >&2
  fi
  return 1
}

# 看护循环：前台运行 python，崩溃即重启；窗口内重启过多则停手（防 crash-loop 刷屏）
run_monitor_loop() {
  load_env
  echo $$ >"$ITERM_PIDFILE"
  trap 'rm -f "$ITERM_PIDFILE"; exit 0' SIGTERM SIGINT

  local restarts=0 window_start
  window_start=$(date +%s)

  while true; do
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting iterm-monitor (supervisor pid $$)"
    set +e
    python3 "$ITERM_MONITOR"
    local code=$?
    set -e
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] iterm-monitor exited code=$code"

    local now
    now=$(date +%s)
    if (( now - window_start > MON_BURST_WINDOW )); then
      restarts=0
      window_start=$now
    fi
    restarts=$((restarts + 1))
    if (( restarts >= MON_MAX_BURST )); then
      echo "error: iterm-monitor restarted $restarts times in ${MON_BURST_WINDOW}s — stopping" >&2
      rm -f "$ITERM_PIDFILE"
      exit 1
    fi

    sleep "$MON_RESTART_DELAY"
  done
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

  # Guarded: tg-relay-daemon now exits non-zero if the worker crash-loops on
  # startup. Surface that, but still attempt the monitor (matches start_monitor).
  "$RELAY_DAEMON" start || echo "  ! tg-relay failed to start cleanly — see above / ./mob tg-status" >&2
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
  _monitor-loop) run_monitor_loop ;;   # 内部：被 start_monitor 后台拉起的看护循环
  -h|--help|help) usage ;;
  *) echo "unknown command: $cmd" >&2; usage; exit 1 ;;
esac
