import { useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { token } from "../api";

/* Resume and job description, before the interview.
 *
 * This is what makes the panel ask about the role you applied for and the work
 * you actually did, instead of a generic backend quiz — which is exactly what
 * a candidate notices and a judge discounts.
 *
 * It runs BEFORE anyone joins the channel, on purpose. Building the question
 * plan takes several seconds of model time; doing it mid-conversation would
 * add that to a live turn, and conversational quality is what gets scored.
 *
 * Uploads go through fetch directly rather than the api helper: this is
 * multipart, and setting Content-Type by hand on a FormData body strips the
 * boundary the server needs to parse it.
 */

function Drop({ id, label, hint, file, onFile }) {
  const input = useRef(null);
  const [over, setOver] = useState(false);

  return (
    <div
      className={`drop ${over ? "over" : ""} ${file ? "filled" : ""}`}
      onClick={() => input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (e.dataTransfer.files?.[0]) onFile(e.dataTransfer.files[0]);
      }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
    >
      <input
        id={id} ref={input} type="file" accept=".pdf,.txt,.md,.rtf"
        onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
      />
      {file ? (
        <>
          <b>{file.name}</b>
          <span>{(file.size / 1024).toFixed(0)} KB · click to replace</span>
        </>
      ) : (
        <>
          <b>{label}</b>
          <span>{hint}</span>
        </>
      )}
    </div>
  );
}

export default function Prepare() {
  const { sessionId } = useParams();
  const navigate = useNavigate();

  const [resume, setResume] = useState(null);
  const [jobFile, setJobFile] = useState(null);
  const [jobText, setJobText] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);

    const form = new FormData();
    if (resume) form.append("resume", resume);
    if (jobFile) form.append("job_file", jobFile);
    if (jobText.trim()) form.append("job_description", jobText.trim());
    if (jobTitle.trim()) form.append("job_title", jobTitle.trim());

    try {
      const res = await fetch(`/session/${encodeURIComponent(sessionId)}/prepare`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token.get()}` },
        body: form,
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.detail || `Could not prepare (${res.status}).`);
      setDone(payload);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <main className="page narrow">
        <div className="stack">
          <h1>Ready</h1>
          <div className="notice info">
            <b>{done.planned} questions prepared for you.</b>{" "}
            {done.personalised
              ? "They are drawn from your resume and the job description."
              : "The panel will use its general question bank — nothing readable came out of the files."}
          </div>
          <div className="card">
            <p className="muted small" style={{ marginBottom: 16 }}>
              The interviewers do not read from a script. Those questions steer
              what gets covered; how each interviewer asks, and what they follow
              up on, is decided as you talk.
            </p>
            <button
              className="btn-primary"
              onClick={() => navigate(`/interview/${encodeURIComponent(sessionId)}`)}
            >
              Continue to the interview
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="page narrow">
      <div className="stack">
        <div className="head">
          <div>
            <h1>Before you start</h1>
            <p className="sub">
              Add your resume and the job description. The panel reads both and
              plans its questions from them.
            </p>
          </div>
        </div>

        {error && <div className="notice error">{error}</div>}

        <form className="card" onSubmit={submit}>
          <div className="field">
            <label htmlFor="resume">Your resume</label>
            <Drop
              id="resume" file={resume} onFile={setResume}
              label="Drop your resume here, or click to choose"
              hint="PDF or plain text, up to 5 MB"
            />
            <p className="hint">
              If it is a scan with no text in it, nothing can be read from it —
              paste the text into the box below instead.
            </p>
          </div>

          <div className="field">
            <label htmlFor="job_title">Role you applied for</label>
            <input
              id="job_title" value={jobTitle} onChange={(e) => setJobTitle(e.target.value)}
              placeholder="Senior Backend Engineer"
            />
          </div>

          <div className="field">
            <label htmlFor="job_file">The job description</label>
            <Drop
              id="job_file" file={jobFile} onFile={setJobFile}
              label="Drop the job advert here, or click to choose"
              hint="PDF or plain text"
            />
          </div>

          <div className="field">
            <label htmlFor="job_text">…or paste it</label>
            <textarea
              id="job_text" value={jobText} onChange={(e) => setJobText(e.target.value)}
              placeholder="Paste the job advert here if you do not have a file."
            />
          </div>

          <div className="row" style={{ marginTop: 20 }}>
            <button
              className="btn-primary"
              disabled={busy || (!resume && !jobFile && !jobText.trim())}
            >
              {busy ? <span className="spinner" /> : "Prepare my interview"}
            </button>
            <button
              type="button" className="btn-ghost" disabled={busy}
              onClick={() => navigate(`/interview/${encodeURIComponent(sessionId)}`)}
            >
              Skip — use general questions
            </button>
          </div>
          <p className="hint">
            This takes a few seconds. It happens now rather than during the
            conversation, so the interviewers never leave you waiting.
          </p>
        </form>
      </div>
    </main>
  );
}
