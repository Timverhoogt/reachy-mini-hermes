(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const state = {
    api: null,
    session: null,
    stream: null,
    producersListener: null,
    connectionListener: null,
    producerId: null,
    enabled: false,
    powerMode: "unknown",
    requested: false,
    visionStartedAt: null,
    visionTimer: null,
  };

  function setUi(status, message, kind = "") {
    byId("camera-live-status").textContent = status;
    const messageElement = byId("camera-message");
    messageElement.textContent = message;
    messageElement.className = `message ${kind}`;
  }

  function updateVisionTimecode() {
    const element = byId("camera-vision-timecode");
    if (!element || state.visionStartedAt === null) return;
    const elapsedSeconds = Math.max(0, Math.floor((performance.now() - state.visionStartedAt) / 1000));
    const hours = String(Math.floor(elapsedSeconds / 3600)).padStart(2, "0");
    const minutes = String(Math.floor((elapsedSeconds % 3600) / 60)).padStart(2, "0");
    const seconds = String(elapsedSeconds % 60).padStart(2, "0");
    element.textContent = `${hours}:${minutes}:${seconds}`;
  }

  function stopVisionOverlay() {
    if (state.visionTimer !== null) window.clearInterval(state.visionTimer);
    state.visionTimer = null;
    state.visionStartedAt = null;
    const element = byId("camera-vision-timecode");
    if (element) element.textContent = "00:00:00";
  }

  function startVisionOverlay() {
    stopVisionOverlay();
    state.visionStartedAt = performance.now();
    updateVisionTimecode();
    state.visionTimer = window.setInterval(updateVisionTimecode, 250);
  }

  function updateButtons() {
    const active = Boolean(state.session || state.stream);
    const allowed = state.enabled && state.powerMode === "awake";
    byId("camera-live-start").disabled = active || !allowed;
    byId("camera-live-stop").disabled = !active;
    byId("camera-live-fullscreen").disabled = !state.stream;
  }

  function detachStream() {
    const video = byId("reachy-camera-video");
    stopVisionOverlay();
    byId("camera-viewer").classList.remove("live");
    video.pause();
    video.srcObject = null;
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
  }

  function cleanupConnection() {
    detachStream();
    if (state.session) {
      try {
        state.session.close();
      } catch {
        // The signaling session may already be closed.
      }
      state.session = null;
    }
    if (state.api) {
      try {
        if (state.producersListener) state.api.unregisterProducersListener(state.producersListener);
        if (state.connectionListener) state.api.unregisterConnectionListener(state.connectionListener);
        // GstWebRTCAPI does not expose a public close method. This pinned bundle's
        // channel owns the signaling WebSocket; closing it prevents one idle socket
        // from leaking on every Start/Stop cycle.
        if (typeof state.api._channel?.close === "function") state.api._channel.close();
      } catch {
        // The signaling channel may already be closed.
      }
    }
    state.api = null;
    state.producersListener = null;
    state.connectionListener = null;
    state.producerId = null;
    updateButtons();
  }

  function stop(message = "Live camera stopped.") {
    const wasActive = state.requested || state.session || state.stream;
    state.requested = false;
    cleanupConnection();
    if (wasActive) setUi("Off", message);
  }

  function policyMessage() {
    if (!state.enabled) return "Enable Local live camera in Settings first.";
    if (state.powerMode === "meeting" || state.powerMode === "sleep") {
      return `Camera viewing is blocked in ${state.powerMode}.`;
    }
    if (state.powerMode !== "awake") return "Wake Reachy before starting the live camera.";
    return "Press Start live camera to connect directly to Reachy's local WebRTC feed.";
  }

  function setPolicy({ enabled, powerMode }) {
    state.enabled = Boolean(enabled);
    state.powerMode = powerMode || "unknown";
    if (!state.enabled || state.powerMode !== "awake") {
      if (state.requested || state.session || state.stream) stop(policyMessage());
      else setUi("Off", policyMessage());
    } else if (!state.requested && !state.session && !state.stream) {
      setUi("Ready", policyMessage());
    }
    updateButtons();
  }

  function isReachyCameraProducer(producer) {
    const name = String(producer?.meta?.name || "").trim().toLowerCase();
    return name === "reachymini" || name === "reachy-mini-camera";
  }

  function attachSession(api, producer) {
    if (!state.requested || state.session || !isReachyCameraProducer(producer)) return;
    const session = api.createConsumerSession(producer.id);
    if (!session) return;
    state.session = session;
    state.producerId = producer.id;

    session.addEventListener("error", (event) => {
      const message = event?.message || "WebRTC camera stream failed";
      state.requested = false;
      cleanupConnection();
      setUi("Error", message, "error");
    });
    session.addEventListener("closed", () => {
      const requested = state.requested;
      state.session = null;
      detachStream();
      updateButtons();
      if (requested) setUi("Offline", "Camera connection closed. Press Start to reconnect.", "error");
    });
    session.addEventListener("streamsChanged", () => {
      if (!state.requested || !session.streams?.length) return;
      const stream = session.streams[0];
      stream.getAudioTracks().forEach((track) => {
        track.enabled = false;
      });
      state.stream = stream;
      const video = byId("reachy-camera-video");
      byId("camera-viewer").classList.add("live");
      startVisionOverlay();
      video.srcObject = stream;
      video.play().then(() => {
        setUi("Live", "Local camera connected. No frames are sent to Hermes or OpenAI.", "ok");
        updateButtons();
      }).catch((error) => {
        setUi("Paused", `Camera connected, but playback was blocked: ${String(error)}`, "error");
      });
    });
    session.connect();
  }

  function start() {
    if (!state.enabled || state.powerMode !== "awake") {
      setUi("Blocked", policyMessage(), "error");
      updateButtons();
      return;
    }
    if (typeof window.RTCPeerConnection !== "function" || !window.GstWebRTCAPI) {
      setUi("Unsupported", "This browser does not provide the required WebRTC camera support.", "error");
      return;
    }

    cleanupConnection();
    state.requested = true;
    setUi("Connecting", "Connecting directly to Reachy's local camera…");
    updateButtons();

    try {
      const signalingScheme = window.location.protocol === "https:" ? "wss" : "ws";
      const api = new window.GstWebRTCAPI({
        signalingServerUrl: `${signalingScheme}://${window.location.hostname}:8443`,
        reconnectionTimeout: 0,
        meta: { name: "reachy-hermes-ui" },
        // The viewer reaches the robot directly over the LAN or private tailnet;
        // avoid adding a separate public STUN dependency in the browser.
        webrtcConfig: { iceServers: [] },
      });
      state.api = api;
      state.connectionListener = {
        connected: () => setUi("Connecting", "Camera signaling connected; waiting for video…"),
        disconnected: () => {
          if (!state.requested) return;
          state.requested = false;
          cleanupConnection();
          setUi("Offline", "Camera signaling disconnected. Press Start to reconnect.", "error");
        },
      };
      state.producersListener = {
        producerAdded: (producer) => {
          if (!isReachyCameraProducer(producer)) return;
          attachSession(api, producer);
        },
        producerRemoved: (producer) => {
          if (!state.requested || producer?.id !== state.producerId) return;
          state.requested = false;
          cleanupConnection();
          setUi("Offline", "Reachy's camera producer stopped.", "error");
        },
      };
      api.registerConnectionListener(state.connectionListener);
      api.registerProducersListener(state.producersListener);
    } catch (error) {
      state.requested = false;
      cleanupConnection();
      setUi("Error", `Could not connect to Reachy's camera: ${String(error)}`, "error");
    }
  }

  async function fullscreen() {
    const viewer = byId("camera-viewer");
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await viewer.requestFullscreen();
    } catch (error) {
      setUi("Live", `Fullscreen could not open: ${String(error)}`, "error");
    }
  }

  byId("camera-live-start").addEventListener("click", start);
  byId("camera-live-stop").addEventListener("click", () => stop());
  byId("camera-live-fullscreen").addEventListener("click", fullscreen);
  window.addEventListener("pagehide", () => stop("Camera stopped because the page closed."));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop("Camera stopped while the app was in the background.");
  });

  window.ReachyCamera = {
    setPolicy,
    stop,
    isActive: () => Boolean(state.requested || state.session || state.stream),
  };
  setUi("Off", "Reading camera policy…");
  updateButtons();
})();
