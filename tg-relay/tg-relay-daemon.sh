#!/usr/bin/env bash
# tg-relay supervisor — singleton + auto-restart on crash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RELAY="${TG_RELAY_SCRIPT:-$ROOT/tg-relay/run_tg_relay.py}"
PIDFILE="$ROOT/inbox/tg-relay-daemon.pid"
LOGFILE="$ROOT/inbox/tg-relay.log"
RESTART_DELAY="${TG_RELAY_RESTART_DELAY:-3}"
MAX_BURST="${TG_RELAY_MAX_BURST:-10}"   # max restarts within window
BURST_WINDOW="${TG_RELAY_BURST_WINDOW:-60}"
LOG_MAX_BYTES="${TG_RELAY_LOG_MAX_BYTES:-2097152}"   # rotate log if > 2 MB

mkdir -p "$ROOT/inbox"

# Print the resolved restart/backoff knobs so the user can see what's in effect.
print_knobs() {
  echo "  knobs: RESTART_DELAY=${RESTART_DELAY}s MAX_BURST=${MAX_BURST} BURST_WINDOW=${BURST_WINDOW}s"
}

# Rotate the log once at startup if it grew past LOG_MAX_BYTES (keeps one .1).
# POSIX-ish: `wc -c` + mv works in bash on macOS; never fatal under set -e.
rotate_log() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local size
  size="$(wc -c <"$f" 2>/dev/null | tr -d ' ')" || return 0
  if [[ -n "$size" ]] && (( size > LOG_MAX_BYTES )); then
    mv -f "$f" "$f.1" 2>/dev/null || : >"$f"
    echo "▶ rotated log ($size bytes > ${LOG_MAX_BYTES}) → $f.1"
  fi
}

usage() {
  cat <<EOF
Usage: tg-relay-daemon.sh <command>

Commands:
  start [--foreground]   Kill duplicates, then run tg-relay (default: background)
  stop                   Stop daemon and all tg-relay.py instances
  restart                stop + start
  status                 Show running PIDs and log tail

Environment:
  TG_RELAY_RESTART_DELAY   Seconds between crash restarts (default: 3)
  TG_RELAY_MAX_BURST       Max restarts within burst window (default: 10)
  TG_RELAY_BURST_WINDOW    Burst window seconds (default: 60)
  TG_RELAY_LOG_MAX_BYTES   Rotate log at startup if larger (default: 2097152)
  TG_RELAY_LOG             Log file path (default: inbox/tg-relay.log)

Examples:
  ./tg-relay-daemon.sh start
  ./tg-relay-daemon.sh start --foreground
  ./mob tg-start
  ./mob tg-stop
EOF
}

relay_pids() {
  pgrep -f "$RELAY" 2>/dev/null || true
}

stop_all() {
  echo "▶ stop tg-relay (singleton cleanup)"

  if [[ -f "$PIDFILE" ]]; then
    local dpid
    dpid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "${dpid:-}" ]] && kill -0 "$dpid" 2>/dev/null; then
      echo "  stopping daemon pid=$dpid"
      kill -TERM "$dpid" 2>/dev/null || true
      for _ in $(seq 1 10); do
        kill -0 "$dpid" 2>/dev/null || break
        sleep 0.3
      done
      kill -9 "$dpid" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
  fi

  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    echo "  stopping tg-relay.py pid=$pid"
    kill -TERM "$pid" 2>/dev/null || true
  done < <(relay_pids | sort -u)

  sleep 1

  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    kill -9 "$pid" 2>/dev/null || true
  done < <(relay_pids | sort -u)

  if relay_pids | grep -q .; then
    echo "  ! some tg-relay processes may still be running" >&2
    relay_pids | sed 's/^/    pid /'
    return 1
  fi
  echo "  ✓ all tg-relay stopped"
}

status_cmd() {
  echo "pidfile : ${PIDFILE}"
  echo "log     : ${LOGFILE}"
  if [[ -f "$PIDFILE" ]]; then
    echo "daemon  : pid=$(cat "$PIDFILE") $(kill -0 "$(cat "$PIDFILE")" 2>/dev/null && echo running || echo dead)"
  else
    echo "daemon  : (not running)"
  fi
  local pids
  pids="$(relay_pids | tr '\n' ' ' | sed 's/ $//')"
  if [[ -n "$pids" ]]; then
    echo "relay   : $pids"
  else
    echo "relay   : (not running)"
  fi
  if [[ -f "$LOGFILE" ]]; then
    echo ""
    echo "── log (last 15 lines) ──"
    tail -n 15 "$LOGFILE"
  fi
}

run_foreground_loop() {
  echo $$ >"$PIDFILE"
  trap 'rm -f "$PIDFILE"; exit 0' SIGTERM SIGINT

  local restarts=0
  local window_start
  window_start=$(date +%s)

  while true; do
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting tg-relay (pid $$)"
    set +e
    python3 "$RELAY"
    local code=$?
    set -e
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] tg-relay exited code=$code"

    local now
    now=$(date +%s)
    if (( now - window_start > BURST_WINDOW )); then
      restarts=0
      window_start=$now
    fi
    restarts=$((restarts + 1))
    if (( restarts >= MAX_BURST )); then
      echo "error: tg-relay restarted $restarts times in ${BURST_WINDOW}s — stopping" >&2
      rm -f "$PIDFILE"
      exit 1
    fi

    sleep "$RESTART_DELAY"
  done
}

start_cmd() {
  local fg=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --foreground|-f) fg=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) echo "unknown: $1" >&2; usage; exit 1 ;;
    esac
  done

  stop_all || true

  rotate_log "$LOGFILE"

  if [[ "$fg" -eq 1 ]]; then
    echo "▶ tg-relay daemon (foreground, auto-restart)"
    print_knobs
    echo "  log: $LOGFILE"
    exec >>"$LOGFILE" 2>&1 </dev/null
    run_foreground_loop
  fi

  echo "▶ tg-relay daemon (background)"
  print_knobs
  nohup "$0" start --foreground >>"$LOGFILE" 2>&1 </dev/null &
  disown 2>/dev/null || true

  local waited=0
  while (( waited < 20 )); do
    if [[ -f "$PIDFILE" ]]; then
      local dpid
      dpid="$(cat "$PIDFILE" 2>/dev/null || true)"
      if [[ -n "${dpid:-}" ]] && kill -0 "$dpid" 2>/dev/null; then
        verify_worker_alive "$dpid"
        return $?
      fi
    fi
    if relay_pids | grep -q .; then
      verify_worker_alive ""
      return $?
    fi
    sleep 0.25
    waited=$((waited + 1))
  done

  echo "  ✗ daemon failed to start — see $LOGFILE" >&2
  rm -f "$PIDFILE"
  exit 1
}

# After the supervisor wrapper comes up, the child python may still crash-loop.
# Wait ~2s and confirm a real tg-relay.py worker is alive before claiming success;
# otherwise print FAILURE + the last log lines so the user sees the real error
# instead of a false green. $1 = supervisor pid (may be empty).
verify_worker_alive() {
  local dpid="$1"
  sleep 2
  local pids
  pids="$(relay_pids | tr '\n' ' ' | sed 's/ $//')"
  if [[ -n "$pids" ]]; then
    [[ -n "$dpid" ]] && echo "  ✓ daemon pid=$dpid" || echo "  ✓ tg-relay running"
    echo "  ✓ worker alive (pids: $pids)"
    echo "  log: $LOGFILE"
    echo "  stop: ./mob tg-stop"
    return 0
  fi
  echo "  ✗ FAILURE: tg-relay worker died right after start (crash-loop)" >&2
  if ! python3 -c "import telegram" >/dev/null 2>&1; then
    echo "  ✗ python-telegram-bot not importable — run: python3 -m pip install python-telegram-bot" >&2
  fi
  if [[ -f "$LOGFILE" ]]; then
    echo "  ── last 15 log lines ($LOGFILE) ──" >&2
    tail -n 15 "$LOGFILE" >&2
  fi
  return 1
}

cmd="${1:-start}"
shift || true

case "$cmd" in
  start) start_cmd "$@" ;;
  stop) stop_all ;;
  restart) stop_all || true; start_cmd ;;
  status) status_cmd ;;
  -h|--help|help) usage ;;
  *) echo "unknown command: $cmd" >&2; usage; exit 1 ;;
esac
