import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

const LABEL = { ready: "Ready", live: "In progress", ended: "Completed" };

/* What is left to do, from the candidate's side.
 *
 * The two rounds are taken separately — possibly days apart — so "Completed"
 * against an interview with a coding exercise outstanding would send someone
 * away thinking they were finished. The stage says which round is next, and
 * the button goes there. */
const STAGE = {
  invited:    { text: "Not started", next: "Start the conversation" },
  voice_done: { text: "Conversation done", next: "Take the coding round" },
  coding_done:{ text: "Coding done", next: "Take the conversation" },
  complete:   { text: "Both rounds complete", next: null },
};

/* Entering the code an employer sent. Separate from signing in on purpose: the
 * account is theirs and lasts, the code is for one interview. */
function ClaimCode({ onClaimed }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.claim(code.trim().toUpperCase());
      setCode("");
      onClaimed();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card claim" onSubmit={submit}>
      <div className="field" style={{ flex: 1 }}>
        <label htmlFor="code">Have an interview code?</label>
        <input
          id="code" value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="ABCD2345" maxLength={8} autoComplete="off"
          style={{ fontFamily: "var(--mono)", letterSpacing: ".12em" }}
        />
        <p className="hint">
          Eight characters, from the employer who invited you. It unlocks an
          interview already written for your CV and their advert.
        </p>
      </div>
      <button className="btn-primary" disabled={busy || code.trim().length < 4}>
        {busy ? <span className="spinner" /> : "Open it"}
      </button>
      {error && <div className="notice error" style={{ flexBasis: "100%" }}>{error}</div>}
    </form>
  );
}

/* What a candidate sees: their own interviews, and no scores.
 *
 * The server does not send a score on this route at all — the redaction is not
 * a matter of what this page chooses to render. Marks are for the person
 * making the hiring decision.
 */
export default function CandidateHome() {
  const { user, justCreated } = useAuth();
  const navigate = useNavigate();
  const [interviews, setInterviews] = useState(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  const load = useCallback(() => {
    api.interviews()
      .then((r) => setInterviews(r.interviews))
      .catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  /* Where a row goes depends on which round is outstanding. An interview
   * curated by an employer already has its questions written, so it skips
   * Prepare entirely — sending them there would ask for the CV the panel was
   * built from. */
  const open = (it) => {
    if (it.stage === "voice_done" && it.coding) {
      navigate(`/coding/${encodeURIComponent(it.session_id)}`);
    } else if (it.stage === "invited" || it.stage === "coding_done") {
      navigate(`/interview/${encodeURIComponent(it.session_id)}`);
    } else {
      navigate(`/prepare/${encodeURIComponent(it.session_id)}`);
    }
  };

  const start = async () => {
    setStarting(true);
    setError("");
    try {
      const { session_id } = await api.post("/interviews", {});
      // Straight to Prepare. Adding the resume and job description is what
      // makes the questions about this role rather than a generic quiz, and a
      // candidate who is not sent there will never find it.
      navigate(`/prepare/${encodeURIComponent(session_id)}`);
    } catch (e) {
      setError(e.message);
      setStarting(false);
    }
  };

  return (
    <main className="page">
      <div className="stack">
        <div className="head">
          <div>
            <h1 style={{ fontSize: 34 }}>Your interviews</h1>
            <p className="sub" style={{ marginTop: 6 }}>
              Signed in as {user.full_name || user.username}
            </p>
          </div>
          {interviews?.length > 0 && (
            <button
              className="btn-primary"
              style={{ marginLeft: "auto" }}
              onClick={start}
              disabled={starting}
            >
              {starting ? <span className="spinner" /> : "New interview"}
            </button>
          )}
        </div>

        {justCreated && (
          <div className="notice info">
            <b>Welcome — we created your account as “{user.username}”.</b>{" "}
            Sign in with exactly that username next time. If you meant to use an
            existing account, sign out and try the spelling again.
          </div>
        )}

        <div className="disclosure">
          <span aria-hidden="true">◆</span>
          <div>
            <b>Every interviewer on the panel is an AI.</b>
            <p>
              You can interrupt any of them mid-sentence, exactly as you would a
              person. A human reviews the assessment afterwards.
            </p>
          </div>
        </div>

        {/* Discovery. The nav has a Profile link, but nobody reads a nav on a
          * page they came to for one thing — and a candidate with public work
          * to show is exactly who benefits from finding this. */}
        <div className="notice">
          <b>Add your GitHub before you interview.</b>{" "}
          The hiring team then reads your public work next to your answers,
          instead of taking the resume's word for it.{" "}
          <a href="/profile" onClick={(e) => { e.preventDefault(); navigate("/profile"); }}>
            Add it now
          </a>
        </div>

        {error && <div className="notice error">{error}</div>}

        {interviews === null && !error && (
          <div className="center" style={{ padding: 40 }}><span className="spinner" /></div>
        )}

        <ClaimCode onClaimed={load} />

        {interviews?.length === 0 && (
          <div className="empty">
            <h3>Nothing here yet</h3>
            <p className="small" style={{ maxWidth: "46ch", margin: "0 auto 20px" }}>
              Enter a code above if an employer sent you one. Otherwise start a
              practice interview — you will add your resume and the job
              description first, so the panel asks about the role you actually
              applied for.
            </p>
            <button className="btn-primary" onClick={start} disabled={starting}>
              {starting ? <span className="spinner" /> : "Start an interview"}
            </button>
          </div>
        )}

        {interviews?.length > 0 && (
          <div className="list">
            {interviews.map((it) => {
              const stage = STAGE[it.stage] || STAGE.invited;
              const done = it.stage === "complete" || it.status === "ended";
              return (
                <button
                  key={it.session_id}
                  className="item"
                  onClick={() => open(it)}
                  disabled={done}
                >
                  <div className="top">
                    <span className="title">{it.job_title || "Interview"}</span>
                    <span className="badges">
                      <span className={`badge ${done ? "ended" : it.status}`}>
                        {done ? "Completed" : stage.text}
                      </span>
                    </span>
                  </div>
                  <span className="meta">
                    {done
                      ? "Thank you — the result is with the hiring team."
                      : stage.next === "Take the coding round"
                        ? "Coding round outstanding. You can take it whenever suits you."
                        : `${stage.next}. You will need a microphone.`}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </main>
  );
}
