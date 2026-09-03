import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

/* The candidate's own profile links.
 *
 * TWO WORDS THAT ARE NOT THE SAME, and this page's whole job is keeping them
 * apart. SAVING a GitHub username is a claim — anyone can type `torvalds`.
 * VERIFYING it means proving control of the account, which is only possible if
 * the candidate publishes a code we generate somewhere only the account holder
 * can write. Until they do, the badge says "claimed", and the operator's copy
 * says the same. A tick that means "they typed something" is worse than no
 * tick at all, because someone will trust it.
 *
 * LinkedIn gets no tick in any circumstance. There is no public API and the
 * terms prohibit scraping, so it is stored as a link for a human to open and
 * labelled exactly that.
 */

function Badge({ status }) {
  const label = { verified: "Verified", claimed: "Claimed, not proven" }[status];
  if (!label) return null;
  return <span className={`vbadge ${status}`}>{label}</span>;
}

function Facts({ github }) {
  const p = github.profile;
  const a = github.activity;
  const push =
    a.days_since_last_push === null
      ? "never"
      : a.days_since_last_push < 45
        ? `${a.days_since_last_push} days ago`
        : `${Math.round(a.days_since_last_push / 30)} months ago`;

  return (
    <dl className="keyvals">
      <div><dt>Account age</dt><dd>{p.age_years} years</dd></div>
      <div><dt>Public repos</dt><dd>{p.public_repos}</dd></div>
      <div><dt>Own / forked</dt><dd>{a.original} / {a.forks}</dd></div>
      <div><dt>Last public push</dt><dd>{push}</dd></div>
      <div><dt>Followers</dt><dd>{p.followers}</dd></div>
      <div><dt>Active this year</dt><dd>{a.pushed_last_year} repos</dd></div>
    </dl>
  );
}

export default function Profile() {
  const navigate = useNavigate();
  const [profile, setProfile] = useState(null);
  const [github, setGithub] = useState("");
  const [linkedin, setLinkedin] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.profile()
      .then((p) => {
        setProfile(p);
        setGithub(p.github_username || "");
        setLinkedin(p.linkedin_url || "");
      })
      .catch((e) => setError(e.message));
  }, []);

  const save = async () => {
    setBusy("save");
    setError("");
    try {
      setProfile(await api.saveLinks({
        github_username: github,
        linkedin_url: linkedin,
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  const verify = async () => {
    setBusy("verify");
    setError("");
    try {
      const result = await api.verifyGithub(github);
      setProfile((p) => ({ ...p, github: result, github_username: result.username }));
      setGithub(result.username);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  const copy = async () => {
    const code = profile?.github?.challenge || profile?.challenge;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard is blocked in some contexts; the code is on screen anyway.
    }
  };

  if (!profile && !error) {
    return <main className="page center" style={{ paddingTop: 60 }}><span className="spinner" /></main>;
  }

  const result = profile?.github;
  const code = result?.challenge || profile?.challenge;
  const proven = result?.ownership?.proven;

  return (
    <main className="page narrow">
      <div className="stack">
        <div className="head">
          <div>
            <h1>Your profile</h1>
            <p className="sub">
              Optional. If you add your GitHub, the hiring team sees your public
              work next to your interview instead of taking the resume's word
              for it.
            </p>
          </div>
        </div>

        {error && <div className="notice error">{error}</div>}

        <div className="card">
          <div className="field">
            <label htmlFor="github">GitHub</label>
            <input
              id="github" value={github}
              onChange={(e) => setGithub(e.target.value)}
              placeholder="your-username, or paste the profile URL"
            />
            <p className="hint">
              Only public information is read: your profile, your public
              repositories and the languages in them. Nothing is written, and
              nothing private is requested — this never asks you to sign in to
              GitHub.
            </p>
          </div>

          <div className="field">
            <label htmlFor="linkedin">LinkedIn</label>
            <input
              id="linkedin" value={linkedin}
              onChange={(e) => setLinkedin(e.target.value)}
              placeholder="https://linkedin.com/in/you"
            />
            <p className="hint">
              Stored as a link for a person to open. LinkedIn has no public API
              and its terms prohibit scraping, so nothing here checks it — and
              it will never be shown with a tick that suggests otherwise.
            </p>
          </div>

          <div className="row" style={{ marginTop: 18 }}>
            <button className="btn-ghost" onClick={save} disabled={!!busy}>
              {busy === "save" ? <span className="spinner" /> : "Save"}
            </button>
            <button className="btn-primary" onClick={verify} disabled={!!busy || !github.trim()}>
              {busy === "verify" ? <span className="spinner" /> : "Check my GitHub"}
            </button>
          </div>
        </div>

        {result && (
          <div className="card">
            <div className="head" style={{ marginBottom: 14 }}>
              <div>
                <h2 style={{ margin: 0 }}>
                  <a href={result.url} target="_blank" rel="noreferrer">
                    {result.username}
                  </a>{" "}
                  <Badge status={result.status} />
                </h2>
                {result.profile.name && (
                  <p className="sub" style={{ marginTop: 4 }}>{result.profile.name}</p>
                )}
              </div>
            </div>

            {!proven && (
              <div className="notice info">
                <b>This account has not been proven to be yours.</b>
                <p>
                  Anyone can type a username into a form, so the hiring team is
                  shown this as a claim. To prove it, put this code in your
                  GitHub profile bio, or make a public gist with it as the
                  description — then check again.
                </p>
                <div className="row" style={{ marginTop: 10, alignItems: "center" }}>
                  <code className="challenge">{code}</code>
                  <button className="btn-ghost small" onClick={copy}>
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
              </div>
            )}

            {proven && (
              <div className="notice ok">
                <b>Ownership proven</b> — the code was found in your{" "}
                {result.ownership.where}.
              </div>
            )}

            <Facts github={result} />

            {result.languages.length > 0 && (
              <div className="field">
                <label>Languages in public repos</label>
                <div className="chips">
                  {result.languages.map((l) => (
                    <span key={l.language} className="chip">
                      {l.language} <b>{l.repos}</b>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {result.resume_match?.available && (
              <div className="field">
                <label>Against your resume</label>
                {result.resume_match.corroborated.length > 0 && (
                  <p className="small">
                    <b>Backed by public code:</b>{" "}
                    {result.resume_match.corroborated.map((c) => c.language).join(", ")}
                  </p>
                )}
                {result.resume_match.claimed_not_seen.length > 0 && (
                  <p className="small">
                    <b>On the resume, not in public repos:</b>{" "}
                    {result.resume_match.claimed_not_seen.join(", ")}
                  </p>
                )}
                <p className="hint">{result.resume_match.note}</p>
              </div>
            )}

            {result.top_repos.length > 0 && (
              <div className="field">
                <label>Most recent work</label>
                <div className="repos">
                  {result.top_repos.map((r) => (
                    <a key={r.name} className="repo" href={r.url}
                       target="_blank" rel="noreferrer">
                      <span className="top">
                        <b>{r.name}</b>
                        <span className="meta">
                          {r.language} {r.stars > 0 && `· ★ ${r.stars}`}
                        </span>
                      </span>
                      {r.description && <span className="desc">{r.description}</span>}
                    </a>
                  ))}
                </div>
              </div>
            )}

            <p className="hint" style={{ marginTop: 16 }}>{result.caveat}</p>
          </div>
        )}

        <button className="btn-ghost" onClick={() => navigate("/")}>
          Back to your interviews
        </button>
      </div>
    </main>
  );
}
