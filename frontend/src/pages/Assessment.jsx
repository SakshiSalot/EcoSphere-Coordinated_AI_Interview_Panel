import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";

const DECISIONS = [
  { key: "hire",    label: "Hire" },
  { key: "maybe",   label: "Maybe" },
  { key: "no_hire", label: "No hire" },
];

/* The assessment, and the decision.
 *
 * Every mark on this page is shown with the candidate's own words underneath
 * it. That is the point of the whole marking engine: a number a hiring manager
 * cannot see the reason for is not evidence, and the quotes were verified
 * against the transcript at scoring time, so nothing here can cite something
 * unsaid.
 */
export default function Assessment() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState("");

  useEffect(() => {
    api.assessment(sessionId)
      .then(setReport)
      .catch((e) => setError(e.message));
  }, [sessionId]);

  const decide = async (decision) => {
    setSaving(decision);
    try {
      await api.decide(sessionId, decision);
      setReport({ ...report, decision });
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving("");
    }
  };

  if (error) {
    return (
      <main className="page narrow">
        <div className="stack">
          <div className="notice error">{error}</div>
          <button className="btn-ghost" onClick={() => navigate("/")}>Back</button>
        </div>
      </main>
    );
  }

  if (!report) {
    return <main className="page center" style={{ paddingTop: 60 }}><span className="spinner" /></main>;
  }

  const a = report.assessment;
  const roles = Object.entries(a.by_role || {});
  const evidence = a.evidence || [];
  const flags = a.flags || [];

  return (
    <main className="page">
      <div className="stack">
        <div className="head">
          <div>
            <h1>{report.job_title || "Assessment"}</h1>
            <p className="sub">Interview {report.session_id}</p>
          </div>
          <button className="btn-ghost" style={{ marginLeft: "auto" }} onClick={() => navigate("/")}>
            Back
          </button>
        </div>

        {/* --- the marks --- */}
        <section className="card">
          <h2 style={{ marginBottom: 16 }}>Marks</h2>
          <div className="marks">
            {roles.map(([role, m]) => (
              <div key={role} className="mark-row">
                <span className="role">{role}</span>
                <span className="figure">
                  {m.earned.toFixed(1)} / {m.available.toFixed(1)} · {Math.round(m.fraction * 100)}%
                </span>
                <span className="bar">
                  <span style={{ width: `${Math.min(100, m.fraction * 100)}%` }} />
                </span>
              </div>
            ))}
          </div>

          <div className="total">
            <span className="big">{Math.round(a.fraction * 100)}%</span>
            <span className="muted small">
              {a.earned} of {a.total}, on questions worth {a.available}
              {a.penalties > 0 && ` · ${a.penalties} deducted for vague or contradictory answers`}
            </span>
          </div>
        </section>

        {/* --- what was flagged --- */}
        {flags.length > 0 && (
          <section className="card">
            <h2 style={{ marginBottom: 6 }}>Flags</h2>
            <p className="muted small" style={{ marginBottom: 14 }}>
              Raised either by the instant check during the conversation, or by
              the model judge afterwards. Both cost the same marks.
            </p>
            <div className="evidence">
              {flags.map((f, i) => (
                <div key={i}>
                  <span className="turnref">Turn {f.turn_id} · {f.kind.replace(/_/g, " ")} · {f.source}</span>
                  <div className="concept">{f.detail}</div>
                  {f.quote && <p className="quote">“{f.quote}”</p>}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* --- the evidence --- */}
        <section className="card">
          <h2 style={{ marginBottom: 6 }}>Evidence</h2>
          <p className="muted small" style={{ marginBottom: 14 }}>
            Every mark awarded, with the candidate’s own words that earned it.
            Each quote was verified against the transcript when it was scored.
          </p>
          {evidence.length === 0 ? (
            <p className="muted small">No concepts were covered.</p>
          ) : (
            <div className="evidence">
              {evidence.map((e, i) => (
                <div key={i}>
                  <span className="turnref">Turn {e.turn_id} · {e.role}</span>
                  <div className="concept">{e.concept}</div>
                  <p className="quote">“{e.quote}”</p>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* --- the human in the loop --- */}
        <section className="card">
          <h2 style={{ marginBottom: 6 }}>Your decision</h2>
          <p className="muted small" style={{ marginBottom: 16 }}>
            The panel assesses; a person decides. This is recorded against your
            name and the time you made it.
          </p>

          {report.decision && (
            <div className="notice info" style={{ marginBottom: 14 }}>
              Recorded as <b>{DECISIONS.find((d) => d.key === report.decision)?.label}</b>.
              You can change it.
            </div>
          )}

          <div className="row">
            {DECISIONS.map((d) => (
              <button
                key={d.key}
                className={report.decision === d.key ? "btn-primary" : "btn-ghost"}
                onClick={() => decide(d.key)}
                disabled={!!saving}
              >
                {saving === d.key ? <span className="spinner" /> : d.label}
              </button>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
