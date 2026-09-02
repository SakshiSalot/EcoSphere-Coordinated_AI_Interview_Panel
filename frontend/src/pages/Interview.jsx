import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";

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

export default function Interview() {
  const { sessionId } = useParams();
  const navigate = useNavigate();

  const [phase, setPhase] = useState("ready");   // ready | connecting | live | ended
  const [panel, setPanel] = useState([]);
  const [turns, setTurns] = useState([]);
  const [speaking, setSpeaking] = useState(null);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState("");

  // Refs, not state: these are not rendered, and putting an SDK client in
  // state re-runs effects on every reconnect.
  const client = useRef(null);
  const mic = useRef(null);
  const timer = useRef(null);
  const bottom = useRef(null);

  const nameOf = useCallback(
    (role) => panel.find((p) => p.role === role)?.name || role,
    [panel]
  );

  const teardown = useCallback(async (endedByPanel) => {
    clearInterval(timer.current);
    try { mic.current?.close(); } catch { /* already closed */ }
    try { await client.current?.leave(); } catch { /* already gone */ }
    mic.current = null;
    client.current = null;
    // Only tear the panel down if WE ended it. If the panel closed the
    // interview itself its agents are already leaving.
    if (!endedByPanel) { try { await api.stopPanel(sessionId); } catch { /* best effort */ } }
    setPhase("ended");
  }, [sessionId]);

  const refresh = useCallback(async () => {
    try {
      const state = await api.state(sessionId);
      setSpeaking(state.floor_holder);
      setTurns(state.transcript || []);
      if (state.closed) await teardown(true);
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
      rtc.on("user-published", async (user, kind) => {
        await rtc.subscribe(user, kind);
        if (kind === "audio") user.audioTrack.play();
      });

      // 3. The candidate enters the room. uid null: the RTC token is minted
      // for uid 0, which Agora reads as "valid for any uid" — binding it to
      // one would reject this browser.
      await rtc.join(creds.app_id, creds.channel, creds.rtc_token, null);
      await rtc.publish([track]);
      client.current = rtc;
      mic.current = track;

      // 4. Only now bring the panel in, to a room with somebody in it.
      const started = await api.startPanel(sessionId, creds.channel);
      setPanel(started.panel || []);

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

        {phase === "ended" && (
          <div className="notice info">
            <b>The interview has ended.</b> Thank you — the hiring team will be
            in touch.
          </div>
        )}

        <div className="card">
          {panel.length > 0 ? (
            <div className="panel-list" style={{ marginBottom: 18 }}>
              {panel.map((p) => (
                <div key={p.role} className={`who-row ${p.role === speaking ? "speaking" : ""}`}>
                  <span className="dot" />
                  <span>
                    <b>{p.name}</b> <span className="title">· {p.title}</span>
                  </span>
                  <span className="now">Speaking</span>
                </div>
              ))}
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
              <button className="btn-danger" onClick={() => teardown(false)}>
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
