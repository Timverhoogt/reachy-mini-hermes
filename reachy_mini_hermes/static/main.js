const $ = (id) => document.getElementById(id);
const fields = [
  "bridge_url", "api_key", "model", "language", "continuous_conversation",
  "motion_enabled", "end_silence_seconds", "max_utterance_seconds", "vad_min_rms",
  "vad_noise_multiplier", "wake_keyword_threshold", "wake_keyword_score",
];
let loaded = false;

function setMessage(text, kind = "") {
  const el = $("form-message");
  el.textContent = text;
  el.className = `message ${kind}`;
}

function fillConfig(config) {
  if (loaded || !config) return;
  fields.forEach((name) => {
    const el = $(name);
    if (!el || !(name in config)) return;
    if (el.type === "checkbox") el.checked = Boolean(config[name]);
    else el.value = config[name] ?? "";
  });
  loaded = true;
}

function updateStatus(payload) {
  const runtime = payload.runtime || {};
  const state = runtime.state || "unknown";
  $("runtime-state").textContent = state.replaceAll("_", " ");
  $("runtime-detail").textContent = runtime.detail || "";
  $("last-transcript").textContent = runtime.transcript || "—";
  $("last-response").textContent = runtime.response_preview || "—";
  const dot = $("status-dot");
  dot.className = "status-dot";
  if (["waiting_for_wake_word", "listening", "thinking", "speaking"].includes(state)) dot.classList.add("ready");
  if (["error", "configuration_error"].includes(state)) dot.classList.add("error");
  fillConfig(payload.config);
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    updateStatus(await response.json());
  } catch (error) {
    $("runtime-state").textContent = "Disconnected";
    $("runtime-detail").textContent = String(error);
    $("status-dot").className = "status-dot error";
  }
}

function payloadFromForm() {
  const payload = {};
  fields.forEach((name) => {
    const el = $(name);
    if (!el) return;
    if (el.type === "checkbox") payload[name] = el.checked;
    else if (el.type === "number") payload[name] = Number(el.value);
    else payload[name] = el.value.trim();
  });
  return payload;
}

$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  setMessage("Saving…");
  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadFromForm()),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    setMessage(body.note || "Saved", "ok");
    $("api_key").value = "********";
    await refreshStatus();
  } catch (error) {
    setMessage(String(error), "error");
  } finally {
    button.disabled = false;
  }
});

$("test-button").addEventListener("click", async () => {
  const button = $("test-button");
  button.disabled = true;
  setMessage("Testing Hermes bridge…");
  try {
    const response = await fetch("/api/test-connection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadFromForm()),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    setMessage(`Connected: ${body.health.status || "ok"}`, "ok");
  } catch (error) {
    setMessage(String(error), "error");
  } finally {
    button.disabled = false;
  }
});

refreshStatus();
setInterval(refreshStatus, 1500);
