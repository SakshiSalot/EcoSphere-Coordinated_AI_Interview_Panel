import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { createMonitor } from "../integrity";

/* The live interview.
 *
 * ORDER MATTERS ON JOIN, and it is the whole reason /join exists separately
 * from /start: the browser enters the voice channel FIRST, and only then is
 * the panel brought in. Each agent speaks its AI disclosure the instant it
 * joins, so starting the panel first announces it to an empty room — and idle
 * agents bill by the minute.
 *
 * State comes from polling /state every two seconds. That is imperceptible in
 * a conversation whose turns take thirty, the endpoint already exists and
 * already redacts for a candidate, and a websocket would be a day of work for
 * nothing anyone could notice.
 */

const POLL_MS = 2000;

/* What the candidate is told about monitoring, in the words they would use.
 *
 * Shown to them live, and not because we have to. Someone who can see exactly
 * what is registering has no reason to imagine worse, and an indicator saying
 * "you are out of frame" lets them fix a tilted laptop lid instead of
 * unknowingly collecting six flags for it. */
const FACE_STATE = {
  ok:          { dot: "good", text: "You are in frame" },
  away:        { dot: "warn", text: "Looking away — this is normal while thinking" },
  none:        { dot: "warn", text: "Nobody in frame — check your camera angle" },
  many:        { dot: "warn", text: "More than one person in frame" },
  unavailable: { dot: "off",  text: "Face detection unavailable — focus is still monitored" },
  unknown:     { dot: "off",  text: "Starting up…" },
};

const CAMERA_STATE = {
  denied:      "Camera declined. The interview continues, and the report will say monitoring was off.",
  unavailable: "No camera available. The interview continues without it.",
  starting:    "Asking for camera permission…",
};

function MonitorPanel({ videoRef, status, live }) {
  const face = FACE_STATE[status?.face] || FACE_STATE.unknown;
  const cameraNote = CAMERA_STATE[status?.camera];

  return (
    <div className="card monitor">
      <div className="monitor-video">
        {/* muted and playsInline so it never competes with the interview
          * audio and never goes fullscreen on a phone. */}
        <video ref={videoRef} muted playsInline autoPlay />
        {status?.camera !== "on" && <span className="monitor-idle">Camera off</span>}
      </div>

      <div className="monitor-body">
        <div className="monitor-head">
          <span className={`dot ${live ? face.dot : "off"}`} />
          <b>{live ? face.text : "Monitoring starts when you join"}</b>
        </div>

        {cameraNote && <p className="small muted">{cameraNote}</p>}
        {status?.note && <p className="small muted">{status.note}</p>}

        <p className="small muted monitor-privacy">
          <b>No video is sent anywhere.</b> The camera image is read on this
          computer and never uploaded, recorded or stored. What reaches the
          hiring team is a list of moments — “looked away”, “left the tab” —
          with how long each lasted, next to the transcript. It carries no
          marks and cannot change your score.
        </p>
      </div>
    </div>
  );
}

export default function Interview() {
  const { sessionId } = useParams();
  const navigate = useNavigate();

  const [phase, setPhase] = useState("ready");   // ready | connecting | live | ended
  const [panel, setPanel] = useState([]);
  const [turns, setTurns] = useState([]);
  const [speaking, setSpeaking] = useState(null);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState("");

  // Monitoring. Opt-out rather than opt-in: the candidate is told plainly what
  // is watched and what leaves their machine (nothing but event names), and
  // declining is recorded as declined rather than silently treated as clean.
  const [watch, setWatch] = useState(true);
  const [integrity, setIntegrity] = useState(null);
  // uid -> "ok" | "blocked" | "subscribe failed". Rendered, so a voice that
  // never arrives is visible during the interview rather than deduced from
  // the logs afterwards.
  const [voices, setVoices] = useState({});

  // Refs, not state: these are not rendered, and putting an SDK client in
  // state re-runs effects on every reconnect.
  const client = useRef(null);
  const mic = useRef(null);
  const timer = useRef(null);
  const bottom = useRef(null);
  const camera = useRef(null);
  const monitor = useRef(null);

  const nameOf = useCallback(
    (role) => panel.find((p) => p.role === role)?.name || role,
    [panel]
  );

  const teardown = useCallback(async () => {
    clearInterval(timer.current);
    // Monitoring stops FIRST, and awaited: stopping flushes the last batch and
    // closes out anything still held — an interview that ends while the
    // candidate is out of frame should record that, not lose it.
    try { await monitor.current?.stop(); } catch { /* nothing running */ }
    monitor.current = null;
    try { mic.current?.close(); } catch { /* already closed */ }
    try { await client.current?.leave(); } catch { /* already gone */ }
    mic.current = null;
    client.current = null;

    /* The candidate's side is finished HERE, before the server is told.
     *
     * /stop does far more than remove agents: it saves the transcript and
     * totals the interview, and totalling waits for the background marking to
     * drain — up to three minutes when the judge is rate-limited. Awaiting it
     * left the page stuck in "live" for all that time: the End button stayed
     * on screen after the panel had said goodbye, and Mute did nothing because
     * the microphone had already been released. The interview was over and the
     * page was the last to know.
     *
     * So the UI closes immediately and the request runs on its own. Nothing
     * below depends on its result, and `keepalive` is not needed because the
     * page is not going anywhere. */
    setPhase("ended");

    /* ALWAYS called, including when the panel ended the interview itself.
     * That used to be skipped, on the reasoning that the agents were already
     * leaving — which missed that this is also what produces the assessment.
     * Interviews that ended most cleanly were the ones with no report. */
    api.stopPanel(sessionId).catch(() => { /* best effort */ });
  }, [sessionId]);

  const refresh = useCallback(async () => {
    try {
      const state = await api.state(sessionId);
      setSpeaking(state.floor_holder);
      setTurns(state.transcript || []);
      if (state.closed) await teardown();
    } catch (err) {
      setError(err.message);
      clearInterval(timer.current);
    }
  }, [sessionId, teardown]);

  const join = async () => {
    setError("");
    setPhase("connecting");

    // Declared out here so the catch can clean up whatever got as far as
    // existing. Assigning straight to the refs would leave a half-built
    // connection behind on failure, and the next Join would call join() on a
    // client that is already in a channel.
    let track = null;
    let rtc = null;

    try {
      // 1. The MICROPHONE FIRST, before anything is connected or spent.
      // Permission is the single likeliest thing to fail here, and failing
      // now means there is nothing to unwind — ask after joining and a denied
      // prompt leaves the browser sitting in a voice channel it cannot use.
      track = await window.AgoraRTC.createMicrophoneAudioTrack();

      // 2. Credentials. Starts nothing, costs no agent-minutes.
      const creds = await api.joinCredentials(sessionId);

      rtc = window.AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });

      /* SUBSCRIBING IS THE PART THAT SILENTLY FAILED.
       *
       * This used to be three lines with no error handling, inside an async
       * event handler — so any rejection was swallowed with nothing logged.
       * A live four-persona run had Priya audible every time and Arjun and
       * Meera silent, while the gateway showed it had streamed their replies
       * and Agora had accepted their voices. There was no way to tell whether
       * the failure was Agora's synthesis or this line, because this line
       * could not report anything.
       *
       * Now: every rejection is caught and named, `play()` gets one retry
       * (the track is occasionally not ready the instant it is subscribed),
       * and the count of connected voices is shown on screen — so the next
       * failure names itself instead of being deduced from logs. */
      rtc.on("user-published", async (user, kind) => {
        try {
          await rtc.subscribe(user, kind);
        } catch (err) {
          console.error(`[audio] could not subscribe to ${user.uid}:`, err);
          setVoices((v) => ({ ...v, [user.uid]: "subscribe failed" }));
          return;
        }
        if (kind !== "audio") return;

        const play = async () => { await user.audioTrack.play(); };
        try {
          await play();
          console.info(`[audio] playing uid ${user.uid}`);
          setVoices((v) => ({ ...v, [user.uid]: "ok" }));
        } catch (err) {
          // Most often the browser's autoplay policy, or a track that was not
          // ready yet. One retry after a beat clears the second case.
          console.warn(`[audio] first play failed for ${user.uid}:`, err);
          setTimeout(() => {
            play()
              .then(() => setVoices((v) => ({ ...v, [user.uid]: "ok" })))
              .catch((e) => {
                console.error(`[audio] uid ${user.uid} will not play:`, e);
                setVoices((v) => ({ ...v, [user.uid]: "blocked" }));
              });
          }, 400);
        }
      });

      rtc.on("user-unpublished", (user) => {
        console.info(`[audio] uid ${user.uid} stopped publishing`);
      });

      // 3. The candidate enters the room. uid null: the RTC token is minted
      // for uid 0, which Agora reads as "valid for any uid" — binding it to
      // one would reject this browser.
      // Join on the uid the server minted the token for. Passing null let
      // Agora pick one at random, which forced the agents to subscribe to
      // "*" — and they then heard each other rather than the candidate.
      await rtc.join(creds.app_id, creds.channel, creds.rtc_token, creds.uid ?? null);
      await rtc.publish([track]);
      client.current = rtc;
      mic.current = track;

      // 4. Only now bring the panel in, to a room with somebody in it.
      const started = await api.startPanel(sessionId, creds.channel);
      setPanel(started.panel || []);

      // 5. Monitoring, last and deliberately non-blocking. It is advisory, and
      // an interview must never fail to start because a camera or a CDN did.
      monitor.current = createMonitor({
        sessionId,
        stage: "voice",
        onStatus: setIntegrity,
      });
      monitor.current.start(watch ? camera.current : null).catch(() => {});

      setPhase("live");
      await refresh();
      timer.current = setInterval(refresh, POLL_MS);
    } catch (err) {
      // Unwind in reverse. Whatever succeeded before the failure has to be
      // released, or a retry inherits it.
      try { await rtc?.leave(); } catch { /* never joined */ }
      try { track?.close(); } catch { /* never opened */ }
      client.current = null;
      mic.current = null;

      setPhase("ready");
      setError(
        err.name === "NotAllowedError" || err.code === "PERMISSION_DENIED"
          ? "Your browser blocked microphone access. Click the icon to the left of the address bar, allow the microphone for this site, then reload and join again."
          : err.name === "NotFoundError"
            ? "No microphone was found. Plug one in or check your system sound settings, then join again."
            : err.message
      );
    }
  };

  const toggleMute = async () => {
    if (!mic.current) return;
    const next = !muted;
    await mic.current.setMuted(next);
    setMuted(next);
  };

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [turns.length]);

  // A closed tab is the likeliest way an agent gets left running, billing
  // until its idle timeout. keepalive lets the request outlive the page.
  useEffect(() => {
    const bail = () => {
      if (!client.current) return;
      // Whatever monitoring has queued goes with it — a closed tab is exactly
      // the moment the last few events matter, and losing them would make
      // "closed the tab and vanished" indistinguishable from a clean finish.
      try { monitor.current?.flushNow(); } catch { /* nothing running */ }
      const held = sessionStorage.getItem("echosphere.token");
      fetch(`/session/${encodeURIComponent(sessionId)}/stop`, {
        method: "POST",
        headers: { Authorization: `Bearer ${held}` },
        keepalive: true,
      }).catch(() => {});
    };
    window.addEventListener("pagehide", bail);
    return () => {
      window.removeEventListener("pagehide", bail);
      clearInterval(timer.current);
    };
  }, [sessionId]);

  return (
    <main className="page">
      <div className="stack">
        <div className="head">
          <div>
            <h1>Your interview</h1>
            <p className="sub">
              {phase === "live"
                ? "Speak naturally. You can interrupt an interviewer at any time."
                : "Check your microphone, then join when you are ready."}
            </p>
          </div>
        </div>

        <div className="disclosure">
          <span aria-hidden="true">◆</span>
          <div>
            <b>Every interviewer on this panel is an AI.</b>
            <p>
              You are not speaking to a person. Each will say so out loud when
              they join. The conversation is transcribed and assessed.
            </p>
          </div>
        </div>

        {error && <div className="notice error">{error}</div>}

        {phase === "ready" && (
          <label className="consent">
            <input
              type="checkbox"
              checked={watch}
              onChange={(e) => setWatch(e.target.checked)}
            />
            <span>
              <b>Use my camera during the interview.</b>
              <span className="small muted">
                The image stays on this computer — it is never uploaded or
                recorded. Only the fact that you were in frame is reported, and
                it carries no marks. You can decline; the interview runs the
                same, and the report will simply say monitoring was off.
              </span>
            </span>
          </label>
        )}

        {(watch || phase === "live") && (
          <MonitorPanel
            videoRef={camera}
            status={integrity}
            live={phase === "live"}
          />
        )}

        {phase === "ended" && (
          <div className="notice info">
            <b>The interview has ended.</b> Thank you — the hiring team will be
            in touch.
          </div>
        )}

        <div className="card">
          {panel.length > 0 ? (
            <div className="panel-list" style={{ marginBottom: 18 }}>
              {panel.map((p) => {
                /* "Next to speak", not "Speaking". The server tells us who
                 * holds the FLOOR — which is who will ask the next question,
                 * not who is making sound right now. Labelling that "Speaking"
                 * had Meera shown as talking while the room was silent, and
                 * sent us looking for an audio bug that was really a wording
                 * bug. Whether her voice is actually connected is a different
                 * fact, and it now has its own indicator. */
                const audio = voices[p.uid];
                return (
                  <div key={p.role}
                       className={`who-row ${p.role === speaking ? "speaking" : ""}`}>
                    <span className="dot" />
                    <span>
                      <b>{p.name}</b> <span className="title">· {p.title}</span>
                    </span>
                    {phase === "live" && audio !== "ok" && (
                      <span className="voice-warn" title="Their audio has not arrived">
                        {audio ? "no audio" : "connecting…"}
                      </span>
                    )}
                    <span className="now">Next to speak</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="muted small" style={{ marginBottom: 18 }}>
              The panel joins once you are in the call.
            </p>
          )}

          <div className="transcript">
            {turns.length === 0 && (
              <p className="muted small">
                The conversation will appear here as you speak.
              </p>
            )}
            {turns.map((t) => (
              <div key={t.turn_id} className={`turn ${t.speaker === "candidate" ? "you" : ""}`}>
                <div className="speaker">
                  {t.speaker === "candidate" ? "You" : nameOf(t.speaker)}
                </div>
                <p>{t.text}</p>
              </div>
            ))}
            <div ref={bottom} />
          </div>
        </div>

        <div className="controls">
          {phase === "ready" && (
            <button className="btn-primary" onClick={join}>Join the interview</button>
          )}
          {phase === "connecting" && (
            <button className="btn-primary" disabled><span className="spinner" /></button>
          )}
          {phase === "live" && (
            <>
              <button className="btn-ghost" onClick={toggleMute}>
                {muted ? "Unmute" : "Mute"}
              </button>
              <button className="btn-danger" onClick={() => teardown()}>
                End interview
              </button>
            </>
          )}
          {phase === "ended" && (
            <button className="btn-ghost" onClick={() => navigate("/")}>Back to your interviews</button>
          )}

          <span className={`status ${phase === "live" ? "live" : ""}`}>
            {{ ready: "Not connected", connecting: "Connecting…", live: "Live", ended: "Ended" }[phase]}
          </span>
        </div>
      </div>
    </main>
  );
}
