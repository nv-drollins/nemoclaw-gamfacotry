# Sandboxed NemoClaw/OpenShell Build

These notes describe the clean Spark build used for the Game Factory demo. The
Game Factory server runs inside the OpenShell sandbox. The Spark host only keeps
the local Ollama model cache and forwards `0.0.0.0:7866` to the sandbox.

## Target Shape

```text
browser
  -> Spark host port 7866
  -> sandbox container port 7866
  -> Game Factory server
  -> https://inference.local/v1
  -> OpenShell managed inference
  -> local Ollama qwen3-coder:30b
```

## Quick Scripted Setup

From the cloned repo root on the Spark:

```bash
./scripts/setup_nemoclaw_app_factory.sh
```

The script checks for Ollama, installs it when missing, starts it when needed,
pulls the configured model if needed, then performs the NemoClaw/OpenShell setup.

Useful overrides:

```bash
./scripts/setup_nemoclaw_app_factory.sh --sandbox app-factory-agent --model qwen3-coder:30b
./scripts/setup_nemoclaw_app_factory.sh --host-port 7866
./scripts/setup_nemoclaw_app_factory.sh --onboard-timeout 300
./scripts/setup_nemoclaw_app_factory.sh --skip-ollama-install
./scripts/setup_nemoclaw_app_factory.sh --skip-onboard
./scripts/setup_nemoclaw_app_factory.sh --force-onboard
```

The remainder of this file shows what the script does and how to modify or
debug each phase manually.

## Clean Existing State

This keeps Ollama models, including `qwen3-coder:30b`, but removes the previous
NemoClaw/OpenShell sandbox state and old demo folders.

```bash
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

nemoclaw vanilla-agent destroy --yes --force || true
nemoclaw uninstall --yes || true
docker rm -f openshell-cluster-nemoclaw 2>/dev/null || true

rm -rf \
  "$HOME/.nemoclaw" \
  "$HOME/nemoclaw-vanilla" \
  "$HOME/nemoclaw-blender" \
  "$HOME/nemoclaw-demos" \
  "$HOME/nemoclaw-nasa-apod" \
  "$HOME/openclaw-app-factory"
```

Verify the model cache is still present:

```bash
ollama list | grep qwen3-coder:30b
```

## Install NemoClaw And Configure Inference

```bash
export NEMOCLAW_SANDBOX_NAME=app-factory-agent
export NEMOCLAW_PROVIDER=ollama
export NEMOCLAW_MODEL=qwen3-coder:30b
export NEMOCLAW_POLICY_TIER=balanced
export NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
export NEMOCLAW_NON_INTERACTIVE=1
export NEMOCLAW_YES=1
export NEMOCLAW_LOCAL_INFERENCE_TIMEOUT=300
export NEMOCLAW_SANDBOX_READY_TIMEOUT=900

curl -fsSL https://www.nvidia.com/nemoclaw.sh -o /tmp/nemoclaw.sh
bash /tmp/nemoclaw.sh --non-interactive --yes-i-accept-third-party-software --fresh || true
```

On this build, `nemoclaw onboard` configured the gateway and local inference,
then failed during sandbox creation because the generated OpenShell command used
the relative `nemoclaw-start` command. The fresh image was valid; creating the
sandbox with the absolute startup path worked. The quick-start script now caps
the onboarding wait and performs this fallback automatically.

On x86/RTX PRO Blackwell hosts, a prior non-GPU OpenShell gateway may cause
NemoClaw onboarding to stop before it creates the `openshell/sandbox-from`
image. The quick-start script detects the "Existing gateway was started without
GPU passthrough" onboarding message, runs `nemoclaw uninstall --yes`, removes
the stale gateway container, and retries onboarding once.

## Create The Sandbox

```bash
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

IMAGE="$(docker images --format '{{.Repository}}:{{.Tag}}' \
  | awk '/^openshell\/sandbox-from:/ {print; exit}')"

nohup openshell sandbox create -g nemoclaw \
  --name app-factory-agent \
  --from "$IMAGE" \
  --provider ollama-local \
  --gpu \
  -- /usr/bin/env \
    CHAT_UI_URL=http://127.0.0.1:18789 \
    NEMOCLAW_DASHBOARD_PORT=18789 \
    /usr/local/bin/nemoclaw-start \
  >/tmp/app-factory-sandbox.log 2>&1 &

openshell sandbox list -g nemoclaw
```

Expected:

```text
app-factory-agent ... Ready
```

Validate managed inference from inside the sandbox container:

```bash
CONTAINER="$(docker ps --format '{{.Names}}' | grep '^openshell-app-factory-agent-' | head -1)"

docker exec "$CONTAINER" /bin/bash -lc \
  'curl -k -sS https://inference.local/v1/models | head -c 500'
```

## Copy And Run The App Inside The Sandbox

From the Spark host, run these commands from the cloned repo root:

```bash
APP_DIR="${APP_DIR:-$PWD}"
CONTAINER="$(docker ps --format '{{.Names}}' | grep '^openshell-app-factory-agent-' | head -1)"

docker exec "$CONTAINER" rm -rf /sandbox/openclaw-app-factory
docker cp "$APP_DIR" "$CONTAINER":/sandbox/openclaw-app-factory
docker exec "$CONTAINER" chown -R sandbox:sandbox /sandbox/openclaw-app-factory
docker exec "$CONTAINER" python3 -m py_compile /sandbox/openclaw-app-factory/server.py

docker exec -u sandbox -w /sandbox/openclaw-app-factory \
  -e APP_FACTORY_PROVIDER=openshell \
  -e APP_FACTORY_MODEL=qwen3-coder:30b \
  -e APP_FACTORY_OPENAI_BASE_URL=https://inference.local/v1 \
  -e APP_FACTORY_OPENAI_INSECURE=1 \
  "$CONTAINER" \
  /bin/bash -lc 'setsid -f python3 server.py --host 0.0.0.0 --port 7866 > app-factory.log 2>&1 < /dev/null'
```

Confirm the app responds inside the sandbox:

```bash
docker exec "$CONTAINER" curl -sS http://127.0.0.1:7866/api/models
```

Expected `host`:

```text
https://inference.local/v1
```

## Forward The Web UI To The Spark

The OpenShell SSH forward was inconsistent in this clean build, so the demo uses
a tiny host-side TCP forwarder. It forwards bytes only; the web app and generated
artifacts still live inside the sandbox.

```bash
CONTAINER_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$CONTAINER")"

nohup python3 ./scripts/forward_port.py \
  --bind-host 0.0.0.0 \
  --bind-port 7866 \
  --target-host "$CONTAINER_IP" \
  --target-port 7866 \
  >/tmp/app_factory_forward.log 2>&1 &
```

Open:

```text
http://192.168.1.164:7866
```

## Verification

```bash
curl -sS http://127.0.0.1:7866/api/models
curl -sS http://127.0.0.1:7866/api/status
```

For the validated run, the app reported:

```text
modelStatus: NemoClaw/OpenShell managed inference
model: qwen3-coder:30b
modelCalls: 2
```

Generated app artifacts were written inside the sandbox under:

```text
/sandbox/openclaw-app-factory/runs/<run_id>/
```
