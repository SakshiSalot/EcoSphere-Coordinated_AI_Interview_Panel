import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";

const DECISIONS = [
  { key: "hire",    label: "Hire" },
  { key: "maybe",   label: "Maybe" },
  { key: "no_hire", label: "No hire" },
];

const CONCERN = {
  none:    { label: "Nothing flagged",   tone: "ok" },
  minor:   { label: "Minor signals",     tone: "warn" },
  review:  { label: "Worth a read",      tone: "warn" },
  unknown: { label: "Not monitored",     tone: "off" },
};

const clock = (seconds) =>
  seconds >= 60 ? `${Math.round(seconds / 60)} min` : `${Math.round(seconds)}s`;

/* Focus and camera signals, and every caveat that goes with them.
 *
 * The caveat sits NEXT TO the count rather than in a footnote, because a bare
 * number on a hiring screen is read as an accusation. "Looked away ×6" with
 * nothing beside it convicts someone of thinking.
 */
function IntegrityPanel({ integrity }) {
  if (!integrity) return null;
  const tone = CONCERN[integrity.concern] || CONCERN.unknown;
  const coverage = integrity.coverage || {};

  return (
    <section className="card">
      <h2 style={{ marginBottom: 6 }}>
        Integrity <span className={`vbadge ${tone.tone}`}>{tone.label}</span>
      </h2>
      <p className="muted small" style={{ marginBottom: 14 }}>{integrity.headline}</p>

      {!integrity.monitored ? (
        <div className="notice">{integrity.note}</div>
      ) : (
        <>
          {!coverage.complete && (
            <div className="notice" style={{ marginBottom: 14 }}>
              <b>Monitoring was not running for the whole interview</b> —
              about {clock(coverage.gap_seconds || 0)} of it went unwatched.
              That is missing data, not a clean record.
            </div>
          )}

          <div className="signals">
            {integrity.signals.map((s) => (
              <div key={s.kind} className="signal">
                <div className="top">
                  <b>{s.label}</b>
                  <span className="count">
                    ×{s.count}{s.seconds >= 1 && ` · ${clock(s.seconds)} total`}
                  </span>
                </div>
                <p className="small">{s.means}</p>
                <p className="small caveat"><b>But:</b> {s.but}</p>
              </div>
            ))}
            {integrity.signals.length === 0 && (
              <p className="muted small">Nothing was flagged.</p>
            )}
          </div>

          <p className="hint" style={{ marginTop: 16 }}>{integrity.note}</p>
        </>
      )}
    </section>
  );
}

/* Who the candidate says they are, and what was actually checked. */
function ProfilePanel({ profile }) {
  const github = profile?.github;
  if (!profile || (!github && !profile.linkedin_url)) return null;
  const proven = github?.ownership?.proven;

  return (
    <section className="card">
      <h2 style={{ marginBottom: 6 }}>Candidate profile</h2>

      {github && (
        <>
          <p className="muted small" style={{ marginBottom: 12 }}>
            <a href={github.url} target="_blank" rel="noreferrer">
              github.com/{github.username}
            </a>{" "}
            <span className={`vbadge ${proven ? "verified" : "claimed"}`}>
              {proven ? "Ownership proven" : "Claimed, not proven"}
            </span>
          </p>

          {!proven && (
            <div className="notice" style={{ marginBottom: 14 }}>
              The candidate typed this username; nobody has proven the account
              is theirs. Read it as a claim.
            </div>
          )}

          <dl className="keyvals">
            <div><dt>Account age</dt><dd>{github.profile.age_years} yrs</dd></div>
            <div><dt>Public repos</dt><dd>{github.profile.public_repos}</dd></div>
            <div><dt>Own / forked</dt><dd>{github.activity.original} / {github.activity.forks}</dd></div>
            <div><dt>Active this year</dt><dd>{github.activity.pushed_last_year} repos</dd></div>
          </dl>

          {github.languages?.length > 0 && (
            <div className="chips" style={{ marginTop: 12 }}>
              {github.languages.slice(0, 8).map((l) => (
                <span key={l.language} className="chip">
                  {l.language} <b>{l.repos}</b>
                </span>
              ))}
            </div>
          )}

          {github.resume_match?.available && (
            <div className="field" style={{ marginTop: 16 }}>
              <label>Resume against public code</label>
              {github.resume_match.corroborated?.length > 0 && (
                <p className="small">
                  <b>Corroborated:</b>{" "}
                  {github.resume_match.corroborated.map((c) => c.language).join(", ")}
                </p>
              )}
              {github.resume_match.claimed_not_seen?.length > 0 && (
                <p className="small">
                  <b>Claimed, no public repo:</b>{" "}
                  {github.resume_match.claimed_not_seen.join(", ")}
                </p>
              )}
              <p className="hint">{github.resume_match.note}</p>
            </div>
          )}

          {github.observations?.length > 0 && (
            <div className="signals" style={{ marginTop: 16 }}>
              {github.observations.map((o, i) => (
                <div key={i} className="signal">
                  <p className="small"><b>{o.text}</b></p>
                  <p className="small caveat"><b>But:</b> {o.but}</p>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {profile.linkedin_url && (
        <p className="hint" style={{ marginTop: 16 }}>
          LinkedIn:{" "}
          <a href={profile.linkedin_url} target="_blank" rel="noreferrer">
            {profile.linkedin_url}
          </a>{" "}
          — a link the candidate supplied. Nothing has checked it: LinkedIn has
          no public API and its terms prohibit scraping. Open it and look.
        </p>
      )}
    </section>
  );
}

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

  // An interview that ended without being marked is a normal state, not an
  // error: the candidate dropped out, or the panel never reached its closing.
  // Reading `assessment.by_role` on a null blanked the whole page — a crash
  // rather than a message, and with the transcript sitting right there unread.
  if (!report.assessment) {
    const turns = report.transcript || [];
    return (
      <main className="page">
        <div className="stack">
          <div className="head">
            <div>
              <h1>{report.job_title || "Interview"}</h1>
              <p className="sub">Interview {report.session_id} · {report.status}</p>
            </div>
            <button className="btn-ghost" onClick={() => navigate("/")}>Back</button>
          </div>

          <div className="notice">
            {report.note || "This interview has not been marked."}
          </div>

          {/* Both of these exist whether or not the marking ran — a candidate
            * who dropped out still generated a monitoring log, and refusing to
            * show it because there is no score would hide the most likely
            * explanation for why there is no score. */}
          <ProfilePanel profile={report.candidate_profile} />
          <IntegrityPanel integrity={report.integrity} />

          {turns.length > 0 && (
            <section className="card">
              <h2>Transcript</h2>
              <p className="sub">
                {turns.length} turns. Nothing has been scored, so there is no
                evidence to cite yet.
              </p>
              <div className="transcript">
                {turns.map((t) => (
                  <div key={t.turn_id}
                       className={`turn ${t.speaker === "candidate" ? "you" : ""}`}>
                    <div className="speaker">{t.speaker}</div>
                    <div>{t.text}</div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </main>
    );
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

        {/* --- who they are, and how the room looked --- */}
        <ProfilePanel profile={report.candidate_profile} />
        <IntegrityPanel integrity={report.integrity} />

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
