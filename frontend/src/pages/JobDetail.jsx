import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import FileText from "../components/FileText";

/* One opening: its candidates, ranked, and the codes that let them in.
 *
 * THE RANKING IS NOT THE POINT OF THIS SCREEN, which is exactly why it is the
 * dangerous one. A sorted list invites a reader to stop at the top row, so the
 * basis behind every score — how many questions, how hard they got — sits in
 * the row itself, a thin interview is called out by name, and the only thing
 * a row click does is open the evidence.
 */

const DECISION = { hire: "Hire", no_hire: "No hire", maybe: "Maybe" };

function Code({ code }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="codechip"
      title="Copy this code"
      onClick={async (e) => {
        e.stopPropagation();
        try {
          await navigator.clipboard.writeText(code);
          setCopied(true);
          setTimeout(() => setCopied(false), 1800);
        } catch { /* clipboard blocked; the code is readable on screen */ }
      }}
    >
      {copied ? "Copied" : code}
    </button>
  );
}

function AddCandidate({ jobId, onAdded }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [resume, setResume] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [last, setLast] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api.addCandidate(jobId, {
        name: name.trim(),
        resume_text: resume.trim(),
      });
      setLast(result);
      setName("");
      setResume("");
      onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="row">
        <button className="btn-primary" onClick={() => setOpen(true)}>
          Add a candidate
        </button>
        {last && (
          <span className="muted small">
            Last added: <b>{last.candidate}</b> · code <Code code={last.invite_code} />
          </span>
        )}
      </div>
    );
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2 style={{ marginBottom: 4 }}>Add a candidate</h2>
      <p className="muted small" style={{ marginBottom: 16 }}>
        Their interview is written now, from this CV against this advert — the
        spoken questions and the coding problem both. It takes a few seconds,
        and it happens here rather than while they wait.
      </p>

      {error && <div className="notice error" style={{ marginBottom: 14 }}>{error}</div>}

      <div className="field">
        <label htmlFor="name">Their name</label>
        <input
          id="name" value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Priya Sharma" autoFocus
        />
      </div>

      <FileText
        id="cv"
        label="Their CV"
        value={resume}
        onChange={setResume}
        rows={8}
        placeholder="Drop their CV above, or paste it here."
        hint="Questions are grounded in what they claim to have built. Without a CV the panel falls back to a generic bank, which is the thing candidates notice and judges discount."
      />

      <div className="row" style={{ marginTop: 18 }}>
        <button className="btn-primary" disabled={busy || !name.trim() || resume.trim().length < 40}>
          {busy ? <><span className="spinner" /> Writing their interview…</> : "Create interview"}
        </button>
        <button type="button" className="btn-ghost" disabled={busy}
                onClick={() => setOpen(false)}>
          Done
        </button>
      </div>
      {busy && (
        <p className="hint">
          Planning questions and writing a coding problem. Around ten seconds.
        </p>
      )}
    </form>
  );
}

function Row({ row, codingEnabled, onOpen }) {
  const pct = row.score === null || row.score === undefined
    ? null : Math.round(row.score * 100);

  return (
    <button
      className={`board-row ${row.complete ? "" : "pending"}`}
      onClick={() => row.complete && onOpen(row.session_id)}
      disabled={!row.complete}
    >
      <span className="rank">{row.complete && row.rank ? row.rank : "·"}</span>

      <span className="who">
        <b>{row.candidate}</b>
        <span className="meta">
          {row.stage_label}
          {!row.claimed && " · code not used yet"}
        </span>
      </span>

      <span className="basis">
        {row.answers !== undefined && row.answers !== null && (
          <>
            {row.answers} answers · reached {row.difficulty_reached}
            {row.reliable === false && (
              <b className="thin"> · thin evidence</b>
            )}
          </>
        )}
      </span>

      <span className="rounds">
        {row.voice_score !== null && row.voice_score !== undefined && (
          <span className="chip">talk {Math.round(row.voice_score * 100)}%</span>
        )}
        {codingEnabled && row.coding_score !== null && row.coding_score !== undefined && (
          <span className="chip">code {Math.round(row.coding_score * 100)}%</span>
        )}
      </span>

      <span className="score">
        {pct === null ? <span className="muted">—</span> : `${pct}%`}
      </span>

      <span className="badges">
        {row.decision && (
          <span className={`badge ${row.decision}`}>{DECISION[row.decision]}</span>
        )}
        <Code code={row.invite_code} />
      </span>
    </button>
  );
}

export default function JobDetail() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const [board, setBoard] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.job(jobId).then(setBoard).catch((e) => setError(e.message));
  }, [jobId]);

  useEffect(load, [load]);

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

  if (!board) {
    return <main className="page center" style={{ paddingTop: 60 }}><span className="spinner" /></main>;
  }

  const rows = board.candidates || [];

  return (
    <main className="page">
      <div className="stack">
        <div className="head">
          <div>
            <h1 style={{ fontSize: 34 }}>{board.title}</h1>
            <p className="sub" style={{ marginTop: 6 }}>
              {rows.length} candidate{rows.length === 1 ? "" : "s"} ·{" "}
              {board.completed} complete ·{" "}
              {board.coding_enabled
                ? `conversation ${Math.round(board.voice_weight * 100)}% and coding ${Math.round((1 - board.voice_weight) * 100)}%`
                : "conversation only"}
            </p>
          </div>
          <button className="btn-ghost" style={{ marginLeft: "auto" }}
                  onClick={() => navigate("/")}>
            All openings
          </button>
        </div>

        <AddCandidate jobId={jobId} onAdded={load} />

        {rows.length === 0 ? (
          <div className="empty">
            <h3>No candidates yet</h3>
            <p className="small" style={{ maxWidth: "48ch", margin: "0 auto" }}>
              Add one with their CV. You get a code to send them; they sign in,
              enter it, and take the interview whenever they like.
            </p>
          </div>
        ) : (
          <>
            <div className="board">
              <div className="board-head">
                <span className="rank">#</span>
                <span className="who">Candidate</span>
                <span className="basis">What it was measured on</span>
                <span className="rounds">Rounds</span>
                <span className="score">Score</span>
                <span className="badges">Code</span>
              </div>
              {rows.map((r) => (
                <Row key={r.session_id} row={r}
                     codingEnabled={board.coding_enabled}
                     onOpen={(id) => navigate(`/assessment/${encodeURIComponent(id)}`)} />
              ))}
            </div>

            {board.caution && (
              <div className="notice warn"><b>Careful:</b> {board.caution}</div>
            )}

            <div className="notice">{board.note}</div>

            {board.spread && (
              <p className="muted small">
                Across the {board.spread.n} completed interviews: best{" "}
                {Math.round(board.spread.best * 100)}%, median{" "}
                {Math.round(board.spread.median * 100)}%, lowest{" "}
                {Math.round(board.spread.worst * 100)}%. Shown as context — no
                candidate's own score is adjusted by how the others did.
              </p>
            )}
          </>
        )}
      </div>
    </main>
  );
}
