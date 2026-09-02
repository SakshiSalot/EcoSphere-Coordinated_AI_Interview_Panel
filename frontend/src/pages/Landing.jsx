import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import LaptopArt from "../components/LaptopArt";

/* The front page.
 *
 * Somebody arriving here has been sent a link and does not know what this is,
 * so the page says what happens and that the interviewers are AI — before
 * asking for anything. Signing in is a separate page, reached from here.
 *
 * NO INTERVIEWER NAMES ANYWHERE ON THIS PAGE. The panel is configured per
 * role — three today, a hiring manager and a customer tomorrow — and a landing
 * page that names Priya, Arjun and Meera is wrong the first time somebody
 * changes personas.yaml. What is true regardless is what the system DOES, so
 * that is what this page claims.
 */

const ADVANTAGES = [
  {
    title: "A panel, not one interviewer",
    body: "Several perspectives at once. An answer that is technically excellent but never mentions a customer gets challenged on exactly that, by somebody whose job it is to notice.",
  },
  {
    title: "It adapts as you talk",
    body: "Questions come from what you actually said, not a fixed list, and the difficulty rises and falls with how you are doing.",
  },
  {
    title: "Interrupt it",
    body: "A real-time voice conversation. Talk over an interviewer mid-sentence and they stop, exactly as a person would.",
  },
  {
    title: "Nothing is scored without a quote",
    body: "Every mark carries the words that earned it, checked against the transcript. No judgement appears that cannot be shown back to you.",
  },
  {
    title: "A person makes the decision",
    body: "The panel assesses and lays out its evidence. A human reads it and decides. The system never hires or rejects anyone.",
  },
  {
    title: "Sized for the role",
    body: "The panel is configured per job — technical, product, behavioural, a hiring manager, a customer. Add a perspective without changing anything else.",
  },
];

export default function Landing() {
  const { user } = useAuth();
  const navigate = useNavigate();

  if (user) return <Navigate to="/" replace />;

  return (
    <main>
      <section className="hero">
        <div className="hero-inner">
          <div>
            <h1>Panel interviews, without the scheduling.</h1>
            <p className="kicker">
              &#123; one conversation, several interviewers, <em>every score quoted</em> &#125;
            </p>

            <div className="hero-actions">
              <button className="btn-cta" onClick={() => navigate("/signin")}>
                <span>Start your interview</span>
                <span className="chev" aria-hidden="true">›</span>
              </button>
              <span className="or">or</span>
              <button className="textlink" onClick={() => navigate("/signin")}>
                Sign in
              </button>
            </div>

          </div>

          <div className="hero-art">
            <LaptopArt />
          </div>
        </div>
      </section>

      <section className="section">
        <p className="eyebrow">Why a panel</p>
        <h2 style={{ marginBottom: 26, maxWidth: "24ch" }}>
          One interviewer sees one thing.
        </h2>
        <div className="grid3">
          {ADVANTAGES.map((a, i) => (
            <div className="persona" key={a.title}>
              <div className="initial" aria-hidden="true">
                {String(i + 1).padStart(2, "0")}
              </div>
              <b>{a.title}</b>
              <p className="small muted" style={{ marginTop: 8 }}>{a.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="band">
        <div className="band-inner">
          <div>
            <p className="eyebrow">Built on Agora</p>
            <h2 style={{ marginBottom: 16 }}>
              Real conversation, not a form that talks.
            </h2>
            <p style={{ maxWidth: "48ch", marginBottom: 26 }}>
              Agora’s Conversational AI Engine carries the audio, hears when you
              stop speaking and lets you cut in mid-sentence. What we built on
              top is the panel — who holds the floor, what they ask next, and
              why every mark can be traced back to something you said.
            </p>
            <button className="btn-cta" onClick={() => navigate("/signin")}>
              <span>Start your interview</span>
              <span className="chev" aria-hidden="true">›</span>
            </button>
          </div>

          <div className="facts">
            <div className="fact">
              <b>Real-time voice</b>
              <span>Speech in, speech out. No typing, no waiting on a page to load.</span>
            </div>
            <div className="fact">
              <b>Interruptible</b>
              <span>Talk over an interviewer and they stop, the way a person would.</span>
            </div>
            <div className="fact">
              <b>Adaptive</b>
              <span>Follow-ups come from what you said, and the difficulty moves with you.</span>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
