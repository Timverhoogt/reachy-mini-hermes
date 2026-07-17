const $ = (id) => document.getElementById(id);
const fields = [
  "bridge_url", "api_key", "model", "conversation_mode", "language", "stt_provider", "stt_model",
  "tts_provider", "tts_model", "tts_voice", "continuous_conversation",
  "motion_enabled", "barge_in_enabled", "camera_enabled", "realtime_model", "realtime_voice", "realtime_reasoning_effort",
  "end_silence_seconds", "max_utterance_seconds", "vad_min_rms", "vad_noise_multiplier",
  "wake_keyword_threshold", "wake_keyword_score",
];
let loaded = false;
let currentConfig = null;
let voiceOptions = { stt: [], tts: [] };

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
  toggleModePanels();
}

function toggleModePanels() {
  const realtime = $("conversation_mode").value === "realtime";
  $("realtime-settings").hidden = !realtime;
  $("voice-provider").textContent = realtime
    ? "OpenAI Realtime streams speech in both directions; pipeline STT/TTS selectors are ignored."
    : "Wake detection stays local. Selected STT and TTS run through the authenticated Hermes-host bridge.";
}

$("conversation_mode").addEventListener("change", toggleModePanels);

function updateStatus(payload) {
  const runtime = payload.runtime || {};
  const state = runtime.state || "unknown";
  $("runtime-state").textContent = state.replaceAll("_", " ");
  $("runtime-detail").textContent = runtime.detail || "";
  $("last-transcript").textContent = runtime.transcript || "—";
  $("last-response").textContent = runtime.response_preview || "—";
  const dot = $("status-dot");
  dot.className = "status-dot";
  if (["waiting_for_wake_word", "listening", "looking", "thinking", "speaking"].includes(state)) dot.classList.add("ready");
  if (["error", "configuration_error"].includes(state)) dot.classList.add("error");
  fillConfig(payload.config);
  currentConfig = payload.config || currentConfig;
}

function modelLabel(model) {
  if (model.id === "hermes-agent") return "Hermes default model";
  const root = model.root && model.root !== model.id ? model.root : model.id;
  return `${root} — ${model.id}`;
}

async function loadModels() {
  const select = $("model");
  const selected = currentConfig?.model || select.value || "hermes-agent";
  try {
    const response = await fetch("/api/models", { cache: "no-store" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    select.replaceChildren();
    (body.models || []).forEach((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = modelLabel(model);
      select.appendChild(option);
    });
    if (![...select.options].some((option) => option.value === selected)) {
      const option = document.createElement("option");
      option.value = selected;
      option.textContent = `${selected} — unavailable route`;
      select.appendChild(option);
    }
    select.value = selected;
    const health = body.health || {};
    const tts = health.tts_provider || "configured Hermes provider";
    const stt = health.stt_provider || "configured Hermes provider";
    $("voice-provider").textContent = `Speech voice: ${tts} TTS · Recognition: ${stt} STT. These are independent from the agent model.`;
  } catch (error) {
    $("model-help").textContent = `Could not load Hermes model routes: ${String(error)}`;
  }
}

function replaceOptions(select, values, selected, label = (value) => value) {
  select.replaceChildren();
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = typeof value === "string" ? value : value.id;
    option.textContent = label(value);
    select.appendChild(option);
  });
  if (selected && ![...select.options].some((option) => option.value === selected)) {
    const option = document.createElement("option");
    option.value = selected;
    option.textContent = selected;
    select.appendChild(option);
  }
  if (selected) select.value = selected;
}

function refreshSpeechControls() {
  const sttSelected = $("stt_provider").value || currentConfig?.stt_provider || "configured";
  const ttsSelected = $("tts_provider").value || currentConfig?.tts_provider || "configured";
  const stt = voiceOptions.stt.find((item) => item.id === sttSelected) || {};
  const tts = voiceOptions.tts.find((item) => item.id === ttsSelected) || {};
  replaceOptions($("stt_model"), stt.models || [], currentConfig?.stt_model || "base");
  replaceOptions($("tts_model"), tts.models || [], currentConfig?.tts_model || "eleven_flash_v2_5");
  replaceOptions(
    $("tts_voice"),
    tts.voices || [],
    currentConfig?.tts_voice || "pNInz6obpgDQGcFmaJgB",
    (voice) => `${voice.name} — ${voice.id}`,
  );
}

async function loadVoiceOptions() {
  try {
    const response = await fetch("/api/voice-options", { cache: "no-store" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    voiceOptions = body;
    replaceOptions(
      $("stt_provider"), body.stt || [], currentConfig?.stt_provider || "configured", (item) => item.label,
    );
    replaceOptions(
      $("tts_provider"), body.tts || [], currentConfig?.tts_provider || "configured", (item) => item.label,
    );
    refreshSpeechControls();
    $("voice-provider").textContent = "Wake detection stays local. Selected STT and TTS run through the authenticated Hermes-host bridge.";
    toggleModePanels();
  } catch (error) {
    $("voice-provider").textContent = `Could not load speech providers: ${String(error)}`;
  }
}

$("stt_provider").addEventListener("change", () => {
  currentConfig = { ...currentConfig, stt_provider: $("stt_provider").value, stt_model: "" };
  refreshSpeechControls();
});
$("tts_provider").addEventListener("change", () => {
  currentConfig = { ...currentConfig, tts_provider: $("tts_provider").value, tts_model: "", tts_voice: "" };
  refreshSpeechControls();
});

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

$("camera-test-button").addEventListener("click", async () => {
  const button = $("camera-test-button");
  button.disabled = true;
  setMessage("Capturing one local camera frame…");
  try {
    const response = await fetch("/api/camera/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "camera" }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    setMessage(`Camera ready: ${body.bytes} byte JPEG captured locally`, "ok");
  } catch (error) {
    setMessage(String(error), "error");
  } finally {
    button.disabled = false;
  }
});

async function setPowerMode(mode, durationMinutes = 60) {
  const message = $("power-message");
  message.textContent = `Switching to ${mode}…`;
  try {
    const response = await fetch("/api/power", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, duration_minutes: durationMinutes }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    message.textContent = `Power mode: ${body.runtime.power_mode}`;
    message.className = "message ok";
    await refreshStatus();
  } catch (error) {
    message.textContent = String(error);
    message.className = "message error";
  }
}

document.querySelectorAll("[data-power]").forEach((button) => {
  button.addEventListener("click", () => setPowerMode(button.dataset.power, Number(button.dataset.minutes || 60)));
});

$("app-off-button").addEventListener("click", async () => {
  if (!window.confirm("Stop the voice app? Restart it later from Reachy Control.")) return;
  await fetch("/api/app-off", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: "off" }),
  });
  $("power-message").textContent = "Voice app is stopping";
});

$("shutdown-button").addEventListener("click", async () => {
  if (window.prompt("Type SHUTDOWN to safely power off the Pi") !== "SHUTDOWN") return;
  await fetch("/api/shutdown", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: "shutdown" }),
  });
  $("power-message").textContent = "Pi is shutting down safely";
});

async function startUi() {
  await refreshStatus();
  await Promise.all([loadModels(), loadVoiceOptions()]);
}

startUi();
setInterval(refreshStatus, 1500);
