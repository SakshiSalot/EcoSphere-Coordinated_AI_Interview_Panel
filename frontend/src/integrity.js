/* Focus and camera monitoring, entirely inside the candidate's browser.
 *
 * THE FRAMES NEVER LEAVE THIS FILE. The camera stream goes to a <video>
 * element and into a face model running on this machine; what is sent to the
 * server is a list of typed events — "nobody in frame for 6 seconds". There is
 * no canvas readback to a blob, no upload, no MediaRecorder, and nothing here
 * could add one without it being obvious in review. A hiring product that
 * ships webcam footage of applicants to a server is a breach waiting to be
 * noticed; this one has nothing to breach.
 *
 * EVERY SIGNAL IS DEBOUNCED, and that is not a performance decision. Raw
 * per-frame detection produces a flag every time someone blinks, glances at
 * their keyboard, or is briefly backlit by a window — noise that would bury
 * the two or three moments an operator actually wants to see. So a condition
 * has to HOLD for a threshold before it becomes an event, and the event
 * carries how long it lasted, because duration is the part that distinguishes
 * a notification from reading an answer off a second screen.
 *
 * WHAT IT CANNOT DO. Anything running on a machine the candidate controls can
 * be turned off by the candidate. The heartbeat is the honest partial answer:
 * this reports that it is still watching every fifteen seconds, so a stretch
 * with no heartbeat is recorded as UNMONITORED rather than as clean. That
 * turns switching it off from an invisible success into a visible gap.
 */

const WASM_BASE = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/wasm";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

/* How long a condition must hold before it counts. Tuned against what people
 * actually do rather than what is easy to detect: looking away for a second is
 * thinking, and half the interview is spent doing it. */
const HOLD_MS = {
  no_face: 4000,
  multiple_faces: 2500,
  gaze_away: 3500,
  window_blur: 1500,
  // tab_hidden and fullscreen_exit are not in here because they are not gated:
  // a hidden tab is a discrete event with an unambiguous start and end, unlike
  // a glance, so it is recorded the moment it happens.
};

const SAMPLE_MS = 200;        // 5 detections a second is plenty and cheap
const FLUSH_MS = 8000;        // batch events rather than a request each
const HEARTBEAT_MS = 15000;   // must match monitor.HEARTBEAT_SECONDS

/* Gaze thresholds. Deliberately loose — this is the weakest signal we collect
 * and a tight threshold would make it the loudest, which is exactly backwards.
 * Head yaw does most of the work: reading from a second screen turns the head,
 * whereas thinking usually only moves the eyes. */
const YAW_DEGREES = 28;
const EYE_DEVIATION = 0.62;

/* Extract yaw and pitch from MediaPipe's 4x4 facial transformation matrix,
 * which arrives column-major. Only the rotation block matters. */
function headAngles(matrix) {
  if (!matrix?.data || matrix.data.length < 16) return { yaw: 0, pitch: 0 };
  const m = matrix.data;
  // Column-major: m[0..2] is the first column, m[4..6] the second, and so on.
  const r20 = m[2];
  const r21 = m[6];
  const r22 = m[10];
  const yaw = Math.atan2(-r20, Math.hypot(r21, r22)) * (180 / Math.PI);
  const pitch = Math.atan2(r21, r22) * (180 / Math.PI);
  return { yaw, pitch };
}

/* How far off centre the eyes are, from the blendshape scores. Taking the max
 * of the four directions rather than summing them: a person looking hard left
 * should read the same as one looking hard down, and a sum would make a
 * diagonal glance score twice as badly as either. */
function eyeDeviation(shapes) {
  if (!shapes?.categories) return 0;
  let worst = 0;
  for (const c of shapes.categories) {
    if (
      c.categoryName === "eyeLookOutLeft" ||
      c.categoryName === "eyeLookOutRight" ||
      c.categoryName === "eyeLookInLeft" ||
      c.categoryName === "eyeLookInRight" ||
      c.categoryName === "eyeLookDownLeft" ||
      c.categoryName === "eyeLookDownRight"
    ) {
      worst = Math.max(worst, c.score);
    }
  }
  return worst;
}

export function createMonitor({ sessionId, stage = "voice", onStatus }) {
  const queue = [];
  const held = {};           // condition -> when it started holding
  let stream = null;
  let video = null;
  let landmarker = null;
  let sampleTimer = null;
  let flushTimer = null;
  let beatTimer = null;
  let running = false;
  let lastVideoTime = -1;
  let lastBeat = 0;

  const status = {
    camera: "off",           // off | starting | on | denied | unavailable
    face: "unknown",         // unknown | ok | none | many | unavailable
    monitoring: false,
    note: "",
  };

  const publish = () => onStatus?.({ ...status });

  const emit = (kind, seconds = 0, detail = "") => {
    queue.push({ kind, at: Date.now(), seconds, detail });
  };

  /* A condition that has to hold before it is worth reporting. Called every
   * sample with whether it is true right now; emits once, on release, with the
   * duration it lasted. */
  const gate = (kind, active) => {
    const now = performance.now();
    if (active) {
      if (held[kind] === undefined) held[kind] = now;
      return;
    }
    const since = held[kind];
    if (since === undefined) return;
    delete held[kind];
    const ms = now - since;
    if (ms >= (HOLD_MS[kind] ?? 2000)) emit(kind, ms / 1000);
  };

  /* Close out anything still held when monitoring stops, so an interview that
   * ends while the candidate is out of frame records that rather than losing
   * it. */
  const releaseAll = () => {
    const now = performance.now();
    for (const [kind, since] of Object.entries(held)) {
      const ms = now - since;
      if (ms >= (HOLD_MS[kind] ?? 2000)) emit(kind, ms / 1000);
      delete held[kind];
    }
  };

  const flush = async (useBeacon = false) => {
    if (!queue.length) return;
    const batch = queue.splice(0, queue.length);
    const body = JSON.stringify({ events: batch, stage });
    const url = `/session/${encodeURIComponent(sessionId)}/integrity`;
    const auth = sessionStorage.getItem("echosphere.token");

    try {
      await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
        },
        body,
        // Lets the final flush outlive the page when the tab is closing.
        keepalive: useBeacon,
      });
    } catch {
      // A failed flush must never interrupt an interview. Put the events back
      // so the next flush carries them; if the network is gone for good, the
      // heartbeat gap records that monitoring stopped, which is the truth.
      queue.unshift(...batch);
    }
  };

  const beat = () => {
    const now = Date.now();
    emit("heartbeat", lastBeat ? (now - lastBeat) / 1000 : 0);
    lastBeat = now;
  };

  // --- focus, which needs no camera and no permission ---------------------

  let hiddenAt = 0;
  let blurAt = 0;

  const onVisibility = () => {
    if (document.hidden) {
      hiddenAt = Date.now();
    } else if (hiddenAt) {
      emit("tab_hidden", (Date.now() - hiddenAt) / 1000);
      hiddenAt = 0;
    }
  };

  const onBlur = () => {
    // Hiding the tab ALSO fires blur. Without this guard every tab switch is
    // recorded twice, once under each kind, and the operator reads double the
    // events that happened.
    if (document.hidden) return;
    blurAt = Date.now();
  };

  const onFocus = () => {
    if (!blurAt) return;
    const seconds = (Date.now() - blurAt) / 1000;
    blurAt = 0;
    if (seconds * 1000 >= HOLD_MS.window_blur) emit("window_blur", seconds);
  };

  const onFullscreen = () => {
    if (!document.fullscreenElement) emit("fullscreen_exit", 0);
  };

  const onPaste = (e) => {
    const text = e.clipboardData?.getData("text") ?? "";
    // Length only. The pasted content is the candidate's answer, and copying
    // it to our server would be collecting their work through a side channel.
    emit("paste", 0, `${text.length} characters`);
  };

  // --- the camera half ----------------------------------------------------

  const sample = () => {
    if (!running || !landmarker || !video || video.readyState < 2) return;
    // detectForVideo demands strictly increasing timestamps; feeding it the
    // same frame twice throws and kills the loop.
    if (video.currentTime === lastVideoTime) return;
    lastVideoTime = video.currentTime;

    let result;
    try {
      result = landmarker.detectForVideo(video, performance.now());
    } catch {
      return; // one bad frame is not worth ending monitoring over
    }

    const faces = result.faceLandmarks?.length ?? 0;
    gate("no_face", faces === 0);
    gate("multiple_faces", faces > 1);

    let away = false;
    if (faces === 1) {
      const { yaw } = headAngles(result.facialTransformationMatrixes?.[0]);
      const eyes = eyeDeviation(result.faceBlendshapes?.[0]);
      away = Math.abs(yaw) > YAW_DEGREES || eyes > EYE_DEVIATION;
    }
    gate("gaze_away", away);

    const next = faces === 0 ? "none" : faces > 1 ? "many" : away ? "away" : "ok";
    if (next !== status.face) {
      status.face = next;
      publish();
    }
  };

  const startCamera = async (videoEl) => {
    video = videoEl;
    status.camera = "starting";
    publish();

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // Small on purpose: the model downsamples anyway, and a 640x480 stream
        // costs a fraction of the CPU of a 1080p one on a laptop that is also
        // running a voice call.
        video: { width: 640, height: 480, facingMode: "user" },
        audio: false,
      });
    } catch (err) {
      status.camera = err?.name === "NotAllowedError" ? "denied" : "unavailable";
      // Otherwise the indicator sits on "Starting up…" for the whole interview
      // while the camera line underneath says it was declined.
      status.face = "unavailable";
      status.note =
        err?.name === "NotAllowedError"
          ? "Camera access was declined. The interview continues; the report will show that monitoring was not running."
          : "No camera was available. The interview continues without it.";
      publish();
      return false;
    }

    video.srcObject = stream;
    await video.play().catch(() => {});
    status.camera = "on";
    publish();

    // The candidate can revoke access or unplug the camera mid-interview.
    stream.getVideoTracks().forEach((track) => {
      track.addEventListener("ended", () => {
        if (!running) return;
        emit("camera_off", 0, "the camera track ended");
        status.camera = "unavailable";
        status.face = "unavailable";
        publish();
      });
    });

    // The face model is loaded AFTER the camera is live, so the preview
    // appears immediately and a slow CDN delays only the analysis. If it never
    // arrives, focus monitoring carries on alone.
    try {
      const { FaceLandmarker, FilesetResolver } = await import(
        "@mediapipe/tasks-vision"
      );
      const fileset = await FilesetResolver.forVisionTasks(WASM_BASE);
      landmarker = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" },
        runningMode: "VIDEO",
        // Three, so "more than one person" is detectable at all. numFaces: 1
        // would silently report the nearest face and never flag a second.
        numFaces: 3,
        outputFaceBlendshapes: true,
        outputFacialTransformationMatrixes: true,
      });
      status.face = "ok";
      publish();
    } catch (err) {
      // Never fatal. A blocked CDN or a machine without WebGL should cost the
      // face signals and nothing else.
      landmarker = null;
      status.face = "unavailable";
      status.note =
        "The face model could not load, so only tab and window focus are being watched.";
      publish();
      return true;
    }

    sampleTimer = setInterval(sample, SAMPLE_MS);
    return true;
  };

  return {
    status: () => ({ ...status }),

    /* Focus monitoring starts immediately and needs no permission. The camera
     * is separate and may be declined — declining costs the face signals and
     * nothing else, which is why they are two calls. */
    async start(videoEl) {
      if (running) return;
      running = true;
      status.monitoring = true;
      publish();

      document.addEventListener("visibilitychange", onVisibility);
      window.addEventListener("blur", onBlur);
      window.addEventListener("focus", onFocus);
      document.addEventListener("fullscreenchange", onFullscreen);
      document.addEventListener("paste", onPaste);

      beat();
      beatTimer = setInterval(beat, HEARTBEAT_MS);
      flushTimer = setInterval(() => flush(false), FLUSH_MS);

      if (videoEl) await startCamera(videoEl);
    },

    async stop() {
      if (!running) return;
      running = false;

      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("fullscreenchange", onFullscreen);
      document.removeEventListener("paste", onPaste);

      clearInterval(sampleTimer);
      clearInterval(flushTimer);
      clearInterval(beatTimer);

      releaseAll();
      beat();

      try { landmarker?.close(); } catch { /* already closed */ }
      landmarker = null;
      stream?.getTracks().forEach((t) => t.stop());
      stream = null;
      if (video) video.srcObject = null;

      status.camera = "off";
      status.face = "unknown";
      status.monitoring = false;
      publish();

      await flush(true);
    },

    /* For a closing tab: synchronous enough to survive pagehide. */
    flushNow() {
      releaseAll();
      flush(true);
    },
  };
}
