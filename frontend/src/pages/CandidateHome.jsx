import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

const LABEL = { ready: "Ready", live: "In progress", ended: "Completed" };

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

  useEffect(() => {
    api.interviews()
      .then((r) => setInterviews(r.interviews))
      .catch((e) => setError(e.message));
  }, []);

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

        {interviews?.length === 0 && (
          <div className="empty">
            <h3>Nothing here yet</h3>
            <p className="small" style={{ maxWidth: "46ch", margin: "0 auto 20px" }}>
              Start one whenever you are ready. You will add your resume and the
              job description first, so the panel asks about the role you
              actually applied for.
            </p>
            <button className="btn-primary" onClick={start} disabled={starting}>
              {starting ? <span className="spinner" /> : "Start an interview"}
            </button>
          </div>
        )}

        {interviews?.length > 0 && (
          <div className="list">
            {interviews.map((it) => (
              <button
                key={it.session_id}
                className="item"
                onClick={() => navigate(`/prepare/${encodeURIComponent(it.session_id)}`)}
                disabled={it.status === "ended"}
              >
                <div className="top">
                  <span className="title">{it.job_title || "Interview"}</span>
                  <span className="badges">
                    <span className={`badge ${it.status}`}>
                      {LABEL[it.status] || it.status}
                    </span>
                  </span>
                </div>
                <span className="meta">
                  {it.status === "ended"
                    ? "Completed — thank you. The result is with the hiring team."
                    : "Add your resume and the job description, then join. You will need a microphone."}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
