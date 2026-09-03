import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import FileText from "../components/FileText";

/* Creating an opening.
 *
 * The advert lives on the OPENING, not on each interview, and that is the
 * whole reason a leaderboard means anything: every candidate is measured
 * against the same requirements. Pasting a slightly different job description
 * into five interviews would produce five scores that cannot be compared, and
 * ranking them would be arithmetic on incompatible units.
 */
export default function NewJob() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [coding, setCoding] = useState(true);
  const [voiceWeight, setVoiceWeight] = useState(70);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const job = await api.createJob({
        title: title.trim(),
        description: description.trim(),
        coding_enabled: coding,
        voice_weight: voiceWeight / 100,
      });
      navigate(`/jobs/${encodeURIComponent(job.job_id)}`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <main className="page narrow">
      <div className="stack">
        <div className="head">
          <div>
            <h1>New opening</h1>
            <p className="sub">
              One advert, however many applicants. Every interview under it is
              built from this description, which is what makes the scores
              comparable to each other.
            </p>
          </div>
        </div>

        {error && <div className="notice error">{error}</div>}

        <form className="card" onSubmit={submit}>
          <div className="field">
            <label htmlFor="title">Role</label>
            <input
              id="title" value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder="Senior Backend Engineer" autoFocus
            />
          </div>

          <FileText
            id="jd"
            label="The job description"
            value={description}
            onChange={setDescription}
            rows={9}
            placeholder="Paste the advert, or drop the PDF above."
            hint="The panel plans its questions from this and each candidate's CV. The more concrete the requirements, the more specific the questions."
          />

          <div className="field">
            <label>Rounds</label>
            <label className="consent" style={{ marginTop: 6 }}>
              <input
                type="checkbox" checked={coding}
                onChange={(e) => setCoding(e.target.checked)}
              />
              <span>
                <b>Include a coding round</b>
                <span className="small muted">
                  A problem written from each candidate's own CV, run in an
                  external sandbox. They can take it separately from the
                  conversation — the same evening or the next morning.
                </span>
              </span>
            </label>
          </div>

          {coding && (
            <div className="field">
              <label htmlFor="weight">
                Weighting — conversation {voiceWeight}% · coding {100 - voiceWeight}%
              </label>
              <input
                id="weight" type="range" min="30" max="90" step="5"
                value={voiceWeight}
                onChange={(e) => setVoiceWeight(Number(e.target.value))}
              />
              <p className="hint">
                Only applies once a candidate has done both. A round nobody has
                sat yet is never counted as zero — someone who has not taken the
                coding exercise has not failed it.
              </p>
            </div>
          )}

          <div className="row" style={{ marginTop: 20 }}>
            <button className="btn-primary" disabled={busy || !title.trim()}>
              {busy ? <span className="spinner" /> : "Create opening"}
            </button>
            <button type="button" className="btn-ghost" disabled={busy}
                    onClick={() => navigate("/")}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
