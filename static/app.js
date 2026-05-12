const state = {
  latestResultUrl: "",
};

const els = {
  prompt: document.querySelector("#prompt"),
  model: document.querySelector("#model"),
  modelStatus: document.querySelector("#modelStatus"),
  start: document.querySelector("#start"),
  reset: document.querySelector("#reset"),
  status: document.querySelector("#status"),
  title: document.querySelector("#title"),
  emptyPreview: document.querySelector("#emptyPreview"),
  preview: document.querySelector("#preview"),
  openResult: document.querySelector("#openResult"),
  reviewNotes: document.querySelector("#reviewNotes"),
  skillMd: document.querySelector("#skillMd"),
  events: document.querySelector("#events"),
  humanPanel: document.querySelector("#humanPanel"),
  feedback: document.querySelector("#feedback"),
  approve: document.querySelector("#approve"),
  refine: document.querySelector("#refine"),
  gpuGauge: document.querySelector("#gpuGauge"),
  gpuValue: document.querySelector("#gpuValue"),
  gpuDetail: document.querySelector("#gpuDetail"),
  memoryGauge: document.querySelector("#memoryGauge"),
  memoryValue: document.querySelector("#memoryValue"),
  memoryDetail: document.querySelector("#memoryDetail"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function setBusy(isBusy) {
  els.start.disabled = isBusy;
  els.reset.disabled = isBusy;
  els.prompt.disabled = isBusy;
  els.model.disabled = isBusy;
  els.refine.disabled = isBusy;
  els.approve.disabled = isBusy;
}

function renderFlow(flow) {
  flow.forEach((step) => {
    const node = document.querySelector(`[data-step="${step.id}"]`);
    if (!node) return;
    node.classList.toggle("active", step.status === "active");
    node.classList.toggle("done", step.status === "done");
    node.classList.toggle("failed", step.status === "failed");
    node.querySelector("span").textContent = step.status;
  });
}

function renderEvents(events) {
  els.events.innerHTML = [...events]
    .slice(-18)
    .reverse()
    .map(
      (event) => `
        <div class="event">
          <strong>${escapeHtml(event.title)}</strong>
          <p>${escapeHtml(event.detail)}</p>
        </div>
      `,
    )
    .join("");
}

function renderResult(result) {
  if (!result || !result.url) {
    els.emptyPreview.classList.remove("hidden");
    els.preview.classList.add("hidden");
    els.openResult.classList.add("hidden");
    state.latestResultUrl = "";
    return;
  }
  els.emptyPreview.classList.add("hidden");
  els.preview.classList.remove("hidden");
  els.openResult.classList.remove("hidden");
  els.openResult.href = result.url;
  els.openResult.textContent = "Open App";
  if (state.latestResultUrl !== result.url) {
    state.latestResultUrl = result.url;
    els.preview.src = result.url;
  }
}

function renderReview(notes, skillMd) {
  els.reviewNotes.innerHTML = notes.length
    ? notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")
    : '<li class="muted">No review notes yet.</li>';
  els.skillMd.textContent = skillMd || "No skill generated yet.";
}

function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, number));
}

function renderGauge(gauge, valueNode, detailNode, reading, fallbackDetail) {
  const percent = clampPercent(reading && reading.percent);
  if (percent === null) {
    gauge.style.setProperty("--value", "0%");
    valueNode.textContent = "--%";
    detailNode.textContent = (reading && reading.detail) || fallbackDetail;
    return;
  }
  gauge.style.setProperty("--value", `${percent}%`);
  valueNode.textContent = `${Math.round(percent)}%`;
  detailNode.textContent = fallbackDetail;
}

function renderTelemetry(data) {
  renderGauge(els.gpuGauge, els.gpuValue, els.gpuDetail, data.gpu, "Live from nvidia-smi");
  const memory = data.memory || {};
  const detail =
    memory.ok && Number.isFinite(memory.usedGiB) && Number.isFinite(memory.totalGiB)
      ? `${memory.usedGiB} / ${memory.totalGiB} GiB used`
      : "Memory telemetry unavailable";
  renderGauge(els.memoryGauge, els.memoryValue, els.memoryDetail, memory, detail);
}

function render(snapshot) {
  els.status.textContent = snapshot.status;
  els.title.textContent = snapshot.title || "Generated app preview";
  if (document.activeElement !== els.prompt && snapshot.status !== "running") {
    els.prompt.value = snapshot.prompt || els.prompt.value;
  }
  setBusy(snapshot.status === "running");
  els.humanPanel.classList.toggle("hidden", !["awaiting_human", "complete"].includes(snapshot.status));
  renderFlow(snapshot.flow || []);
  renderEvents(snapshot.events || []);
  renderResult(snapshot.result);
  renderReview(snapshot.reviewNotes || [], snapshot.skillMd || "");
}

async function refresh() {
  try {
    render(await api("/api/status"));
  } catch (error) {
    els.status.textContent = "offline";
    els.events.innerHTML = `<div class="event"><strong>Connection failed</strong><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function loadModels() {
  try {
    const data = await api("/api/models");
    const models = data.models && data.models.length ? data.models : ["qwen2.5-coder:7b"];
    els.model.innerHTML = models.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
    els.model.value = data.default || models[0];
    els.modelStatus.textContent = data.ok
      ? `Model route ready at ${data.host}`
      : `Model route unavailable; fallback will run. ${data.error || ""}`;
  } catch (error) {
    els.modelStatus.textContent = `Model check failed: ${error.message}`;
  }
}

async function refreshTelemetry() {
  try {
    renderTelemetry(await api("/api/telemetry"));
  } catch (error) {
    renderTelemetry({
      gpu: { ok: false, detail: "GPU telemetry unavailable" },
      memory: { ok: false, detail: "Memory telemetry unavailable" },
    });
  }
}

els.start.addEventListener("click", async () => {
  await api("/api/start", {
    method: "POST",
    body: JSON.stringify({ prompt: els.prompt.value, model: els.model.value }),
  });
  await refresh();
});

els.reset.addEventListener("click", async () => {
  const data = await api("/api/reset", { method: "POST", body: "{}" });
  if (data.prompt) {
    els.prompt.value = data.prompt;
  }
  state.latestResultUrl = "";
  await refresh();
});

els.approve.addEventListener("click", async () => {
  await api("/api/approve", { method: "POST", body: "{}" });
  await refresh();
});

els.refine.addEventListener("click", async () => {
  await api("/api/refine", {
    method: "POST",
    body: JSON.stringify({ feedback: els.feedback.value }),
  });
  els.feedback.value = "";
  await refresh();
});

loadModels();
refresh();
refreshTelemetry();
setInterval(refresh, 1000);
setInterval(refreshTelemetry, 2500);
