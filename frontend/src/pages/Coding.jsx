import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { createMonitor } from "../integrity";

/* The coding round.
 *
 * SEPARATE FROM THE CONVERSATION ON PURPOSE, and everything about this page
 * follows from that: a candidate may open it the next morning, on a different
 * machine, after the gateway has restarted twice. So nothing lives in memory —
 * the code is saved as they type, and closing the tab costs nothing.
 *
 * THE TESTS ARE VISIBLE. This is an interview, not a submission portal. Hidden
 * tests turn it into a guessing game, and what is being judged is how someone
 * reasons about a problem, not whether they can divine an edge case nobody
 * mentioned.
 *
 * Submitting is final. Idempotent by refusal rather than by recomputation:
 * letting someone submit repeatedly is letting them keep trying until the
 * sandbox happens to agree with them.
 */

const SAVE_AFTER_MS = 1200;

function Tests({ tests, results }) {
  return (
    <div className="tests">
      {tests.map((t, i) => {
        const r = results?.[i];
        return (
          <div key={i} className={`test ${r ? (r.passed ? "pass" : "fail") : ""}`}>
            <div className="top">
              <b>Test {i + 1}</b>
              {r && <span className="verdict">{r.passed ? "passed" : "failed"}</span>}
            </div>
            <p className="small muted">{t.why}</p>
            <div className="io">
              <span><em>in</em> <code>{t.stdin || "(none)"}</code></span>
              <span><em>expect</em> <code>{t.expected}</code></span>
              {r && !r.passed && r.got && (
                <span><em>got</em> <code>{r.got}</code></span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function Coding() {
  const { sessionId } = useParams();
  const navigate = useNavigate();

  const [round, setRound] = useState(null);
  const [source, setSource] = useState("");
  const [stdin, setStdin] = useState("");
  const [output, setOutput] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(true);

  const saveTimer = useRef(null);
  const monitor = useRef(null);

  useEffect(() => {
    api.coding(sessionId)
      .then((r) => {
        setRound(r);
        if (r.ready) {
          setSource(r.source || "");
          setStdin(r.tests?.[0]?.stdin || "");
        }
      })
      .catch((e) => setError(e.message));
  }, [sessionId]);

  /* Focus monitoring only — no camera on this round. Pasting a solution and
   * switching away to find one are the signals that mean anything while
   * somebody writes code, and asking for a webcam to detect a paste would be
   * collecting more than the question needs. */
  useEffect(() => {
    if (!round?.ready || round.submitted) return;
    monitor.current = createMonitor({ sessionId, stage: "coding" });
    monitor.current.start(null).catch(() => {});
    return () => {
      monitor.current?.stop().catch(() => {});
      monitor.current = null;
    };
  }, [sessionId, round?.ready, round?.submitted]);

  // Autosave. The round survives a closed tab, so the code has to as well.
  const edit = useCallback((next) => {
    setSource(next);
    setSaved(false);
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      api.codingSave(sessionId, next).then(() => setSaved(true)).catch(() => {});
    }, SAVE_AFTER_MS);
  }, [sessionId]);

  useEffect(() => () => clearTimeout(saveTimer.current), []);

  const run = async () => {
    setBusy("run");
    setError("");
    setOutput(null);
    try {
      setOutput(await api.codingRun(sessionId, source, stdin));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  const submit = async () => {
    if (!window.confirm(
      "Submit this round? Every test runs once and the round closes — you cannot submit again."
    )) return;
    setBusy("submit");
    setError("");
    try {
      const r = await api.codingSubmit(sessionId, source);
      setResult(r);
      setRound((prev) => ({ ...prev, submitted: true }));
      monitor.current?.stop().catch(() => {});
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  /* Tab inserts spaces instead of leaving the textarea. Without this the first
   * press of Tab moves focus to the Run button, which reads as the editor
   * being broken. */
  const key = (e) => {
    if (e.key !== "Tab") return;
    e.preventDefault();
    const el = e.target;
    const { selectionStart: a, selectionEnd: b } = el;
    const next = `${source.slice(0, a)}    ${source.slice(b)}`;
    edit(next);
    requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = a + 4; });
  };

  if (error && !round) {
    return (
      <main className="page narrow">
        <div className="stack">
          <div className="notice error">{error}</div>
          <button className="btn-ghost" onClick={() => navigate("/")}>Back</button>
        </div>
      </main>
    );
  }

  if (!round) {
    return <main className="page center" style={{ paddingTop: 60 }}><span className="spinner" /></main>;
  }

  if (!round.ready) {
    return (
      <main className="page narrow">
        <div className="stack">
          <h1>No coding round</h1>
          <div className="notice">
            This interview does not have a coding exercise attached to it.
          </div>
          <button className="btn-ghost" onClick={() => navigate("/")}>Back</button>
        </div>
      </main>
    );
  }

  return (
    <main className="page">
      <div className="stack">
        <div className="head">
          <div>
            <h1 style={{ fontSize: 30 }}>{round.title}</h1>
            <p className="sub" style={{ marginTop: 6 }}>
              {round.language} · written from your CV: {round.grounded_in}
            </p>
          </div>
          <span className={`status ${saved ? "" : "live"}`} style={{ marginLeft: "auto" }}>
            {round.submitted ? "Submitted" : saved ? "Saved" : "Saving…"}
          </span>
        </div>

        {error && <div className="notice error">{error}</div>}

        {result && (
          <div className="notice ok">
            <b>{result.passed} of {result.total} tests passed.</b>{" "}
            {result.weight_note}
          </div>
        )}

        {round.submitted && !result && (
          <div className="notice info">
            You have already submitted this round. Your answer is with the
            hiring team.
          </div>
        )}

        <div className="coding">
          <section className="card">
            <h2 style={{ marginBottom: 10 }}>The problem</h2>
            <p style={{ marginBottom: 18 }}>{round.prompt}</p>

            <h3 style={{ marginBottom: 8 }}>Tests</h3>
            <p className="muted small" style={{ marginBottom: 12 }}>
              Shown on purpose — this is a conversation, not a guessing game.
            </p>
            <Tests tests={round.tests} results={result?.results} />
          </section>

          <section className="card">
            <div className="field">
              <label htmlFor="src">Your solution</label>
              <textarea
                id="src" className="editor" value={source} spellCheck="false"
                onChange={(e) => edit(e.target.value)}
                onKeyDown={key}
                disabled={round.submitted}
                rows={18}
              />
            </div>

            <div className="field">
              <label htmlFor="stdin">Input for a single run</label>
              <input
                id="stdin" value={stdin} onChange={(e) => setStdin(e.target.value)}
                disabled={round.submitted}
              />
            </div>

            <div className="row">
              <button className="btn-ghost" onClick={run}
                      disabled={!!busy || round.submitted || !source.trim()}>
                {busy === "run" ? <span className="spinner" /> : "Run once"}
              </button>
              <button className="btn-primary" onClick={submit}
                      disabled={!!busy || round.submitted || !source.trim()}>
                {busy === "submit" ? <span className="spinner" /> : "Submit round"}
              </button>
            </div>

            {output && (
              <div className={`runout ${output.ok ? "ok" : "bad"}`}>
                <div className="top">
                  <b>{output.verdict}</b>
                  {output.time && <span>{output.time}s</span>}
                </div>
                {output.stdout && <pre>{output.stdout}</pre>}
                {output.stderr && <pre className="err">{output.stderr}</pre>}
                {!output.stdout && !output.stderr && (
                  <p className="small muted">No output.</p>
                )}
              </div>
            )}

            <p className="hint" style={{ marginTop: 14 }}>
              Your code runs on an external sandbox, never on this server.
              Tab switches and pastes are recorded during this round and shown
              to the hiring team alongside your work; they carry no marks.
            </p>
          </section>
        </div>

        <button className="btn-ghost" onClick={() => navigate("/")}>
          Back to your interviews
        </button>
      </div>
    </main>
  );
}
