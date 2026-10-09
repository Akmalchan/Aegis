#!/usr/bin/env bash
# AEGIS scanner launcher: FastAPI (uvicorn, 127.0.0.1:$PORT) + a public cloudflared tunnel.
#
# The Guild integration's base URL is FROZEN once published, so the tunnel must outlive API
# restarts. The tunnel is a detached process (own session, pid in state/cloudflared.pid) that
# this script only starts when it is not already running; the API runs in a separate restart loop.
#
#   ./run.sh           ensure tunnel is up, then run the API in the foreground (auto-restart loop).
#                      Ctrl-C stops the API only; the tunnel keeps running.
#   ./run.sh start     same, but the API loop is detached too (log: state/api.log)
#   ./run.sh api       restart only the API (kills uvicorn; the running loop respawns it).
#                      If no loop is running, runs the loop in the foreground (tunnel untouched).
#   ./run.sh tunnel    (re)start only the tunnel. Quick tunnel => NEW URL => re-publish Guild integration!
#   ./run.sh stop      stop API loop + tunnel
#   ./run.sh status    show what is running + the URL
#   ./run.sh url       print the tunnel URL
#
# Env: PORT (default 8787), RELOAD=1 (uvicorn --reload),
#      CLOUDFLARE_TUNNEL_TOKEN (named tunnel, stable URL) + TUNNEL_URL (its public https URL).
#      Both may also be set in .env.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
STATE="$ROOT/state"
mkdir -p "$STATE"

PORT="${PORT:-8787}"
HOST=127.0.0.1
CF_PID="$STATE/cloudflared.pid"
CF_LOG="$STATE/cloudflared.log"
URL_FILE="$STATE/tunnel_url.txt"
LOOP_PID="$STATE/api_loop.pid"
API_PID="$STATE/api.pid"
API_LOG="$STATE/api.log"
# exec a command in a new session: same pid, immune to Ctrl-C / terminal close (macOS has no setsid(1))
SETSID_PY='import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])'

# read KEY from .env (environment wins)
env_get() {
  [ -f "$ROOT/.env" ] || return 0
  grep -E "^[[:space:]]*$1=" "$ROOT/.env" | tail -1 | cut -d= -f2- \
    | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/'
}
CLOUDFLARE_TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-$(env_get CLOUDFLARE_TUNNEL_TOKEN)}"
NAMED_TUNNEL_URL="${TUNNEL_URL:-$(env_get TUNNEL_URL)}"

alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
pidof_file() { [ -f "$1" ] && cat "$1" 2>/dev/null; }
tunnel_alive() { alive "$(pidof_file "$CF_PID")"; }
loop_alive() { alive "$(pidof_file "$LOOP_PID")"; }

kill_tree() {  # kill a pid and all its descendants
  local pid="$1" sig="${2:-TERM}" kid
  for kid in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$kid" "$sig"; done
  kill "-$sig" "$pid" 2>/dev/null || true
}

keep_awake() {  # keep the Mac awake while pid $1 lives
  if command -v caffeinate >/dev/null 2>&1; then caffeinate -i -w "$1" >/dev/null 2>&1 & fi
}

banner() {
  echo
  echo "################################################################################"
  echo "#"
  echo "#   AEGIS PUBLIC URL:   $1"
  echo "#"
  echo "#   check:  curl -s $1/healthz"
  echo "#   saved:  state/tunnel_url.txt   (Guild integration base URL is frozen to it)"
  echo "#"
  echo "################################################################################"
  echo
}

start_tunnel() {
  command -v cloudflared >/dev/null || { echo "cloudflared not installed (brew install cloudflared)" >&2; return 1; }
  local prev="" pid url=""
  [ -f "$URL_FILE" ] && prev="$(cat "$URL_FILE")"
  [ -z "$prev" ] && [ -f "$STATE/tunnel_url.prev.txt" ] && prev="$(cat "$STATE/tunnel_url.prev.txt")"
  : > "$CF_LOG"
  # NB: cloudflared reads $TUNNEL_URL as its --url ORIGIN flag, so it must never see our public TUNNEL_URL.
  if [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
    echo "starting NAMED cloudflared tunnel (stable URL)..."
    # token via env, not argv, to keep it out of `ps`
    env -u TUNNEL_URL TUNNEL_TOKEN="$CLOUDFLARE_TUNNEL_TOKEN" \
      nohup python3 -c "$SETSID_PY" cloudflared tunnel --no-autoupdate run </dev/null >>"$CF_LOG" 2>&1 &
  else
    echo "starting QUICK cloudflared tunnel -> http://$HOST:$PORT ..."
    env -u TUNNEL_URL -u TUNNEL_TOKEN \
      nohup python3 -c "$SETSID_PY" cloudflared tunnel --no-autoupdate --url "http://$HOST:$PORT" </dev/null >>"$CF_LOG" 2>&1 &
  fi
  pid=$!   # env/nohup/python all exec, so this is cloudflared's pid
  echo "$pid" > "$CF_PID"
  keep_awake "$pid"

  if [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
    url="$NAMED_TUNNEL_URL"
    for _ in $(seq 30); do grep -q 'Registered tunnel connection' "$CF_LOG" && break; alive "$pid" || break; sleep 1; done
    [ -z "$url" ] && echo "WARNING: CLOUDFLARE_TUNNEL_TOKEN is set but TUNNEL_URL is empty; set TUNNEL_URL=https://<hostname>" >&2
  else
    for _ in $(seq 45); do
      url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CF_LOG" | grep -v '^https://api\.' | head -1)"
      [ -n "$url" ] && break
      alive "$pid" || break
      sleep 1
    done
  fi
  if ! alive "$pid"; then
    echo "ERROR: cloudflared exited. Last log lines:" >&2; tail -20 "$CF_LOG" >&2; rm -f "$CF_PID"; return 1
  fi
  [ -z "$url" ] && { echo "ERROR: could not determine tunnel URL (see $CF_LOG)" >&2; return 1; }
  echo "$url" > "$URL_FILE"
  if [ -n "$prev" ] && [ "$prev" != "$url" ]; then
    echo "!!! TUNNEL URL CHANGED: $prev -> $url"
    echo "!!! A Guild integration published against the old URL is now dead: bump VERSION and re-run fleet/integration.sh"
  fi
  banner "$url"
}

ensure_tunnel() {
  if tunnel_alive; then
    echo "tunnel already running (pid $(cat "$CF_PID")), reusing it"
    banner "$(cat "$URL_FILE" 2>/dev/null)"
  else
    [ -f "$CF_PID" ] && echo "!!! tunnel process is gone, starting a new one" >&2
    start_tunnel
  fi
}

stop_tunnel() {
  local pid; pid="$(pidof_file "$CF_PID")"
  if alive "$pid"; then
    kill_tree "$pid"   # cloudflared drains connections for a few seconds on SIGTERM
    for _ in $(seq 30); do alive "$pid" || break; sleep 0.5; done
    alive "$pid" && kill_tree "$pid" KILL
    echo "stopped tunnel (pid $pid)"
  fi
  rm -f "$CF_PID"
  [ -f "$URL_FILE" ] && mv -f "$URL_FILE" "$STATE/tunnel_url.prev.txt"
  return 0
}

free_port() {  # kill a stray aegis uvicorn holding the port; refuse to touch anything else
  local p; p="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1)"
  [ -z "$p" ] && return 0
  if ps -o command= -p "$p" | grep -q 'aegis.server:app'; then
    echo "killing stray aegis server on :$PORT (pid $p)"; kill_tree "$p"; sleep 1
  else
    echo "ERROR: port $PORT is held by: $(ps -o pid=,command= -p "$p")" >&2; return 1
  fi
}

# API restart loop: respawns uvicorn whenever it exits, until SIGINT/SIGTERM. Never touches the tunnel.
api_loop() {
  local child="" stopping="" rc
  echo "$$" > "$LOOP_PID"
  keep_awake "$$"
  trap 'stopping=1; [ -n "$child" ] && kill_tree "$child"' INT TERM
  free_port || { rm -f "$LOOP_PID"; return 1; }
  local reload=(); [ "${RELOAD:-}" = 1 ] && reload=(--reload --reload-dir aegis)
  while [ -z "$stopping" ]; do
    echo "[$(date +%H:%M:%S)] starting API on http://$HOST:$PORT"
    uv run uvicorn aegis.server:app --host "$HOST" --port "$PORT" ${reload[@]+"${reload[@]}"} &
    child=$!
    echo "$child" > "$API_PID"
    wait "$child"; rc=$?
    # `wait` returns early when a trapped signal arrives: make sure the child is really gone
    if alive "$child"; then [ -n "$stopping" ] && kill_tree "$child"; wait "$child" 2>/dev/null; fi
    child=""
    [ -n "$stopping" ] && break
    echo "[$(date +%H:%M:%S)] API exited (rc=$rc), restarting in 1s"
    tunnel_alive || echo "!!! WARNING: tunnel is down. ./run.sh tunnel to restart it (quick tunnel => NEW URL)" >&2
    sleep 1
  done
  rm -f "$LOOP_PID" "$API_PID"
  echo "API stopped. Tunnel left running: $(cat "$URL_FILE" 2>/dev/null)  (./run.sh stop stops it)"
}

wait_api() {
  for _ in $(seq 60); do curl -s -o /dev/null "http://$HOST:$PORT/healthz" && return 0; sleep 0.5; done
  echo "WARNING: API not answering on :$PORT yet (see $API_LOG)" >&2; return 1
}

restart_api() {
  local pid; pid="$(pidof_file "$API_PID")"
  alive "$pid" && kill_tree "$pid"
  sleep 1.5
  wait_api && echo "API restarted on http://$HOST:$PORT (tunnel untouched: $(cat "$URL_FILE" 2>/dev/null))"
}

stop_api() {
  local lp ap; lp="$(pidof_file "$LOOP_PID")"
  if alive "$lp"; then
    kill -TERM "$lp" 2>/dev/null
    for _ in $(seq 20); do alive "$lp" || break; sleep 0.3; done
    alive "$lp" && kill_tree "$lp" KILL
    echo "stopped API loop (pid $lp)"
  fi
  ap="$(pidof_file "$API_PID")"; alive "$ap" && kill_tree "$ap"
  rm -f "$LOOP_PID" "$API_PID"
  return 0
}

status() {
  if tunnel_alive; then echo "tunnel: UP    pid $(cat "$CF_PID")   url $(cat "$URL_FILE" 2>/dev/null)"; else echo "tunnel: DOWN"; fi
  if loop_alive; then echo "api:    UP    loop pid $(cat "$LOOP_PID")   uvicorn pid $(pidof_file "$API_PID")"; else echo "api:    DOWN"; fi
}

case "${1:-}" in
  "")
    ensure_tunnel || exit 1
    if loop_alive; then echo "API loop already running (pid $(cat "$LOOP_PID")); ./run.sh api restarts it"; exit 0; fi
    api_loop
    ;;
  start)
    ensure_tunnel || exit 1
    if loop_alive; then echo "API loop already running (pid $(cat "$LOOP_PID"))"; exit 0; fi
    : > "$API_LOG"
    nohup python3 -c "$SETSID_PY" "$0" __loop </dev/null >>"$API_LOG" 2>&1 &
    sleep 1
    wait_api && echo "API up on http://$HOST:$PORT (detached, log: state/api.log)"
    ;;
  __loop) api_loop ;;
  api)    if loop_alive; then restart_api; else api_loop; fi ;;
  tunnel) stop_tunnel; start_tunnel ;;
  stop)   stop_api; stop_tunnel ;;
  status) status ;;
  url)    cat "$URL_FILE" 2>/dev/null || { echo "no tunnel URL (tunnel not running?)" >&2; exit 1; } ;;
  *)      sed -n '2,20p' "$0"; exit 2 ;;
esac
