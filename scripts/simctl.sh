#!/usr/bin/env bash
# Start/stop/restart the laptop simulator stack (sim hub + fake welle + fake gpsd + core + dawn-timed)
# in the background with a pidfile. Usage: scripts/simctl.sh {start|stop|restart|status|restart-core}
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/var/sim.pid"
LOG="$ROOT/var/sim.log"
mkdir -p "$ROOT/var"

start() {
  if status >/dev/null 2>&1; then echo "already running (pid $(cat "$PIDFILE"))"; return 0; fi
  (cd "$ROOT" && DAWN_SIM=1 DAWN_CONFIG="$ROOT/config/config.sim.yaml" DAWN_DATA_DIR="$ROOT/var/dawn" PYTHONUNBUFFERED=1 \
    nohup "$ROOT/.venv/bin/python" -m dawn_sim --no-keyboard "$@" >"$LOG" 2>&1 & echo $! >"$PIDFILE")
  for _ in $(seq 1 40); do curl -sf localhost:8080/api/health >/dev/null 2>&1 && break; sleep 0.5; done
  echo "started (pid $(cat "$PIDFILE"))"
}
sweep() {
  # kill orphaned children of a previous launcher (patterns built at runtime so they never match this script)
  for pat in "dawn_cor""e" "dawn_time""d" "dawn_si""m"; do
    for p in $(pgrep -f "python -m $pat" || true); do kill "$p" 2>/dev/null || true; done
  done
  sleep 0.5
  for pat in "dawn_cor""e" "dawn_time""d" "dawn_si""m"; do
    for p in $(pgrep -f "python -m $pat" || true); do kill -9 "$p" 2>/dev/null || true; done
  done
}
stop() {
  sweep
  if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE")"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "stopped"
  else
    echo "not running"
  fi
}
status() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && { echo "running (pid $(cat "$PIDFILE"))"; return 0; }
  echo "not running"; return 1
}
restart_core() {
  # the launcher restarts core automatically when it exits
  for p in $(ps -eo pid,args | awk '/\.venv\/bin\/python -m dawn_core$/ {print $1}'); do kill "$p"; done
  for _ in $(seq 1 40); do sleep 0.5; curl -sf localhost:8080/api/health >/dev/null 2>&1 && { echo "core restarted"; return 0; }; done
  echo "core did not come back"; return 1
}
case "${1:-}" in
  start) shift; start "$@";;
  stop) stop;;
  restart) shift; stop; sleep 1; start "$@";;
  status) status;;
  restart-core) restart_core;;
  *) echo "usage: $0 {start|stop|restart|status|restart-core}"; exit 2;;
esac
