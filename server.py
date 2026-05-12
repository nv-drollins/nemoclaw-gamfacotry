#!/usr/bin/env python3
"""Flexible local-model app factory demo."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import random
import re
import shutil
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
RUNS_ROOT = APP_ROOT / "runs"
MODEL_PROVIDER = os.environ.get("APP_FACTORY_PROVIDER", "ollama").strip().lower()
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OPENAI_BASE_URL = os.environ.get("APP_FACTORY_OPENAI_BASE_URL", "https://inference.local/v1").rstrip("/")
OPENAI_API_KEY = os.environ.get("APP_FACTORY_OPENAI_API_KEY", "unused")
OPENAI_INSECURE = os.environ.get("APP_FACTORY_OPENAI_INSECURE", "1").lower() in {"1", "true", "yes", "on"}
DEFAULT_MODEL = os.environ.get("APP_FACTORY_MODEL", "qwen3-coder:30b")
DEFAULT_PROMPT = (
    "Build a simple web based game where a rover collects crystals, avoids hazards, "
    "and shows score, timer, and restart controls."
)
RANDOM_GAME_PROMPTS = [
    DEFAULT_PROMPT,
    "Build a dark-mode asteroid dodger where a tiny ship survives for 60 seconds, collects green energy cells, and pauses with an inline final score when time ends.",
    "Build a simple memory card matching game with twelve cards, a move counter, a restart button, and a compact victory summary.",
    "Build a keyboard-controlled maze game where a robot finds the exit, collects three keys, avoids patrol drones, and shows progress inline.",
    "Build a one-screen reaction game where targets light up in random grid cells, the player clicks them for points, and the game pauses when the timer ends.",
    "Build a falling-block catcher game where a basket catches green tokens, avoids gray hazards, tracks lives and score, and has restart controls.",
    "Build a simple tower defense micro-game where waves move across lanes, the player places limited green turrets, and the round summary appears inline.",
    "Build a space mining game where a rover mines ore nodes before oxygen runs out, with an oxygen meter, score, and restart button.",
    "Build a typing speed game where words appear one at a time, correct entries add score, mistakes reduce time, and results show inline.",
    "Build a puzzle slider game with numbered tiles, move counter, shuffle button, and win state without popups.",
    "Build a rhythm tap game where beats cross a target line, the player presses space to score, and the app shows combo and final score inline.",
    "Build a simple stealth grid game where a scout reaches a goal while avoiding moving sentries, with keyboard controls and a reset button.",
    "Build a resource balancing game where the player routes power between three stations, keeps meters stable for 45 seconds, and sees an inline result.",
    "Build a mini pinball-inspired click game with bumpers, score multipliers, a timer, and a restart control.",
    "Build a color sequence game like Simon with green-accented pads, rounds, mistake count, and an inline game-over state.",
    "Build a lane-switch runner where a bike avoids obstacles, collects boosts, and pauses the run with final stats when lives reach zero.",
    "Build a bug-squash arcade game where bugs appear around the screen, clicks score points, misses reduce energy, and the result displays inline.",
    "Build a compact chess-knight puzzle where the player moves a knight to collect targets on a board with move limits.",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def strip_code_fences(text: str) -> str:
    fenced = re.search(r"```(?:html|markdown|md|text)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced else text.strip()


def extract_section(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}\s*:\s*(.*?)(?=\n[A-Z_ ]{{3,}}\s*:|\Z)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def extract_html(text: str) -> str:
    for pattern in [
        r"```html\s*(.*?)```",
        r"HTML\s*:\s*```(?:html)?\s*(.*?)```",
        r"REFINED_HTML\s*:\s*```(?:html)?\s*(.*?)```",
    ]:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    start = text.lower().find("<!doctype html")
    if start < 0:
        start = text.lower().find("<html")
    if start >= 0:
        return text[start:].strip()
    return ""


def ensure_complete_html(value: str, prompt: str, fallback_html: str = "") -> str:
    candidate = value.strip()
    if "<html" in candidate.lower() and "</html>" in candidate.lower():
        return candidate
    if fallback_html and "<html" in fallback_html.lower() and "</html>" in fallback_html.lower():
        return fallback_html
    return fallback_app_html(prompt, "Recovered App", "The model response was incomplete, so the deployer wrapped it safely.")


def slugify(value: str, fallback: str = "generated-app") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return (slug or fallback)[:64]


def clean_plain_text(value: str, limit: int) -> str:
    cleaned = strip_code_fences(value)
    cleaned = re.sub(r"^[#*\-\s]+", "", cleaned.strip())
    cleaned = re.sub(r"[*_`]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


APP_DIALOG_GUARD = """<script>
(() => {
  if (window.__appFactoryDialogGuard) return;
  window.__appFactoryDialogGuard = true;
  const showNotice = (message) => {
    const text = String(message || 'Game over');
    let notice = document.querySelector('[data-app-factory-notice]');
    if (!notice) {
      notice = document.createElement('div');
      notice.setAttribute('data-app-factory-notice', 'true');
      notice.style.cssText = [
        'position:fixed',
        'left:50%',
        'bottom:20px',
        'transform:translateX(-50%)',
        'z-index:2147483647',
        'max-width:min(560px,calc(100vw - 32px))',
        'padding:12px 14px',
        'border:1px solid #76B900',
        'border-radius:8px',
        'background:#10161c',
        'color:#edf3f7',
        'box-shadow:0 18px 34px rgba(0,0,0,.35)',
        'font:600 14px system-ui,sans-serif',
        'text-align:center'
      ].join(';');
      document.addEventListener('DOMContentLoaded', () => document.body.appendChild(notice), { once: true });
      if (document.body) document.body.appendChild(notice);
    }
    notice.textContent = text;
    window.clearTimeout(notice.__hideTimer);
    notice.__hideTimer = window.setTimeout(() => notice.remove(), 6000);
  };
  window.__appFactoryNotice = showNotice;
  window.alert = showNotice;
  window.confirm = (message) => { showNotice(message); return false; };
  window.prompt = (message, fallback = '') => { showNotice(message); return String(fallback || ''); };
})();
</script>"""


def add_dialog_guard(app_html: str) -> str:
    if "__appFactoryDialogGuard" in app_html:
        return app_html
    lower = app_html.lower()
    head_close = lower.find("</head>")
    if head_close >= 0:
        return app_html[:head_close] + APP_DIALOG_GUARD + "\n" + app_html[head_close:]
    body_open = lower.find("<body")
    if body_open >= 0:
        body_end = app_html.find(">", body_open)
        if body_end >= 0:
            return app_html[: body_end + 1] + "\n" + APP_DIALOG_GUARD + "\n" + app_html[body_end + 1 :]
    return APP_DIALOG_GUARD + "\n" + app_html


def preferred_model(names: list[str]) -> str:
    preferred = [
        DEFAULT_MODEL,
        "qwen3-coder:30b",
        "qwen2.5-coder:32b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:7b",
        "qwen3.6:35b",
        "qwen3:14b",
        "glm-4.7-flash:latest",
    ]
    return next((name for name in preferred if name in names), names[0] if names else DEFAULT_MODEL)


def openai_ssl_context() -> ssl.SSLContext | None:
    if not OPENAI_INSECURE:
        return None
    return ssl._create_unverified_context()


def list_ollama_models() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "host": OLLAMA_HOST, "models": [DEFAULT_MODEL], "default": DEFAULT_MODEL, "error": str(exc)}

    names = [item.get("name", "") for item in payload.get("models", []) if item.get("name")]
    default = preferred_model(names)
    return {"ok": True, "host": OLLAMA_HOST, "models": names or [DEFAULT_MODEL], "default": default, "error": ""}


def list_openai_models() -> dict[str, Any]:
    request = urllib.request.Request(
        f"{OPENAI_BASE_URL}/models",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=5, context=openai_ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "host": OPENAI_BASE_URL,
            "models": [DEFAULT_MODEL],
            "default": DEFAULT_MODEL,
            "error": str(exc),
        }

    names = [item.get("id", "") for item in payload.get("data", []) if item.get("id")]
    return {
        "ok": True,
        "host": OPENAI_BASE_URL,
        "models": names or [DEFAULT_MODEL],
        "default": preferred_model(names),
        "error": "",
    }


def list_models() -> dict[str, Any]:
    if MODEL_PROVIDER in {"openshell", "openai", "nemoclaw"}:
        return list_openai_models()
    return list_ollama_models()


def call_ollama(model: str, prompt: str, timeout: int = 120) -> dict[str, Any]:
    started = time.monotonic()
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.25, "num_ctx": 12288, "num_predict": 9000},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {
            "ok": True,
            "provider": "local Ollama",
            "model": model,
            "elapsed": round(time.monotonic() - started, 2),
            "text": data.get("response", "").strip(),
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "fallback",
            "model": model,
            "elapsed": round(time.monotonic() - started, 2),
            "text": "",
            "error": str(exc),
        }


def call_openai_compatible(model: str, prompt: str, timeout: int = 180) -> dict[str, Any]:
    started = time.monotonic()
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.25,
            "max_tokens": 9000,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{OPENAI_BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=openai_ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = (message.get("content") or choice.get("text") or "").strip()
        return {
            "ok": True,
            "provider": "NemoClaw/OpenShell managed inference",
            "model": model,
            "elapsed": round(time.monotonic() - started, 2),
            "text": text,
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "fallback",
            "model": model,
            "elapsed": round(time.monotonic() - started, 2),
            "text": "",
            "error": str(exc),
        }


def call_model(model: str, prompt: str, timeout: int = 180) -> dict[str, Any]:
    if MODEL_PROVIDER in {"openshell", "openai", "nemoclaw"}:
        return call_openai_compatible(model, prompt, timeout=timeout)
    return call_ollama(model, prompt, timeout=timeout)


def builder_prompt(
    user_prompt: str,
    feedback: list[str],
    previous_summary: str,
    current_html: str = "",
    active_refinement: str = "",
) -> str:
    feedback_text = "\n".join(f"- {item}" for item in feedback) or "- None"
    if current_html:
        return f"""You are the Builder agent in a local NemoClaw/OpenClaw-style app factory demo.

Revise the existing deployed web app. Do not restart from the original idea unless the human explicitly asks for a rebuild.

Original user prompt:
{user_prompt}

Current app summary:
{previous_summary or "None"}

Latest human refinement request:
{active_refinement or "None"}

All human refinement feedback so far:
{feedback_text}

Existing deployed HTML to revise:
```html
{current_html[:14000]}
```

Hard requirements:
- Return exactly the labeled sections below.
- Preserve the current app's core structure, game/application identity, and working controls unless feedback asks to change them.
- Apply the latest human refinement request in the returned HTML.
- If you cannot complete the requested change, return the existing deployed HTML unchanged.
- Never replace the existing app with a generic fallback, sample game, or unrelated app.
- The app must remain a single complete HTML document with embedded CSS and JavaScript.
- Do not use external scripts, external CSS, CDNs, image URLs, or network calls.
- Use a dark visual theme. If you use any accent color, use #76B900.
- Do not use alert(), confirm(), prompt(), modal dialogs, or blocking browser popups.
- If a game or timer ends, pause the game with state flags, stop timers, and show the result inline in the app.
- Keep the result understandable and deployable.

TITLE: short app title
SUMMARY: one sentence describing the revised app
HTML:
```html
<!doctype html>
...
</html>
```
"""

    return f"""You are the Builder agent in a local NemoClaw/OpenClaw-style app factory demo.

Create one small, polished, self-contained web application from the user's prompt.

User prompt:
{user_prompt}

Human refinement feedback so far:
{feedback_text}

Previous app summary:
{previous_summary or "None"}

Hard requirements:
- Return exactly the labeled sections below.
- The app must be a single complete HTML document with embedded CSS and JavaScript.
- Do not use external scripts, external CSS, CDNs, image URLs, or network calls.
- Make the first screen the usable application, not a marketing page.
- If the prompt asks for a game, build a playable browser game with keyboard or button controls.
- Use a dark visual theme. If you use any accent color, use #76B900.
- Do not use alert(), confirm(), prompt(), modal dialogs, or blocking browser popups.
- If a game or timer ends, pause the game with state flags, stop timers, and show the result inline in the app.
- Keep the code understandable and under roughly 500 lines.
- Include visible controls, status, and a restart/reset action when useful.

TITLE: short app title
SUMMARY: one sentence about what the app does
HTML:
```html
<!doctype html>
...
</html>
```
"""


def reviewer_prompt(user_prompt: str, title: str, summary: str, app_html: str) -> str:
    return f"""You are the Reviewer agent in a local app factory demo.

Review and refine the Builder's single-file web app. Improve obvious issues:
- broken layout
- missing controls
- weak mobile behavior
- missing restart/reset path
- inaccessible contrast
- fragile JavaScript
- text overflow
- any alert(), confirm(), prompt(), modal dialog, or blocking browser popup
- game-over/timer-over behavior that should pause the app and show inline status
- colors that should use a dark theme with #76B900 as the accent
- If the Builder HTML is already acceptable, return it unchanged as a complete HTML document.
- Never return only snippets, notes, or a partial document.

User prompt:
{user_prompt}

Builder title:
{title}

Builder summary:
{summary}

Builder HTML:
```html
{app_html[:18000]}
```

Return exactly:
VERDICT: approve or revised
REVIEW_NOTES:
- concise note
- concise note
SKILL_MD:
```markdown
# Skill: short reusable lesson

When refining this kind of app...
```
REFINED_HTML:
```html
<!doctype html>
...
</html>
```
"""


def fallback_app_html(user_prompt: str, title: str = "Rover Crystal Run", note: str = "") -> str:
    escaped_prompt = html.escape(user_prompt)
    escaped_title = html.escape(title)
    escaped_note = html.escape(note or "Generated by the local fallback path because the model response was unavailable.")
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escaped_title}</title>
    <style>
      :root {{ --ink:#edf3f7; --paper:#0c1116; --panel:#141b22; --line:#2c3741; --green:#76B900; }}
      * {{ box-sizing:border-box; }}
      body {{ margin:0; min-height:100vh; background:var(--paper); color:var(--ink); font-family:Inter,ui-sans-serif,system-ui,sans-serif; }}
      main {{ max-width:980px; margin:0 auto; padding:22px; }}
      header {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:14px; }}
      h1 {{ margin:0 0 6px; font-size:28px; }}
      p {{ margin:0; color:#9aa8b2; line-height:1.45; }}
      .hud {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:14px 0; }}
      .hud div, .board, .controls {{ border:1px solid var(--line); border-radius:8px; background:var(--panel); }}
      .hud div {{ padding:12px; }}
      .hud strong {{ display:block; font-size:24px; }}
      .board {{ position:relative; height:420px; overflow:hidden; outline:0; }}
      .tile {{ position:absolute; width:34px; height:34px; display:grid; place-items:center; border-radius:8px; font-weight:900; }}
      .player {{ background:var(--green); color:#071006; }}
      .crystal {{ background:#10161c; color:var(--green); border:2px solid var(--green); }}
      .hazard {{ background:#252d35; color:var(--green); border:2px solid var(--green); }}
      .controls {{ display:flex; gap:10px; flex-wrap:wrap; padding:12px; margin-top:12px; }}
      button {{ min-height:40px; border:1px solid var(--line); border-radius:8px; background:#10161c; color:var(--ink); font-weight:800; padding:0 14px; cursor:pointer; }}
      button.primary {{ background:var(--green); border-color:var(--green); color:#071006; }}
      .note {{ margin-top:12px; color:#9aa8b2; font-size:13px; }}
      @media (max-width:720px) {{ .hud {{ grid-template-columns:repeat(2,1fr); }} .board {{ height:360px; }} header {{ flex-direction:column; }} }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <div>
          <h1>{escaped_title}</h1>
          <p>{escaped_prompt}</p>
        </div>
        <button class="primary" id="restartTop">Restart</button>
      </header>
      <section class="hud">
        <div><strong id="score">0</strong><span>score</span></div>
        <div><strong id="time">45</strong><span>seconds</span></div>
        <div><strong id="crystals">0/8</strong><span>crystals</span></div>
        <div><strong id="state">Ready</strong><span>state</span></div>
      </section>
      <section class="board" id="board" tabindex="0" aria-label="Rover game board"></section>
      <section class="controls">
        <button data-move="up">Up</button>
        <button data-move="left">Left</button>
        <button data-move="down">Down</button>
        <button data-move="right">Right</button>
        <button id="restart">Restart</button>
      </section>
      <p class="note">{escaped_note} Use arrow keys or the controls to collect crystals and avoid hazards.</p>
    </main>
    <script>
      const board = document.querySelector('#board');
      const scoreEl = document.querySelector('#score');
      const timeEl = document.querySelector('#time');
      const crystalsEl = document.querySelector('#crystals');
      const stateEl = document.querySelector('#state');
      const size = 10;
      let player, crystals, hazards, score, timeLeft, timer, running;

      function keyOf(p) {{ return `${{p.x}},${{p.y}}`; }}
      function randomCell(occupied) {{
        let cell;
        do {{ cell = {{ x: Math.floor(Math.random()*size), y: Math.floor(Math.random()*size) }}; }}
        while (occupied.has(keyOf(cell)));
        occupied.add(keyOf(cell));
        return cell;
      }}
      function reset() {{
        clearInterval(timer);
        const occupied = new Set();
        player = {{ x: 0, y: 0 }};
        occupied.add(keyOf(player));
        crystals = Array.from({{length:8}}, () => randomCell(occupied));
        hazards = Array.from({{length:7}}, () => randomCell(occupied));
        score = 0; timeLeft = 45; running = true;
        stateEl.textContent = 'Playing';
        timer = setInterval(() => {{ timeLeft--; if (timeLeft <= 0) finish('Time'); render(); }}, 1000);
        render();
        board.focus();
      }}
      function finish(label) {{ running = false; clearInterval(timer); stateEl.textContent = label; }}
      function move(dx, dy) {{
        if (!running) return;
        player.x = Math.max(0, Math.min(size-1, player.x + dx));
        player.y = Math.max(0, Math.min(size-1, player.y + dy));
        if (hazards.some(h => h.x === player.x && h.y === player.y)) {{ score = Math.max(0, score - 2); stateEl.textContent = 'Hazard'; }}
        const before = crystals.length;
        crystals = crystals.filter(c => !(c.x === player.x && c.y === player.y));
        if (crystals.length < before) {{ score += 5; stateEl.textContent = 'Crystal'; }}
        if (crystals.length === 0) finish('Won');
        render();
      }}
      function drawTile(className, text, cell) {{
        const tile = document.createElement('div');
        tile.className = `tile ${{className}}`;
        tile.textContent = text;
        tile.style.left = `calc(${{cell.x}} * 10% + 4px)`;
        tile.style.top = `calc(${{cell.y}} * 10% + 4px)`;
        tile.style.width = 'calc(10% - 8px)';
        tile.style.height = 'calc(10% - 8px)';
        board.appendChild(tile);
      }}
      function render() {{
        board.innerHTML = '';
        hazards.forEach(h => drawTile('hazard', '!', h));
        crystals.forEach(c => drawTile('crystal', '*', c));
        drawTile('player', 'R', player);
        scoreEl.textContent = score;
        timeEl.textContent = timeLeft;
        crystalsEl.textContent = `${{8 - crystals.length}}/8`;
      }}
      document.addEventListener('keydown', (event) => {{
        if (event.key === 'ArrowUp') move(0,-1);
        if (event.key === 'ArrowDown') move(0,1);
        if (event.key === 'ArrowLeft') move(-1,0);
        if (event.key === 'ArrowRight') move(1,0);
      }});
      document.querySelectorAll('[data-move]').forEach(button => button.addEventListener('click', () => {{
        const moves = {{ up:[0,-1], down:[0,1], left:[-1,0], right:[1,0] }};
        move(...moves[button.dataset.move]);
      }}));
      document.querySelector('#restart').addEventListener('click', reset);
      document.querySelector('#restartTop').addEventListener('click', reset);
      reset();
    </script>
  </body>
</html>
"""


def fallback_skill_md(user_prompt: str, review_notes: list[str]) -> str:
    notes = "\n".join(f"- {note}" for note in review_notes) or "- Keep generated apps self-contained and visibly interactive."
    return f"""# Skill: Refine Single-File Web Apps

When generating a browser app from a short prompt:

{notes}

Prompt context:

```text
{user_prompt[:500]}
```
"""


class DemoState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.reset()

    def reset(self, prompt: str | None = None) -> None:
        with self.lock:
            self.run_id = ""
            self.version = 0
            self.status = "idle"
            self.phase = "idle"
            self.prompt = prompt or DEFAULT_PROMPT
            self.model = DEFAULT_MODEL
            self.model_status = "unchecked"
            self.feedback: list[str] = []
            self.events: list[dict[str, str]] = []
            self.flow = [
                {"id": "builder", "label": "Builder", "status": "queued"},
                {"id": "reviewer", "label": "Reviewer", "status": "queued"},
                {"id": "deployer", "label": "Deployer", "status": "queued"},
                {"id": "human", "label": "Human Check", "status": "queued"},
            ]
            self.result: dict[str, str] = {}
            self.title = ""
            self.summary = ""
            self.review_notes: list[str] = []
            self.skill_md = ""
            self.metrics = {"modelCalls": 0, "versions": 0, "refinements": 0}
            self.log("system", "Ready", "Enter a prompt or use Random Game to start from a curated game idea.")

    def log(self, kind: str, title: str, detail: str) -> None:
        self.events.append({"time": utc_now(), "kind": kind, "title": title, "detail": detail})
        self.events = self.events[-80:]

    def set_flow(self, step_id: str, status: str) -> None:
        for step in self.flow:
            if step["id"] == step_id:
                step["status"] = status

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "runId": self.run_id,
                "version": self.version,
                "status": self.status,
                "phase": self.phase,
                "prompt": self.prompt,
                "model": self.model,
                "modelStatus": self.model_status,
                "feedback": list(self.feedback),
                "events": list(self.events),
                "flow": list(self.flow),
                "result": dict(self.result),
                "title": self.title,
                "summary": self.summary,
                "reviewNotes": list(self.review_notes),
                "skillMd": self.skill_md,
                "metrics": dict(self.metrics),
            }


STATE = DemoState()


def run_dir() -> Path:
    return RUNS_ROOT / STATE.run_id


def publish_current_app(app_html: str, title: str, summary: str, skill_md: str) -> None:
    app_html = add_dialog_guard(app_html)
    base = run_dir()
    version_dir = base / f"v{STATE.version}"
    live_dir = base / "live"
    write_text(version_dir / "index.html", app_html)
    write_text(version_dir / "SKILL.md", skill_md)
    write_text(version_dir / "README.md", f"# {title}\n\n{summary}\n")
    if live_dir.exists():
        shutil.rmtree(live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)
    write_text(live_dir / "index.html", app_html)
    write_text(live_dir / "SKILL.md", skill_md)
    write_text(live_dir / "README.md", f"# {title}\n\n{summary}\n")
    with STATE.lock:
        STATE.result = {
            "title": title,
            "summary": summary,
            "url": f"/apps/{STATE.run_id}/index.html?v={STATE.version}-{int(time.time())}",
            "skillUrl": f"/apps/{STATE.run_id}/SKILL.md?v={STATE.version}-{int(time.time())}",
        }


def read_current_live_html() -> str:
    with STATE.lock:
        run_id = STATE.run_id
    if not run_id:
        return ""
    path = RUNS_ROOT / run_id / "live" / "index.html"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def parse_builder_response(
    user_prompt: str,
    response: dict[str, Any],
    previous_html: str = "",
    previous_title: str = "",
    previous_summary: str = "",
) -> dict[str, str]:
    text = response.get("text", "")
    title = clean_plain_text(extract_section(text, "TITLE").splitlines()[0], 80) if text else ""
    summary = clean_plain_text(extract_section(text, "SUMMARY").splitlines()[0], 180) if text else ""
    app_html = extract_html(text)
    if not response.get("ok") or not app_html:
        if previous_html:
            title = previous_title or "Revised Web App"
            summary = previous_summary or "Preserved the last deployed app because the refinement response was incomplete."
            app_html = previous_html
        else:
            title = "Rover Crystal Run" if "game" in user_prompt.lower() else "Generated Web App"
            summary = "Fallback app generated locally because the model response was unavailable or incomplete."
            app_html = fallback_app_html(user_prompt, title, response.get("error", ""))
    return {
        "title": title or "Generated Web App",
        "summary": summary or "A self-contained web app generated from the prompt.",
        "html": ensure_complete_html(app_html, user_prompt, previous_html),
    }


def parse_reviewer_response(user_prompt: str, builder: dict[str, str], response: dict[str, Any]) -> dict[str, Any]:
    text = response.get("text", "")
    notes_block = extract_section(text, "REVIEW_NOTES")
    notes = [line.strip("- ").strip() for line in notes_block.splitlines() if line.strip()]
    refined = extract_html(extract_section(text, "REFINED_HTML")) or extract_html(text)
    skill_md = strip_code_fences(extract_section(text, "SKILL_MD"))
    if not response.get("ok") or not refined:
        notes = notes or [
            "Kept the app self-contained.",
            "Ensured the result has visible controls and a reset path.",
            "Preserved a deployable single-file HTML artifact.",
        ]
        refined = builder["html"]
    if not skill_md:
        skill_md = fallback_skill_md(user_prompt, notes)
    return {"notes": notes[:8], "html": ensure_complete_html(refined, user_prompt, builder["html"]), "skill_md": skill_md}


def run_generation(user_prompt: str, model: str, refinement: str = "", smoke_test: bool = False) -> None:
    try:
        previous_html = read_current_live_html()
        with STATE.lock:
            previous_title = STATE.title
            previous_summary = STATE.summary
            if not STATE.run_id:
                STATE.run_id = uuid.uuid4().hex[:8]
                target = run_dir()
                if target.exists():
                    shutil.rmtree(target)
            STATE.version += 1
            STATE.status = "running"
            STATE.phase = "builder"
            STATE.prompt = user_prompt
            STATE.model = model
            STATE.feedback.append(refinement) if refinement else None
            STATE.flow = [
                {"id": "builder", "label": "Builder", "status": "active"},
                {"id": "reviewer", "label": "Reviewer", "status": "queued"},
                {"id": "deployer", "label": "Deployer", "status": "queued"},
                {"id": "human", "label": "Human Check", "status": "queued"},
            ]
            STATE.metrics["versions"] = STATE.version
            STATE.metrics["refinements"] = max(0, STATE.version - 1)
            if refinement and previous_html:
                detail = f"Revising version {STATE.version - 1} into version {STATE.version} with {model}."
            else:
                detail = f"Generating version {STATE.version} with {model}."
            STATE.log("builder", "Builder started", detail)

        builder_result = call_model(
            model,
            builder_prompt(
                user_prompt,
                STATE.feedback,
                previous_summary,
                current_html=previous_html,
                active_refinement=refinement,
            ),
        )
        with STATE.lock:
            STATE.metrics["modelCalls"] += 1
            STATE.model_status = builder_result["provider"]
            if builder_result["ok"]:
                STATE.log("builder", "Builder completed", f"Model route responded in {builder_result['elapsed']}s.")
            else:
                STATE.log("builder", "Builder fallback", builder_result["error"])
        builder = parse_builder_response(
            user_prompt,
            builder_result,
            previous_html=previous_html,
            previous_title=previous_title,
            previous_summary=previous_summary,
        )
        if not smoke_test:
            time.sleep(0.4)

        with STATE.lock:
            STATE.set_flow("builder", "done")
            STATE.set_flow("reviewer", "active")
            STATE.phase = "reviewer"
            STATE.log("reviewer", "Reviewer started", "Reviewing layout, controls, mobile behavior, and deployability.")
        reviewer_result = call_model(model, reviewer_prompt(user_prompt, builder["title"], builder["summary"], builder["html"]))
        with STATE.lock:
            STATE.metrics["modelCalls"] += 1
            STATE.model_status = reviewer_result["provider"]
            if reviewer_result["ok"]:
                STATE.log("reviewer", "Reviewer completed", f"Model route responded in {reviewer_result['elapsed']}s.")
            else:
                STATE.log("reviewer", "Reviewer fallback", reviewer_result["error"])
        reviewed = parse_reviewer_response(user_prompt, builder, reviewer_result)
        if not smoke_test:
            time.sleep(0.4)

        with STATE.lock:
            STATE.set_flow("reviewer", "done")
            STATE.set_flow("deployer", "active")
            STATE.phase = "deployer"
            STATE.title = builder["title"]
            STATE.summary = builder["summary"]
            STATE.review_notes = reviewed["notes"]
            STATE.skill_md = reviewed["skill_md"]
            STATE.log("deployer", "Deploying app", "Writing the reviewed app to the live preview.")
        publish_current_app(reviewed["html"], builder["title"], builder["summary"], reviewed["skill_md"])
        if not smoke_test:
            time.sleep(0.3)

        with STATE.lock:
            STATE.set_flow("deployer", "done")
            STATE.set_flow("human", "active")
            STATE.phase = "human"
            STATE.status = "awaiting_human"
            STATE.log("human", "Ready for human check", "Approve the app or add feedback to send it back through the loop.")
    except Exception as exc:  # pragma: no cover - visible in UI
        with STATE.lock:
            STATE.status = "failed"
            STATE.phase = "error"
            STATE.log("error", f"{type(exc).__name__}: {exc}", traceback.format_exc())


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenClawAppFactory/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json(STATE.snapshot())
            return
        if parsed.path == "/api/models":
            self.send_json(list_models())
            return
        if parsed.path == "/":
            self.serve_file(STATIC_ROOT / "index.html")
            return
        if parsed.path.startswith("/apps/"):
            self.serve_app_file(parsed.path)
            return
        self.serve_file((STATIC_ROOT / parsed.path.lstrip("/")).resolve())

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        body = self.read_body()
        if parsed.path == "/api/start":
            prompt = str(body.get("prompt") or DEFAULT_PROMPT).strip() or DEFAULT_PROMPT
            model = str(body.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
            with STATE.lock:
                if STATE.worker and STATE.worker.is_alive():
                    self.send_json({"ok": False, "message": "Generation is already running."}, status=409)
                    return
                STATE.reset()
                STATE.worker = threading.Thread(target=run_generation, args=(prompt, model), daemon=True)
                STATE.worker.start()
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/refine":
            feedback = str(body.get("feedback") or "").strip()
            if not feedback:
                self.send_json({"ok": False, "message": "Feedback is required."}, status=400)
                return
            with STATE.lock:
                if STATE.worker and STATE.worker.is_alive():
                    self.send_json({"ok": False, "message": "Generation is already running."}, status=409)
                    return
                if not STATE.run_id:
                    self.send_json({"ok": False, "message": "Start a run first."}, status=409)
                    return
                STATE.worker = threading.Thread(
                    target=run_generation,
                    args=(STATE.prompt, STATE.model, feedback),
                    daemon=True,
                )
                STATE.worker.start()
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/approve":
            with STATE.lock:
                STATE.status = "complete"
                STATE.phase = "complete"
                STATE.set_flow("human", "done")
                STATE.log("human", "Final app approved", "The current deployed app is marked as final.")
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/reset":
            prompt = random.choice(RANDOM_GAME_PROMPTS)
            STATE.reset(prompt=prompt)
            self.send_json({"ok": True, "prompt": prompt})
            return
        self.send_json({"error": "Not found"}, status=404)

    def serve_file(self, path: Path) -> None:
        root = STATIC_ROOT.resolve()
        path = path.resolve()
        if root not in path.parents and path != root:
            self.send_bytes(b"Not found", "text/plain", 404)
            return
        if not path.is_file():
            self.send_bytes(b"Not found", "text/plain", 404)
            return
        self.send_bytes(path.read_bytes(), mimetypes.guess_type(str(path))[0] or "application/octet-stream")

    def serve_app_file(self, path: str) -> None:
        parts = [urllib.parse.unquote(part) for part in path.split("/") if part]
        if len(parts) < 3:
            self.send_bytes(b"Not found", "text/plain", 404)
            return
        run_id = parts[1]
        rel_name = "/".join(parts[2:])
        target = (RUNS_ROOT / run_id / "live" / rel_name).resolve()
        root = (RUNS_ROOT / run_id / "live").resolve()
        if root not in target.parents and target != root:
            self.send_bytes(b"Not found", "text/plain", 404)
            return
        if not target.is_file():
            self.send_bytes(b"Not found", "text/plain", 404)
            return
        self.send_bytes(target.read_bytes(), mimetypes.guess_type(str(target))[0] or "text/plain")


def smoke_test(model: str) -> int:
    STATE.reset()
    run_generation(DEFAULT_PROMPT, model, smoke_test=True)
    snapshot = STATE.snapshot()
    print(json.dumps({"status": snapshot["status"], "modelStatus": snapshot["modelStatus"], "result": snapshot["result"]}, indent=2))
    if snapshot["status"] != "awaiting_human":
        return 1
    if not snapshot["result"].get("url"):
        return 1
    if not (run_dir() / "live" / "index.html").is_file():
        return 1
    return 0


def run_server(host: str, port: int) -> None:
    STATIC_ROOT.mkdir(parents=True, exist_ok=True)
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"OpenClaw app factory listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping app factory.", flush=True)
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OpenClaw App Factory demo.")
    parser.add_argument("--host", default=os.environ.get("APP_FACTORY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("APP_FACTORY_PORT", "7866")))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--smoke-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.smoke_test:
        return smoke_test(args.model)
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
