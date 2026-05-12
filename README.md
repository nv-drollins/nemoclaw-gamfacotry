# NemoClaw Game Factory

NemoClaw Game Factory is a local, sandboxed demo for generating small web apps
and games from a prompt. A user enters an idea, a builder agent creates a
self-contained `index.html`, a reviewer agent checks and refines it, and the app
is deployed into a live browser preview for human approval or follow-up changes.

The preferred demo path runs the App Factory server inside a NemoClaw/OpenShell
sandbox and routes model calls through OpenShell managed inference:

```text
browser
  -> Spark host port 7866
  -> OpenShell sandbox
  -> App Factory server
  -> https://inference.local/v1
  -> local Ollama qwen3-coder:30b
```

## What It Shows

1. Prompt-to-web-app generation
2. Builder and reviewer agent loop
3. Generated `SKILL.md` correction notes
4. Live deployment into a browser preview
5. Human approval or refinement feedback
6. Local model execution with `qwen3-coder:30b`

## Requirements

- NVIDIA Spark or Linux host with Docker
- NVIDIA driver and container GPU support
- Python 3.11 or newer inside the sandbox
- Ollama with `qwen3-coder:30b`, or network access so the setup script can install/pull it
  and pin Ollama to the Spark GPU-tested `0.22.1` release
- NemoClaw/OpenShell installed by the setup flow below

## Quick Start

Run this on the Spark. The setup script checks for Ollama, installs it if it is
missing, pins it to `0.22.1` for GB10 GPU offload, starts it if needed, pulls `qwen3-coder:30b` if needed,
installs/configures NemoClaw/OpenShell, creates or reuses the sandbox, copies
this app into `/sandbox/openclaw-app-factory`, starts the app in the sandbox, and
forwards Spark port `7866` to the sandboxed server.

```bash
git clone https://github.com/nv-drollins/nemoclaw-gamfacotry.git
cd nemoclaw-gamfacotry

./scripts/setup_nemoclaw_app_factory.sh
```

Open:

```text
http://<spark-ip>:7866
```

Common overrides:

```bash
./scripts/setup_nemoclaw_app_factory.sh --model qwen3-coder:30b --host-port 7866
./scripts/setup_nemoclaw_app_factory.sh --onboard-timeout 300
./scripts/setup_nemoclaw_app_factory.sh --skip-ollama-install
./scripts/setup_nemoclaw_app_factory.sh --skip-onboard
./scripts/setup_nemoclaw_app_factory.sh --force-onboard
```

To override the pinned Ollama release:

```bash
APP_FACTORY_OLLAMA_VERSION=0.22.1 ./scripts/setup_nemoclaw_app_factory.sh
```

Stop or restart the demo after it is installed:

```bash
./stop.sh
./restart.sh
```

For a full sandbox tear-down:

```bash
./stop.sh --delete-sandbox
```

## Ollama Details

The quick-start script handles this automatically. Use these commands when you
want to install or manage Ollama manually.

On Ubuntu or the Spark host, the demo currently pins Ollama to `0.22.1` because
newer `0.23.x` builds have been observed to fall back to CPU-only execution on
this Spark GB10 setup.

Generic Ollama install:

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
```

Pull the coding model used by the demo:

```bash
ollama pull qwen3-coder:30b
ollama list
```

Optional quick model test:

```bash
ollama run qwen3-coder:30b "Reply with OK"
```

If you need Ollama reachable from containers or other hosts, configure the
Ollama service to listen beyond localhost:

```bash
sudo systemctl edit ollama
```

Add:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

## Direct Ollama Mode

This is useful for quick local development without NemoClaw:

```bash
export APP_FACTORY_PROVIDER=ollama
export APP_FACTORY_MODEL=qwen3-coder:30b
python3 server.py --host 0.0.0.0 --port 7866
```

Open:

```text
http://<spark-ip>:7866
```

## Manual NemoClaw/OpenShell Deployment

The quick-start script wraps these steps. Use the manual flow when you want to
modify the installation, debug the sandbox, or run each phase by hand. The
demo-ready path runs the App Factory code inside the OpenShell sandbox. The
Spark host only forwards the browser port.

Clone the repo on the Spark:

```bash
git clone https://github.com/nv-drollins/nemoclaw-gamfacotry.git
cd nemoclaw-gamfacotry
```

Install NemoClaw and configure local Ollama inference:

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

If the onboarding flow creates the sandbox successfully, continue to "Run The
App In The Sandbox" below. On the validated Spark build, the gateway and
inference setup completed, but sandbox creation needed the absolute startup
path workaround below. The quick-start script caps the NemoClaw onboarding wait
and performs this fallback automatically.

Create the sandbox from the fresh NemoClaw image:

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

Validate managed inference:

```bash
CONTAINER="$(docker ps --format '{{.Names}}' | grep '^openshell-app-factory-agent-' | head -1)"

docker exec "$CONTAINER" /bin/bash -lc \
  'curl -k -sS https://inference.local/v1/models | head -c 500'
```

## Run The App In The Sandbox

Copy this repo into `/sandbox/openclaw-app-factory` and start the server as the
sandbox user:

```bash
CONTAINER="$(docker ps --format '{{.Names}}' | grep '^openshell-app-factory-agent-' | head -1)"

docker exec "$CONTAINER" rm -rf /sandbox/openclaw-app-factory
docker cp . "$CONTAINER":/sandbox/openclaw-app-factory
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

Confirm the sandboxed server sees the OpenShell inference route:

```bash
docker exec "$CONTAINER" curl -sS http://127.0.0.1:7866/api/models
```

The response should include:

```json
{
  "host": "https://inference.local/v1",
  "default": "qwen3-coder:30b"
}
```

## Expose The Demo Port

Forward Spark port `7866` to the sandboxed app:

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
http://<spark-ip>:7866
```

## Smoke Tests

Direct server smoke test:

```bash
APP_FACTORY_PROVIDER=ollama python3 server.py --smoke-test --model qwen3-coder:30b
```

Sandboxed route checks:

```bash
curl -sS http://127.0.0.1:7866/api/models
curl -sS http://127.0.0.1:7866/api/status
```

During a successful sandboxed generation, `/api/status` should report:

```text
modelStatus: NemoClaw/OpenShell managed inference
model: qwen3-coder:30b
modelCalls: 2
```

Generated apps are written inside the sandbox:

```text
/sandbox/openclaw-app-factory/runs/<run_id>/
```

For the full clean-rebuild transcript and extra troubleshooting notes, see
[`SANDBOX_BUILD.md`](SANDBOX_BUILD.md).
