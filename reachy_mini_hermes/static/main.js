const $ = (id) => document.getElementById(id);
const fields = [
  "bridge_url", "api_key", "model", "conversation_mode", "language", "stt_provider", "stt_model",
  "tts_provider", "tts_model", "tts_voice", "continuous_conversation",
  "motion_enabled", "barge_in_enabled", "camera_enabled", "camera_feed_enabled", "face_tracking_enabled", "face_tracking_weight",
  "doa_enabled", "robot_tools_enabled", "realtime_model", "realtime_voice", "realtime_reasoning_effort",
  "end_silence_seconds", "max_utterance_seconds", "vad_min_rms", "vad_noise_multiplier",
  "wake_keyword_threshold", "wake_keyword_score",
];
let loaded = false;
let currentConfig = null;
let voiceOptions = { stt: [], tts: [] };
let manualActionPending = false;
let powerTransitionPending = false;
let statusRefreshPending = false;
let currentPowerMode = "unknown";
let lastMotorAnnouncement = "";
let deferredInstallPrompt = null;
let announcementRequestPending = false;
let lastAnnouncementLiveText = "";

const announcementText = $("announcement-text");
announcementText.value = window.sessionStorage.getItem("reachy-hermes-announcement-draft") || "";
$("announcement-count").textContent = `${announcementText.value.length.toLocaleString()} / 15,000`;

document.querySelectorAll(".manual-control, [data-power]").forEach((button) => { button.disabled = true; });
$("emotion-select").disabled = true;
$("announcement-send").disabled = true;
$("announcement-stop").disabled = true;

function activateTab(name, focus = false, recordHistory = false) {
  const target = document.querySelector(`[data-tab="${name}"]`) || document.querySelector("[data-tab]");
  if (!target) return;
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const active = button === target;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  if (target.dataset.tab !== "robot" && window.ReachyCamera?.isActive()) {
    window.ReachyCamera.stop("Camera stopped when leaving the Robot tab.");
  }
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== target.dataset.tab;
  });
  window.localStorage.setItem("reachy-hermes-tab", target.dataset.tab);
  if (recordHistory && window.location.hash !== `#${target.dataset.tab}`) {
    window.history.pushState(null, "", `#${target.dataset.tab}`);
  }
  if (focus) target.focus();
}

const tabButtons = [...document.querySelectorAll("[data-tab]")];
tabButtons.forEach((button, index) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab, false, true));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (event.key === "ArrowLeft") next = (index - 1 + tabButtons.length) % tabButtons.length;
    if (event.key === "ArrowRight") next = (index + 1) % tabButtons.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabButtons.length - 1;
    activateTab(tabButtons[next].dataset.tab, true, true);
  });
});
const initialTab = window.location.hash.slice(1) || window.localStorage.getItem("reachy-hermes-tab") || "dashboard";
activateTab(initialTab);
window.addEventListener("popstate", () => activateTab(window.location.hash.slice(1) || "dashboard"));

function runningStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

function updateInstallUi() {
  const button = $("install-button");
  const status = $("install-status");
  const message = $("install-message");
  const help = $("install-help");
  if (runningStandalone()) {
    button.hidden = true;
    status.textContent = "Installed";
    message.textContent = "Running as the Reachy Hermes home-screen app.";
    message.className = "message ok";
    help.hidden = true;
    return;
  }
  help.hidden = false;
  if (deferredInstallPrompt) {
    button.hidden = false;
    status.textContent = "Ready";
    message.textContent = "Chrome is ready to install the standalone app.";
    message.className = "message ok";
    return;
  }
  button.hidden = true;
  status.textContent = window.isSecureContext ? "Web app" : "Shortcut";
  message.textContent = window.isSecureContext
    ? "Chrome will enable Install app when its PWA checks are complete."
    : "This LAN address uses HTTP. Use Chrome's ⋮ menu and Add to Home screen, or open the dashboard through trusted HTTPS for full app installation.";
  message.className = "message";
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  updateInstallUi();
});

window.addEventListener("appinstalled", () => {
  deferredInstallPrompt = null;
  updateInstallUi();
});

$("install-button").addEventListener("click", async () => {
  if (!deferredInstallPrompt) return;
  const prompt = deferredInstallPrompt;
  deferredInstallPrompt = null;
  await prompt.prompt();
  const choice = await prompt.userChoice;
  if (choice.outcome === "accepted") {
    $("install-button").hidden = true;
    $("install-status").textContent = "Installing";
    $("install-help").hidden = true;
    $("install-message").textContent = "Installation accepted. Reachy Hermes is being added to your home screen.";
    $("install-message").className = "message ok";
  } else {
    updateInstallUi();
    $("install-message").textContent = "Installation dismissed. You can try again from Chrome's menu.";
  }
});

async function registerPwa() {
  updateInstallUi();
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  try {
    await navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
  } catch (error) {
    $("install-message").textContent = `App installation support could not start: ${String(error)}`;
    $("install-message").className = "message error";
  }
}
registerPwa();

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
  const powerMode = runtime.power_mode || "unknown";
  currentPowerMode = powerMode;
  const robotBusy = Boolean(runtime.robot_action_busy);
  const motorsEnabled = runtime.motors_enabled;
  const headSafelyFolded = Boolean(runtime.head_safely_folded);
  const controlsBlocked = ["meeting", "sleep"].includes(powerMode)
    || robotBusy || manualActionPending || powerTransitionPending;
  $("power-mode-badge").textContent = powerMode;
  $("robot-mode-badge").textContent = robotBusy ? "moving" : powerMode;
  const robotActionLabels = {
    move_reachy_head: "Look direction",
    express_reachy_emotion: "Expression preset",
    dance_reachy: "Dance preset",
  };
  $("last-robot-action").textContent = robotActionLabels[runtime.last_robot_action]
    || runtime.last_robot_action
    || "—";
  $("robot-action-error").textContent = runtime.robot_action_last_error
    ? ` · ${runtime.robot_action_last_error}`
    : "";
  const motorStateText = motorsEnabled === true
    ? `Torque on · ${headSafelyFolded ? "folded pose" : "active pose"}`
    : motorsEnabled === false
      ? `Torque off · ${headSafelyFolded ? "folded safely" : "pose unconfirmed"}`
      : "Motor state unavailable";
  $("motor-state").textContent = motorStateText;
  const motorDot = $("motor-state-dot");
  motorDot.className = "motor-state-dot";
  if (motorsEnabled === true) motorDot.classList.add("on");
  if (motorsEnabled === false) motorDot.classList.add("off");
  if (runtime.last_error && motorsEnabled !== false) motorDot.classList.add("error");
  document.querySelector(".robot-control-card").setAttribute(
    "aria-busy",
    String(robotBusy || manualActionPending || powerTransitionPending),
  );
  document.querySelector(".robot-control-card").dataset.actionBusy = String(
    robotBusy || manualActionPending,
  );
  document.querySelectorAll(".manual-control").forEach((button) => {
    button.disabled = controlsBlocked;
  });
  $("emotion-select").disabled = controlsBlocked;
  $("robot-stop-button").disabled = powerTransitionPending;
  const readinessText = powerTransitionPending
    ? "Changing motor power — manual presets are paused"
    : robotBusy
      ? "Moving — press Stop action to cancel active and queued movement"
      : powerMode === "standby"
        ? "Standby — a movement command wakes Reachy first"
        : powerMode === "awake"
          ? "Awake — bounded remote movement is enabled"
          : `${powerMode} — manual movement unavailable`;
  $("robot-readiness").textContent = readinessText;
  const motorAnnouncement = `${motorStateText}. ${readinessText}`;
  if (motorAnnouncement !== lastMotorAnnouncement) {
    $("motor-state-live").textContent = motorAnnouncement;
    lastMotorAnnouncement = motorAnnouncement;
  }
  document.querySelectorAll("[data-power]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.power === powerMode));
    button.disabled = powerTransitionPending || manualActionPending;
  });
  const dot = $("status-dot");
  dot.className = "status-dot";
  if (["waiting_for_wake_word", "listening", "looking", "thinking", "speaking"].includes(state)) dot.classList.add("ready");
  if (["error", "configuration_error"].includes(state)) dot.classList.add("error");
  fillConfig(payload.config);
  currentConfig = payload.config || currentConfig;
  window.ReachyCamera?.setPolicy({
    enabled: Boolean(payload.config?.camera_feed_enabled),
    powerMode,
  });
  const announcementBusy = Boolean(runtime.announcement_busy);
  const announcementQueueDepth = Number(runtime.announcement_queue_depth || 0);
  const announcementBlocked = ["meeting", "sleep"].includes(powerMode);
  $("announcement-badge").textContent = announcementBusy ? "Speaking" : announcementQueueDepth ? "Queued" : "Ready";
  $("announcement-queue-badge").textContent = `${announcementQueueDepth} queued`;
  $("announcement-current").textContent = runtime.announcement_current_preview || "—";
  $("announcement-last").textContent = runtime.announcement_last_text || "—";
  $("announcement-error").textContent = runtime.announcement_last_error || "";
  const announcementLiveText = announcementBusy
    ? `Reachy is speaking. ${announcementQueueDepth} announcements remain queued.`
    : announcementQueueDepth
      ? `${announcementQueueDepth} announcements queued.`
      : "Announcement playback is ready.";
  if (announcementLiveText !== lastAnnouncementLiveText) {
    $("announcement-live").textContent = announcementLiveText;
    lastAnnouncementLiveText = announcementLiveText;
  }
  $("announcement-send").disabled = announcementBlocked || announcementRequestPending;
  $("announcement-stop").disabled = !announcementBusy && announcementQueueDepth === 0;
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

function refreshAnnouncementSpeechControls() {
  const providerSelect = $("announcement-provider");
  const selectedProvider = providerSelect.value;
  providerSelect.replaceChildren(new Option("Use app voice setting", ""));
  (voiceOptions.tts || []).forEach((item) => providerSelect.add(new Option(item.label, item.id)));
  providerSelect.value = [...providerSelect.options].some((option) => option.value === selectedProvider)
    ? selectedProvider : "";
  const provider = voiceOptions.tts.find((item) => item.id === providerSelect.value) || {};
  const modelSelect = $("announcement-model");
  const voiceSelect = $("announcement-voice");
  const selectedModel = modelSelect.value;
  const selectedVoice = voiceSelect.value;
  modelSelect.replaceChildren(new Option("Use app model", ""));
  (provider.models || []).forEach((model) => modelSelect.add(new Option(model, model)));
  voiceSelect.replaceChildren(new Option("Use app voice", ""));
  (provider.voices || []).forEach((voice) => voiceSelect.add(new Option(`${voice.name} — ${voice.id}`, voice.id)));
  if ([...modelSelect.options].some((option) => option.value === selectedModel)) modelSelect.value = selectedModel;
  if ([...voiceSelect.options].some((option) => option.value === selectedVoice)) voiceSelect.value = selectedVoice;
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
    refreshAnnouncementSpeechControls();
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
  if (statusRefreshPending) return;
  statusRefreshPending = true;
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    updateStatus(await response.json());
  } catch (error) {
    if (window.ReachyCamera?.isActive()) {
      window.ReachyCamera.stop("Camera stopped because Hermes status is unavailable.");
    }
    $("runtime-state").textContent = "Disconnected";
    $("runtime-detail").textContent = String(error);
    $("status-dot").className = "status-dot error";
    $("robot-mode-badge").textContent = "offline";
    $("motor-state").textContent = "Motor state unavailable";
    $("motor-state-dot").className = "motor-state-dot error";
    $("robot-readiness").textContent = "Remote controls disabled until live status returns";
    const disconnectedAnnouncement = "Motor state unavailable. Remote controls disabled until live status returns.";
    if (disconnectedAnnouncement !== lastMotorAnnouncement) {
      $("motor-state-live").textContent = disconnectedAnnouncement;
      lastMotorAnnouncement = disconnectedAnnouncement;
    }
    document.querySelectorAll(".manual-control, [data-power]").forEach((button) => { button.disabled = true; });
    $("emotion-select").disabled = true;
    $("announcement-send").disabled = true;
    $("announcement-stop").disabled = true;
    $("announcement-badge").textContent = "Offline";
  } finally {
    statusRefreshPending = false;
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

$("announcement-provider").addEventListener("change", refreshAnnouncementSpeechControls);
announcementText.addEventListener("input", () => {
  $("announcement-count").textContent = `${announcementText.value.length.toLocaleString()} / 15,000`;
  window.sessionStorage.setItem("reachy-hermes-announcement-draft", announcementText.value);
});
announcementText.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    $("announcement-send").click();
  }
});
document.querySelectorAll(".announcement-template").forEach((button) => {
  button.addEventListener("click", () => {
    announcementText.value = button.dataset.announcementTemplate || "";
    announcementText.dispatchEvent(new Event("input"));
    announcementText.focus();
  });
});

$("announcement-send").addEventListener("click", async () => {
  const text = announcementText.value.trim();
  const message = $("announcement-message");
  if (!text) {
    message.textContent = "Enter announcement text first.";
    message.className = "message error";
    announcementText.focus();
    return;
  }
  announcementRequestPending = true;
  $("announcement-send").disabled = true;
  message.textContent = "Adding announcement to Reachy's playback queue…";
  message.className = "message";
  try {
    const response = await fetch("/api/announcements", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        provider: $("announcement-provider").value,
        model: $("announcement-model").value,
        voice: $("announcement-voice").value,
        behavior: $("announcement-behavior").value,
        repeat: Number($("announcement-repeat").value),
        pause_seconds: Number($("announcement-pause").value),
      }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    message.textContent = body.queue_depth > 1
      ? `Queued behind ${body.queue_depth - 1} announcement${body.queue_depth === 2 ? "" : "s"}.`
      : "Announcement accepted. Reachy is preparing to speak.";
    message.className = "message ok";
    announcementText.value = "";
    announcementText.dispatchEvent(new Event("input"));
  } catch (error) {
    message.textContent = String(error);
    message.className = "message error";
  } finally {
    announcementRequestPending = false;
    await refreshStatus();
  }
});

$("announcement-stop").addEventListener("click", async () => {
  const button = $("announcement-stop");
  const message = $("announcement-message");
  button.disabled = true;
  try {
    const response = await fetch("/api/announcements/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear_queue: true }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    message.textContent = body.active_cancelled
      ? `Announcement stopped${body.queued_cleared ? ` and ${body.queued_cleared} queued cleared` : ""}.`
      : `${body.queued_cleared} queued announcement${body.queued_cleared === 1 ? "" : "s"} cleared.`;
    message.className = "message ok";
  } catch (error) {
    message.textContent = String(error);
    message.className = "message error";
  } finally {
    await refreshStatus();
  }
});

$("camera-test-button").addEventListener("click", async () => {
  const button = $("camera-test-button");
  const message = $("camera-message");
  button.disabled = true;
  message.textContent = "Capturing one local camera frame…";
  message.className = "message";
  try {
    const response = await fetch("/api/camera/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "camera" }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    message.textContent = `Camera ready: ${body.bytes} byte JPEG captured locally`;
    message.className = "message ok";
  } catch (error) {
    message.textContent = String(error);
    message.className = "message error";
  } finally {
    button.disabled = false;
  }
});

async function sendManualRobotAction(action, value) {
  const message = $("robot-message");
  manualActionPending = true;
  message.textContent = currentPowerMode === "standby"
    ? `Waking Reachy before ${action}: ${value}…`
    : `Starting ${action}: ${value}…`;
  message.className = "message";
  document.querySelectorAll(".manual-control, [data-power]").forEach((button) => { button.disabled = true; });
  $("emotion-select").disabled = true;
  try {
    const response = await fetch("/api/robot/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, value }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    message.textContent = `${action === "look" ? "Look" : action} ${value} started · Reachy is ${body.power_mode}`;
    message.className = "message ok";
  } catch (error) {
    message.textContent = String(error);
    message.className = "message error";
  } finally {
    manualActionPending = false;
    await refreshStatus();
  }
}

document.querySelectorAll("[data-robot-action]").forEach((button) => {
  button.addEventListener("click", () => {
    if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
    sendManualRobotAction(button.dataset.robotAction, button.dataset.robotValue);
  });
});

const dPad = $("look-d-pad");
dPad.addEventListener("keydown", (event) => {
  const keyDirections = {
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
    Home: "center",
  };
  const direction = keyDirections[event.key];
  if (!direction) return;
  event.preventDefault();
  dPad.querySelector(`[data-robot-value="${direction}"]`)?.click();
});

$("emotion-button").addEventListener("click", () => {
  sendManualRobotAction("emotion", $("emotion-select").value);
});

$("robot-stop-button").addEventListener("click", async () => {
  const button = $("robot-stop-button");
  const message = $("robot-message");
  button.disabled = true;
  message.textContent = "Stopping active and queued movement…";
  message.className = "message";
  try {
    const response = await fetch("/api/robot/stop", { method: "POST" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    message.textContent = !body.robot_stopped
      ? "Stop requested — Reachy is still settling"
      : body.active_cancelled
        ? "Movement stopped"
        : body.queued_cancelled
          ? `Cleared ${body.queued_cancelled} queued movement${body.queued_cancelled === 1 ? "" : "s"}`
          : "Robot is already stopped";
    message.className = "message ok";
  } catch (error) {
    message.textContent = String(error);
    message.className = "message error";
  } finally {
    button.disabled = false;
    await refreshStatus();
  }
});

async function setPowerMode(mode, durationMinutes = 60, message = $("power-message")) {
  if (powerTransitionPending) return;
  powerTransitionPending = true;
  document.querySelectorAll("[data-power], .manual-control").forEach((button) => { button.disabled = true; });
  $("emotion-select").disabled = true;
  $("robot-stop-button").disabled = true;
  if (mode !== "awake" && window.ReachyCamera?.isActive()) {
    window.ReachyCamera.stop(`Camera stopped before switching to ${mode}.`);
  }
  message.textContent = mode === "standby"
    ? "Folding Reachy before disabling motor torque…"
    : mode === "awake"
      ? "Enabling motor torque and waking Reachy…"
      : `Switching to ${mode}…`;
  message.className = "message";
  try {
    const response = await fetch("/api/power", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, duration_minutes: durationMinutes }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    const runtime = body.runtime || {};
    if (mode === "awake" && (runtime.power_mode !== "awake" || runtime.motors_enabled !== true)) {
      throw new Error("Awake was not confirmed by the robot runtime");
    }
    if (mode === "standby" && (runtime.motors_enabled !== false || runtime.head_safely_folded !== true)) {
      throw new Error("Safe folded Standby was not confirmed by the robot runtime");
    }
    message.textContent = mode === "standby"
      ? "Reachy folded safely · motor torque disabled"
      : mode === "awake"
        ? "Reachy awake · motor torque enabled"
        : `Power mode: ${body.runtime.power_mode}`;
    message.className = "message ok";
  } catch (error) {
    message.textContent = String(error);
    message.className = "message error";
  } finally {
    powerTransitionPending = false;
    await refreshStatus();
  }
}

document.querySelectorAll("[data-power]").forEach((button) => {
  button.addEventListener("click", () => {
    const panel = button.closest("[data-panel]");
    const message = panel.closest("#panel-robot") ? $("robot-message") : $("power-message");
    setPowerMode(button.dataset.power, Number(button.dataset.minutes || 60), message);
  });
});

$("app-off-button").addEventListener("click", async () => {
  if (!window.confirm("Stop the voice app? Restart it later from Reachy Control.")) return;
  if (window.ReachyCamera?.isActive()) {
    window.ReachyCamera.stop("Camera stopped before stopping the voice app.");
  }
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

async function loadRobotOptions() {
  try {
    const response = await fetch("/api/robot/options", { cache: "no-store" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    const emotionSelect = $("emotion-select");
    const selected = emotionSelect.value || "happy";
    const emotionLabels = {
      happy: "Happy · animated",
      excited: "Excited · animated",
      loving: "Loving · gentle",
      grateful: "Grateful · gentle",
      thinking: "Thinking · subtle",
      confused: "Confused · expressive",
      sad: "Sad · gentle",
      surprised: "Surprised · animated",
      calm: "Calm · gentle",
      welcoming: "Welcoming · expressive",
      yes: "Yes · head nod",
      no: "No · head shake",
    };
    emotionSelect.replaceChildren();
    (body.emotion || []).forEach((emotion) => {
      const option = document.createElement("option");
      option.value = emotion;
      option.textContent = emotionLabels[emotion] || emotion.charAt(0).toUpperCase() + emotion.slice(1);
      emotionSelect.appendChild(option);
    });
    if ([...emotionSelect.options].some((option) => option.value === selected)) emotionSelect.value = selected;
    const allowedLook = new Set(body.look || []);
    document.querySelectorAll('[data-robot-action="look"]').forEach((button) => {
      button.hidden = !allowedLook.has(button.dataset.robotValue);
    });
    const allowedDances = new Set(body.dance || []);
    document.querySelectorAll('[data-robot-action="dance"]').forEach((button) => {
      button.hidden = !allowedDances.has(button.dataset.robotValue);
    });
  } catch (error) {
    $("robot-message").textContent = `Could not load robot controls: ${String(error)}`;
    $("robot-message").className = "message error";
  }
}

async function startUi() {
  await refreshStatus();
  await Promise.all([loadModels(), loadVoiceOptions(), loadRobotOptions()]);
}

startUi();
setInterval(refreshStatus, 1500);
