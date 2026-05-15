#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-app-factory-agent}"
MODEL="${NEMOCLAW_MODEL:-qwen3-coder:30b}"
GATEWAY="${OPENSHELL_GATEWAY:-nemoclaw}"
HOST_BIND="${APP_FACTORY_BIND_HOST:-0.0.0.0}"
HOST_PORT="${APP_FACTORY_HOST_PORT:-7866}"
APP_PORT="${APP_FACTORY_APP_PORT:-7866}"
APP_SANDBOX_DIR="${APP_FACTORY_SANDBOX_DIR:-/sandbox/openclaw-app-factory}"
INSTALL_OLLAMA="${APP_FACTORY_INSTALL_OLLAMA:-1}"
OLLAMA_VERSION="${APP_FACTORY_OLLAMA_VERSION:-0.22.1}"
PULL_MODEL=1
RUN_ONBOARD=1
FORCE_ONBOARD=0
START_FORWARD=1
WAIT_SECONDS="${APP_FACTORY_READY_TIMEOUT:-900}"
ONBOARD_TIMEOUT="${APP_FACTORY_ONBOARD_TIMEOUT:-900}"
ONBOARD_LOG="${APP_FACTORY_ONBOARD_LOG:-/tmp/app-factory-nemoclaw-onboard.log}"

usage() {
  cat <<EOF
Usage: $0 [options]

Install/configure NemoClaw/OpenShell for the NemoClaw Game Factory demo, then
run the Game Factory inside the sandbox and expose it on the Spark host.

Options:
  --sandbox NAME       Sandbox name. Default: $SANDBOX_NAME
  --model MODEL        Ollama model. Default: $MODEL
  --host-port PORT     Spark host port. Default: $HOST_PORT
  --app-port PORT      Sandbox app port. Default: $APP_PORT
  --bind HOST          Host bind address. Default: $HOST_BIND
  --gateway NAME       OpenShell gateway name. Default: $GATEWAY
  --onboard-timeout SEC
                       Max seconds to let NemoClaw onboard wait before using
                       the OpenShell fallback. Default: $ONBOARD_TIMEOUT
  --skip-ollama-install
                       Do not install Ollama if the ollama command is missing.
  --skip-model-pull    Do not pull the Ollama model if it is missing.
  --skip-onboard       Do not run the NemoClaw installer/onboard step.
  --force-onboard      Re-run NemoClaw onboarding even if the gateway exists.
  --no-forward         Start the sandboxed app but do not expose a host port.
  -h, --help           Show this help.

Environment overrides:
  NEMOCLAW_SANDBOX_NAME
  NEMOCLAW_MODEL
  OPENSHELL_GATEWAY
  APP_FACTORY_HOST_PORT
  APP_FACTORY_BIND_HOST
  APP_FACTORY_INSTALL_OLLAMA
  APP_FACTORY_OLLAMA_VERSION
  APP_FACTORY_READY_TIMEOUT
  APP_FACTORY_ONBOARD_TIMEOUT
  APP_FACTORY_ONBOARD_LOG
EOF
}

log() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf 'Warning: %s\n' "$*" >&2
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --sandbox)
      SANDBOX_NAME="${2:?missing sandbox name}"
      shift
      ;;
    --model)
      MODEL="${2:?missing model}"
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
    --bind)
      HOST_BIND="${2:?missing bind address}"
      shift
      ;;
    --gateway)
      GATEWAY="${2:?missing gateway name}"
      shift
      ;;
    --onboard-timeout)
      ONBOARD_TIMEOUT="${2:?missing timeout seconds}"
      shift
      ;;
    --skip-ollama-install)
      INSTALL_OLLAMA=0
      ;;
    --skip-model-pull)
      PULL_MODEL=0
      ;;
    --skip-onboard)
      RUN_ONBOARD=0
      ;;
    --force-onboard)
      FORCE_ONBOARD=1
      ;;
    --no-forward)
      START_FORWARD=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

run_as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    die "Root privileges are required for: $*"
  fi
}

with_user_path() {
  export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
}

ollama_base_url() {
  local value="${OLLAMA_HOST:-http://127.0.0.1:11434}"
  case "$value" in
    http://*|https://*) ;;
    *) value="http://$value" ;;
  esac
  printf '%s\n' "${value%/}"
}

ollama_api_ready() {
  curl -fsS --max-time 5 "$(ollama_base_url)/api/tags" >/dev/null 2>&1
}

ollama_has_model() {
  ollama list 2>/dev/null | awk '{print $1}' | grep -Fxq "$MODEL"
}

latest_sandbox_image() {
  docker images --format '{{.Repository}}:{{.Tag}}' \
    | awk '/^openshell\/sandbox-from:/ {print; exit}'
}

sandbox_ready() {
  openshell sandbox list -g "$GATEWAY" 2>/dev/null \
    | awk -v name="$SANDBOX_NAME" '$1 == name && $0 ~ /Ready/ { found = 1 } END { exit found ? 0 : 1 }'
}

gateway_ready() {
  with_user_path
  command -v openshell >/dev/null 2>&1 || return 1
  openshell status -g "$GATEWAY" >/dev/null 2>&1 || return 1
  openshell provider list -g "$GATEWAY" 2>/dev/null | grep -q 'ollama-local'
}

wait_for_sandbox() {
  local elapsed=0
  while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    if sandbox_ready; then
      return 0
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  return 1
}

find_container() {
  docker ps --format '{{.Names}}' \
    | grep -E "^openshell-${SANDBOX_NAME}-" \
    | head -1
}

stop_forward() {
  local pid_file="/tmp/app_factory_forward_${HOST_PORT}.pid"

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

stop_onboard_processes() {
  if command -v pkill >/dev/null 2>&1; then
    pkill -f '/tmp/nemoclaw.sh --non-interactive --yes-i-accept-third-party-software --fresh' >/dev/null 2>&1 || true
    pkill -f 'nemoclaw onboard --fresh --non-interactive --yes-i-accept-third-party-software --yes' >/dev/null 2>&1 || true
  fi
}

remove_sandbox_containers() {
  while IFS= read -r container; do
    [ -n "$container" ] || continue
    docker rm -f "$container" >/dev/null 2>&1 || true
  done < <(docker ps -a --format '{{.Names}}' | grep -E "^openshell-${SANDBOX_NAME}-" || true)
}

cleanup_failed_onboard_sandbox() {
  with_user_path
  if command -v openshell >/dev/null 2>&1; then
    openshell sandbox delete -g "$GATEWAY" "$SANDBOX_NAME" >/dev/null 2>&1 || true
  fi
  remove_sandbox_containers
}

onboard_log_needs_gateway_reset() {
  [ -f "$ONBOARD_LOG" ] || return 1
  grep -Eq \
    'Existing gateway was started without GPU passthrough|Clear the stale gateway state|nemoclaw uninstall && nemoclaw onboard --gpu' \
    "$ONBOARD_LOG"
}

onboard_log_needs_ollama_systemd_repair() {
  [ -f "$ONBOARD_LOG" ] || return 1
  grep -Eq \
    'Failed to inspect existing Ollama systemd override|Refusing to continue because preserving existing Ollama settings is required' \
    "$ONBOARD_LOG"
}

reset_stale_gateway_state() {
  with_user_path
  warn "Resetting stale NemoClaw/OpenShell gateway state, then retrying GPU onboarding."

  if command -v nemoclaw >/dev/null 2>&1; then
    nemoclaw uninstall --yes >/tmp/app-factory-nemoclaw-uninstall.log 2>&1 || \
      warn "nemoclaw uninstall did not complete cleanly; continuing cleanup."
  fi

  if command -v docker >/dev/null 2>&1; then
    docker rm -f "openshell-cluster-${GATEWAY}" >/dev/null 2>&1 || true
  fi

  cleanup_failed_onboard_sandbox
}

preflight() {
  log "Checking prerequisites"
  need_cmd curl
  need_cmd docker
  need_cmd python3
  need_cmd tar
  need_cmd zstd
  need_cmd awk
  need_cmd grep
}

ensure_ollama_installed() {
  log "Checking Ollama installation"
  if command -v ollama >/dev/null 2>&1; then
    local installed_version
    installed_version="$(ollama --version 2>/dev/null | awk '{print $4}' || true)"
    ollama --version 2>/dev/null || true
    if [ -z "$OLLAMA_VERSION" ] || [ "$installed_version" = "$OLLAMA_VERSION" ]; then
      return 0
    fi
    if [ "$INSTALL_OLLAMA" = "0" ]; then
      warn "Ollama $installed_version is installed, but this demo expects $OLLAMA_VERSION on Spark GB10 for GPU offload."
      return 0
    fi
    log "Pinning Ollama to $OLLAMA_VERSION for Spark GPU offload"
    install_pinned_ollama
    return 0
  fi

  if [ "$INSTALL_OLLAMA" = "0" ]; then
    die "Ollama is not installed. Install it or rerun without --skip-ollama-install."
  fi

  log "Installing pinned Ollama $OLLAMA_VERSION"
  install_pinned_ollama
  hash -r
  command -v ollama >/dev/null 2>&1 || die "Ollama installer completed, but ollama is still not on PATH."
}

install_pinned_ollama() {
  [ -n "$OLLAMA_VERSION" ] || return 0
  need_cmd zstd

  local arch asset url archive
  case "$(uname -m)" in
    aarch64|arm64)
      arch="arm64"
      ;;
    x86_64|amd64)
      arch="amd64"
      ;;
    *)
      die "Unsupported architecture for pinned Ollama install: $(uname -m)"
      ;;
  esac

  asset="ollama-linux-${arch}.tar.zst"
  url="https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/${asset}"
  archive="/tmp/ollama-v${OLLAMA_VERSION}-${arch}.tar.zst"

  log "Downloading Ollama v${OLLAMA_VERSION} (${arch})"
  curl -fL --show-error -o "$archive" "$url"

  if command -v systemctl >/dev/null 2>&1; then
    run_as_root systemctl stop ollama >/dev/null 2>&1 || true
  fi

  if [ -x /usr/local/bin/ollama ]; then
    run_as_root cp -a /usr/local/bin/ollama "/usr/local/bin/ollama.backup.$(date +%Y%m%d%H%M%S)"
  fi
  run_as_root tar --zstd -xf "$archive" -C /usr/local
  run_as_root chmod -R a+rX /usr/local/lib/ollama
  run_as_root useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama >/dev/null 2>&1 || true
  run_as_root usermod -a -G video,render ollama >/dev/null 2>&1 || true
  run_as_root tee /etc/systemd/system/ollama.service >/dev/null <<'EOF'
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="PATH=/usr/local/cuda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

[Install]
WantedBy=default.target
EOF

  if command -v systemctl >/dev/null 2>&1; then
    run_as_root systemctl daemon-reload >/dev/null 2>&1 || true
    run_as_root systemctl enable --now ollama >/dev/null 2>&1 || true
  fi
}

ensure_ollama_running() {
  if ollama_api_ready; then
    log "Ollama API is already reachable at $(ollama_base_url)"
    return 0
  fi

  log "Starting Ollama"
  if command -v systemctl >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then
      systemctl enable --now ollama >/dev/null 2>&1 || systemctl restart ollama >/dev/null 2>&1 || true
    elif command -v sudo >/dev/null 2>&1; then
      sudo systemctl enable --now ollama >/dev/null 2>&1 || sudo systemctl restart ollama >/dev/null 2>&1 || true
    fi
  fi

  if ! ollama_api_ready && command -v pgrep >/dev/null 2>&1 && ! pgrep -f 'ollama serve' >/dev/null 2>&1; then
    nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
  fi

  for _ in $(seq 1 30); do
    if ollama_api_ready; then
      return 0
    fi
    sleep 1
  done

  die "Ollama is installed, but the API is not reachable at $(ollama_base_url). Check: systemctl status ollama"
}

ensure_ollama() {
  ensure_ollama_installed
  ensure_ollama_running
}

ensure_ollama_systemd_loopback() {
  command -v systemctl >/dev/null 2>&1 || return 0
  systemctl list-unit-files ollama.service >/dev/null 2>&1 || return 0

  local current_env
  current_env="$(systemctl show ollama --property=Environment --value 2>/dev/null || true)"
  if printf '%s\n' "$current_env" | grep -q 'OLLAMA_HOST=127\.0\.0\.1:11434'; then
    log "Ollama systemd loopback override is already active"
    return 0
  fi

  log "Ensuring Ollama systemd loopback override for NemoClaw"
  run_as_root mkdir -p /etc/systemd/system/ollama.service.d
  run_as_root tee /etc/systemd/system/ollama.service.d/90-app-factory-loopback.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
EOF

  run_as_root systemctl daemon-reload >/dev/null 2>&1 || true
  run_as_root systemctl restart ollama >/dev/null 2>&1 || true

  for _ in $(seq 1 30); do
    if ollama_api_ready; then
      return 0
    fi
    sleep 1
  done

  warn "Ollama did not respond after applying the loopback override. NemoClaw may still fail to configure inference."
}

ensure_model() {
  log "Checking Ollama model: $MODEL"
  if ollama_has_model; then
    ollama list | grep -F "$MODEL" || true
    return 0
  fi

  if [ "$PULL_MODEL" -ne 1 ]; then
    die "Ollama model '$MODEL' is missing. Run: ollama pull $MODEL"
  fi

  ollama pull "$MODEL"
}

run_onboard_installer() {
  rm -f "$ONBOARD_LOG"
  local status=0

  if command -v timeout >/dev/null 2>&1; then
    timeout --kill-after=30s "${ONBOARD_TIMEOUT}s" \
      bash /tmp/nemoclaw.sh --non-interactive --yes-i-accept-third-party-software --fresh \
      2>&1 | tee "$ONBOARD_LOG"
    status=${PIPESTATUS[0]}
  else
    bash /tmp/nemoclaw.sh --non-interactive --yes-i-accept-third-party-software --fresh \
      2>&1 | tee "$ONBOARD_LOG"
    status=${PIPESTATUS[0]}
  fi

  return "$status"
}

run_onboard() {
  if [ "$RUN_ONBOARD" -ne 1 ]; then
    log "Skipping NemoClaw onboarding"
    return 0
  fi

  if [ "$FORCE_ONBOARD" -ne 1 ] && gateway_ready; then
    log "Reusing existing NemoClaw/OpenShell gateway: $GATEWAY"
    return 0
  fi

  log "Installing/configuring NemoClaw for local Ollama inference"
  export NEMOCLAW_SANDBOX_NAME="$SANDBOX_NAME"
  export NEMOCLAW_PROVIDER=ollama
  export NEMOCLAW_MODEL="$MODEL"
  export NEMOCLAW_POLICY_TIER="${NEMOCLAW_POLICY_TIER:-balanced}"
  export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
  export NEMOCLAW_NON_INTERACTIVE=1
  export NEMOCLAW_YES=1
  export NEMOCLAW_LOCAL_INFERENCE_TIMEOUT="${NEMOCLAW_LOCAL_INFERENCE_TIMEOUT:-300}"
  export NEMOCLAW_SANDBOX_READY_TIMEOUT="${NEMOCLAW_SANDBOX_READY_TIMEOUT:-120}"

  curl -fsSL https://www.nvidia.com/nemoclaw.sh -o /tmp/nemoclaw.sh

  local status=0
  set +e
  run_onboard_installer
  status=$?
  set -e

  if [ "$status" -ne 0 ]; then
    warn "NemoClaw onboarding exited with status $status."
    stop_onboard_processes
    if sandbox_ready; then
      warn "NemoClaw onboarding did not exit cleanly, but the sandbox is Ready. Reusing the NemoClaw-created sandbox."
      return 0
    fi
    cleanup_failed_onboard_sandbox

    if onboard_log_needs_gateway_reset; then
      reset_stale_gateway_state
      log "Retrying NemoClaw onboarding after stale gateway cleanup"

      set +e
      run_onboard_installer
      status=$?
      set -e

      if [ "$status" -ne 0 ]; then
        warn "NemoClaw onboarding retry exited with status $status."
        stop_onboard_processes
        if sandbox_ready; then
          warn "NemoClaw onboarding retry did not exit cleanly, but the sandbox is Ready. Reusing the NemoClaw-created sandbox."
          return 0
        fi
        cleanup_failed_onboard_sandbox
      fi
    fi

    if [ "$status" -ne 0 ] && onboard_log_needs_ollama_systemd_repair; then
      ensure_ollama_systemd_loopback
      log "Retrying NemoClaw onboarding after Ollama systemd override repair"

      set +e
      run_onboard_installer
      status=$?
      set -e

      if [ "$status" -ne 0 ]; then
        warn "NemoClaw onboarding retry exited with status $status."
        stop_onboard_processes
        if sandbox_ready; then
          warn "NemoClaw onboarding retry did not exit cleanly, but the sandbox is Ready. Reusing the NemoClaw-created sandbox."
          return 0
        fi
        cleanup_failed_onboard_sandbox
      fi
    fi

    if [ "$status" -ne 0 ]; then
      if [ -n "$(latest_sandbox_image)" ]; then
        warn "Continuing because gateway/inference setup created a usable sandbox image."
      else
        die "NemoClaw onboarding failed before creating an openshell/sandbox-from image. See $ONBOARD_LOG."
      fi
    fi
  fi
}

ensure_sandbox() {
  with_user_path
  need_cmd openshell

  if sandbox_ready; then
    log "Reusing ready sandbox: $SANDBOX_NAME"
    return 0
  fi

  local image
  image="$(latest_sandbox_image)"
  [ -n "$image" ] || die "No openshell/sandbox-from image found. Re-run without --skip-onboard."

  log "Creating OpenShell sandbox: $SANDBOX_NAME"
  openshell sandbox delete -g "$GATEWAY" "$SANDBOX_NAME" >/dev/null 2>&1 || true
  remove_sandbox_containers

  nohup openshell sandbox create -g "$GATEWAY" \
    --name "$SANDBOX_NAME" \
    --from "$image" \
    --provider ollama-local \
    --gpu \
    -- /usr/bin/env \
      CHAT_UI_URL=http://127.0.0.1:18789 \
      NEMOCLAW_DASHBOARD_PORT=18789 \
      /usr/local/bin/nemoclaw-start \
    >/tmp/app-factory-sandbox.log 2>&1 &

  if ! wait_for_sandbox; then
    tail -80 /tmp/app-factory-sandbox.log >&2 || true
    die "Sandbox '$SANDBOX_NAME' did not become Ready within ${WAIT_SECONDS}s."
  fi

  openshell sandbox list -g "$GATEWAY"
}

install_app() {
  local container
  container="$(find_container)"
  [ -n "$container" ] || die "Could not find running sandbox container for '$SANDBOX_NAME'."

  log "Installing Game Factory into $container:$APP_SANDBOX_DIR"
  docker exec "$container" rm -rf "$APP_SANDBOX_DIR"
  docker exec "$container" mkdir -p "$APP_SANDBOX_DIR"

  tar \
    --exclude='.git' \
    --exclude='runs' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -C "$APP_DIR" \
    -cf - . \
    | docker exec -i "$container" tar -xf - -C "$APP_SANDBOX_DIR"

  docker exec "$container" chown -R sandbox:sandbox "$APP_SANDBOX_DIR"
  docker exec "$container" python3 -m py_compile "$APP_SANDBOX_DIR/server.py"

  log "Starting Game Factory inside the sandbox"
  docker exec "$container" pkill -f "python3 server.py --host 0.0.0.0 --port ${APP_PORT}" >/dev/null 2>&1 || true
  docker exec -u sandbox -w "$APP_SANDBOX_DIR" \
    -e APP_FACTORY_PROVIDER=openshell \
    -e APP_FACTORY_MODEL="$MODEL" \
    -e APP_FACTORY_OPENAI_BASE_URL=https://inference.local/v1 \
    -e APP_FACTORY_OPENAI_INSECURE=1 \
    -e APP_FACTORY_MODEL_TIMEOUT="${APP_FACTORY_MODEL_TIMEOUT:-360}" \
    -e APP_FACTORY_MAX_TOKENS="${APP_FACTORY_MAX_TOKENS:-6000}" \
    -e HTTP_PROXY="http://10.200.0.1:3128" \
    -e HTTPS_PROXY="http://10.200.0.1:3128" \
    -e http_proxy="http://10.200.0.1:3128" \
    -e https_proxy="http://10.200.0.1:3128" \
    -e NO_PROXY="localhost,127.0.0.1,::1,10.200.0.1" \
    -e no_proxy="localhost,127.0.0.1,::1,10.200.0.1" \
    "$container" \
    /bin/sh -c "nohup python3 server.py --host 0.0.0.0 --port ${APP_PORT} > app-factory.log 2>&1 &"

  docker exec "$container" curl -sS --max-time 10 "http://127.0.0.1:${APP_PORT}/api/models" >/dev/null
}

start_forward() {
  if [ "$START_FORWARD" -ne 1 ]; then
    log "Skipping host port forward"
    return 0
  fi

  local container container_ip pid_file
  container="$(find_container)"
  [ -n "$container" ] || die "Could not find running sandbox container for '$SANDBOX_NAME'."

  container_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$container")"
  [ -n "$container_ip" ] || die "Could not resolve sandbox container IP."

  log "Forwarding ${HOST_BIND}:${HOST_PORT} -> ${container_ip}:${APP_PORT}"
  stop_forward

  pid_file="/tmp/app_factory_forward_${HOST_PORT}.pid"
  nohup python3 "$APP_DIR/scripts/forward_port.py" \
    --bind-host "$HOST_BIND" \
    --bind-port "$HOST_PORT" \
    --target-host "$container_ip" \
    --target-port "$APP_PORT" \
    >/tmp/app_factory_forward.log 2>&1 &
  echo "$!" >"$pid_file"

  sleep 1
  curl -sS --max-time 10 "http://127.0.0.1:${HOST_PORT}/api/models" >/dev/null
}

print_summary() {
  local host_ip
  host_ip="${APP_FACTORY_PUBLIC_HOST:-}"
  if [ -z "$host_ip" ]; then
    host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  host_ip="${host_ip:-127.0.0.1}"

  log "Ready"
  printf 'Open: http://%s:%s\n' "$host_ip" "$HOST_PORT"
  printf 'Sandbox: %s\n' "$SANDBOX_NAME"
  printf 'Model: %s\n' "$MODEL"
  printf 'Provider: NemoClaw/OpenShell managed inference\n'
}

main() {
  preflight
  ensure_ollama
  ensure_ollama_systemd_loopback
  ensure_model
  run_onboard
  ensure_sandbox
  install_app
  start_forward
  print_summary
}

main "$@"
