import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

const LABEL = { ready: "Ready", live: "In progress", ended: "Assessed" };
const DECISION = { hire: "Hire", no_hire: "No hire", maybe: "Maybe" };

/* The human in the loop.
 *
 * Every interview this operator set up, with its score and whether a decision
 * has been recorded. The point of the product is that a person makes that
 * call, so the outstanding ones are what this screen is for.
 */
export default function OperatorHome() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [interviews, setInterviews] = useState(null);
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState("");

  // Deleting is destructive and the rows all look alike, so confirm against
  // the specific one rather than trusting a click.
  const remove = async (it, event) => {
    event.stopPropagation();
    const who = it.candidate || it.session_id;
    if (!window.confirm(`Delete the interview with ${who}? This cannot be undone.`)) return;
    setRemoving(it.session_id);
    try {
      await api.removeInterview(it.session_id);
      setInterviews((rows) => rows.filter((r) => r.session_id !== it.session_id));
    } catch (e) {
      setError(e.message);
    } finally {
      setRemoving("");
    }
  };

  useEffect(() => {
    api.interviews()
      .then((r) => setInterviews(r.interviews))
      .catch((e) => setError(e.message));
    // A failure here must not take the interview list down with it — the two
    // are independent, and an operator with no openings still has interviews.
    api.jobs().then((r) => setJobs(r.jobs)).catch(() => setJobs([]));
  }, []);

  const awaiting = interviews?.filter((i) => i.status === "ended" && !i.decision) ?? [];

  // Interviews curated under an opening are shown on that opening's
  // leaderboard, where they can be compared. Listing them here as well would
  // put the same candidate on two screens with two different meanings.
  const loose = interviews?.filter((i) => !i.job_id) ?? [];

  return (
    <main className="page">
      <div className="stack">
        <div className="head">
          <div>
            <h1>Hiring</h1>
            <p className="sub">Signed in as {user.full_name || user.username}</p>
          </div>
          <button className="btn-primary" style={{ marginLeft: "auto" }}
                  onClick={() => navigate("/jobs/new")}>
            New opening
          </button>
        </div>

        {error && <div className="notice error">{error}</div>}

        {/* --- openings --- */}
        {jobs === null ? null : jobs.length === 0 ? (
          <div className="empty">
            <h3>No openings yet</h3>
            <p className="small" style={{ maxWidth: "50ch", margin: "0 auto 20px" }}>
              An opening holds one advert and however many applicants. Every
              interview under it is built from the same advert, which is what
              makes the candidates comparable to each other.
            </p>
            <button className="btn-primary" onClick={() => navigate("/jobs/new")}>
              Create an opening
            </button>
          </div>
        ) : (
          <div className="list">
            {jobs.map((j) => (
              <button
                key={j.job_id}
                className="item"
                onClick={() => navigate(`/jobs/${encodeURIComponent(j.job_id)}`)}
              >
                <div className="top">
                  <span className="title">{j.title}</span>
                  <span className="badges">
                    {j.coding_enabled ? (
                      <span className="badge ready">Talk + code</span>
                    ) : (
                      <span className="badge ready">Conversation only</span>
                    )}
                  </span>
                </div>
                <span className="meta">
                  {j.candidates} candidate{j.candidates === 1 ? "" : "s"} ·{" "}
                  {j.completed} complete
                  {j.candidates === 0 && " — add one to get an invite code"}
                </span>
              </button>
            ))}
          </div>
        )}

        {awaiting.length > 0 && (
          <div className="notice info">
            <b>{awaiting.length} assessment{awaiting.length > 1 ? "s" : ""} awaiting your decision.</b>{" "}
            The panel scores and cites the evidence; the hiring call is yours.
          </div>
        )}

        {interviews === null && !error && (
          <div className="center" style={{ padding: 40 }}><span className="spinner" /></div>
        )}

        {loose.length > 0 && (
          <div className="head" style={{ marginTop: 10 }}>
            <div>
              <h2>Interviews outside an opening</h2>
              <p className="sub" style={{ marginTop: 4 }}>
                Started by a candidate directly, or set up from the terminal.
                These are not ranked — there is no shared advert to rank them
                against.
              </p>
            </div>
          </div>
        )}

        {loose.length > 0 && (
          <div className="list">
            {loose.map((it) => {
              const scored = typeof it.score === "number";
              return (
                <div
                  key={it.session_id}
                  className="item"
                  role="button"
                  tabIndex={0}
                  onClick={() => navigate(`/assessment/${encodeURIComponent(it.session_id)}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      navigate(`/assessment/${encodeURIComponent(it.session_id)}`);
                    }
                  }}
                >
                  <div className="top">
                    <span className="title">{it.candidate}</span>
                    <span className="badges">
                      <span className={`badge ${it.status}`}>{LABEL[it.status] || it.status}</span>
                      {it.decision && (
                        <span className={`badge ${it.decision}`}>{DECISION[it.decision]}</span>
                      )}
                      {/* A decision is the record a hire rests on; the server
                          refuses to delete one, so do not offer it here. */}
                      {!it.decision && (
                        <button
                          className="btn-danger-quiet"
                          title="Delete this interview"
                          onClick={(e) => remove(it, e)}
                          disabled={removing === it.session_id}
                        >
                          {removing === it.session_id ? "…" : "Delete"}
                        </button>
                      )}
                    </span>
                  </div>
                  <div className="spread">
                    <span className="meta">{it.job_title || "—"}</span>
                    {scored && (
                      <span className="grow score">
                        {Math.round(it.score * 100)}<small> / 100</small>
                      </span>
                    )}
                  </div>
                  {it.status !== "ended" && (
                    <span className="meta small">
                      Not finished — opens the transcript so far.
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </main>
  );
}
