import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

/* Signing in, on its own page.
 *
 * One form for both cases. A candidate who has never been here chooses a
 * username and password and the account is created; from then on the same
 * ones are required. Nobody hands an interview candidate a username in
 * advance, and a separate sign-up flow is one more thing to get wrong before
 * an interview that is already stressful.
 *
 * The failure mode of that convenience is a typo quietly producing a second,
 * empty account — so the page warns before, and the home screen says so after.
 */
export default function SignIn() {
  const { user, signIn } = useAuth();
  const navigate = useNavigate();
  const [fields, setFields] = useState({ username: "", password: "", full_name: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  if (user) return <Navigate to="/" replace />;

  const set = (key) => (e) => setFields({ ...fields, [key]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await signIn(fields.username.trim(), fields.password);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="page narrow">
      <div className="stack">
        <div className="head" style={{ marginTop: 12 }}>
          <div>
            <h1 style={{ fontSize: 34 }}>Sign in</h1>
            <p className="sub" style={{ marginTop: 8 }}>
              New here? Choose any username and password — we will create your
              account. After that, use the same ones.
            </p>
          </div>
        </div>

        <div className="disclosure">
          <span aria-hidden="true">◆</span>
          <div>
            <b>Interviews on this platform are conducted by AI.</b>
            <p>
              You will not be speaking to a person. Conversations are
              transcribed and assessed, and a human reviews the result before
              any decision.
            </p>
          </div>
        </div>

        {error && <div className="notice error">{error}</div>}

        <form className="card" onSubmit={submit}>
          <div className="field">
            <label htmlFor="username">Username</label>
            <input
              id="username" value={fields.username} onChange={set("username")}
              autoComplete="username" required autoFocus
              autoCapitalize="none" spellCheck="false"
              placeholder="something you will remember"
            />
            <p className="hint">
              Use exactly the same one each time — a different spelling starts a
              new account, and your interview will not be on it.
            </p>
          </div>

          <div className="field">
            <label htmlFor="full_name">
              Your name <span className="muted">(optional)</span>
            </label>
            <input
              id="full_name" value={fields.full_name} onChange={set("full_name")}
              autoComplete="name" placeholder="how the interviewers address you"
            />
          </div>

          <div className="field">
            <label htmlFor="password">Password</label>
            <div className="password-field">
              <input
                id="password" type={showPassword ? "text" : "password"}
                value={fields.password}
                onChange={set("password")} autoComplete="current-password"
                required minLength={8}
              />
              <button
                type="button"
                className="reveal"
                onClick={() => setShowPassword((v) => !v)}
                aria-pressed={showPassword}
                aria-label={showPassword ? "Hide password" : "Show password"}
                tabIndex={-1}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
            <p className="hint">At least 8 characters.</p>
          </div>

          <div style={{ marginTop: 22 }}>
            <button className="btn-primary btn-block" disabled={busy}>
              {busy ? <span className="spinner" /> : "Continue"}
            </button>
          </div>
        </form>

        <p className="center small muted">
          Interview operators use the account they were given.{" "}
          <a
            href="/"
            onClick={(e) => { e.preventDefault(); navigate("/"); }}
          >
            What is this?
          </a>
        </p>
      </div>
    </main>
  );
}
