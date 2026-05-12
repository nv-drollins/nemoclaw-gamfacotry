#!/usr/bin/env bash
set -euo pipefail

SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-app-factory-agent}"
GATEWAY="${OPENSHELL_GATEWAY:-nemoclaw}"
HOST_PORT="${APP_FACTORY_HOST_PORT:-7866}"
APP_PORT="${APP_FACTORY_APP_PORT:-7866}"
DELETE_SANDBOX=0

usage() {
  cat <<EOF
Usage: $0 [options]

Stop the NemoClaw Game Factory app and host port forwarder.

Options:
  --sandbox NAME       Sandbox name. Default: $SANDBOX_NAME
  --gateway NAME       OpenShell gateway name. Default: $GATEWAY
  --host-port PORT     Spark host port. Default: $HOST_PORT
  --app-port PORT      Sandbox app port. Default: $APP_PORT
  --delete-sandbox     Also delete the OpenShell sandbox/container.
  -h, --help           Show this help.

Setup-only options passed by restart.sh are accepted and ignored.
EOF
}

log() {
  printf '\n==> %s\n' "$*"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --sandbox)
      SANDBOX_NAME="${2:?missing sandbox name}"
      shift
      ;;
    --gateway)
      GATEWAY="${2:?missing gateway name}"
      shift
      ;;
    --host-port)
      HOST_PORT="${2:?missing host port}"
      shift
      ;;
    --app-port)
      APP_PORT="${2:?missing app port}"
      shift
      ;;
    --delete-sandbox|--remove-sandbox)
      DELETE_SANDBOX=1
      ;;
    --model|--bind|--onboard-timeout)
      shift
      ;;
    --skip-ollama-install|--skip-model-pull|--skip-onboard|--force-onboard|--no-forward)
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      ;;
  esac
  shift
done

find_containers() {
  if command -v docker >/dev/null 2>&1; then
    docker ps -a --format '{{.Names}}' | grep -E "^openshell-${SANDBOX_NAME}-" || true
  fi
}

stop_forward() {
  local pid_file="/tmp/app_factory_forward_${HOST_PORT}.pid"

  log "Stopping host port forward on ${HOST_PORT}"
  if command -v openshell >/dev/null 2>&1; then
    openshell forward stop -g "$GATEWAY" "$HOST_PORT" >/dev/null 2>&1 || true
  fi

  if [ -f "$pid_file" ]; then
    kill "$(cat "$pid_file")" >/dev/null 2>&1 || true
    rm -f "$pid_file"
  fi

  if command -v pkill >/dev/null 2>&1; then
    pkill -f "forward_port.py .*--bind-port ${HOST_PORT}" >/dev/null 2>&1 || true
  fi
}

stop_app_server() {
  local container
  while IFS= read -r container; do
    [ -n "$container" ] || continue
    log "Stopping App Factory in $container"
    docker exec "$container" pkill -f "python3 server.py --host 0.0.0.0 --port ${APP_PORT}" >/dev/null 2>&1 || true
  done < <(find_containers)
}

delete_sandbox() {
  local container

  log "Deleting sandbox: $SANDBOX_NAME"
  if command -v openshell >/dev/null 2>&1; then
    openshell sandbox delete -g "$GATEWAY" "$SANDBOX_NAME" >/dev/null 2>&1 || true
  fi

  while IFS= read -r container; do
    [ -n "$container" ] || continue
    docker rm -f "$container" >/dev/null 2>&1 || true
  done < <(find_containers)
}

stop_forward
stop_app_server

if [ "$DELETE_SANDBOX" -eq 1 ]; then
  delete_sandbox
fi

log "Stopped"
