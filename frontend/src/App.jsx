import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import Landing from "./pages/Landing";
import SignIn from "./pages/SignIn";
import CandidateHome from "./pages/CandidateHome";
import OperatorHome from "./pages/OperatorHome";
import Prepare from "./pages/Prepare";
import Interview from "./pages/Interview";
import Assessment from "./pages/Assessment";

/* The bar every page sits under. Dark, so it belongs to the hero on the
 * landing page and reads as a product chrome everywhere else.
 *
 * The mark is a level meter — four bars. This is a VOICE product, and a
 * generic geometric glyph would say nothing about it. */
function TopBar() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a
          className="wordmark"
          href="/"
          onClick={(e) => { e.preventDefault(); navigate("/"); }}
        >
          <span className="bars" aria-hidden="true">
            <i /><i /><i /><i />
          </span>
          Lumina
        </a>

        <nav className="topnav">
          {user ? (
            <>
              <NavLink to="/" end className={({ isActive }) => (isActive ? "on" : "")}>
                {user.role === "operator" ? "Interviews" : "Your interviews"}
              </NavLink>
              <span className="whoami">
                <b>{user.full_name || user.username}</b>
                {user.role}
              </span>
              <button
                className="cta"
                onClick={() => { signOut(); navigate("/"); }}
              >
                Sign out
              </button>
            </>
          ) : (
            <button className="cta" onClick={() => navigate("/signin")}>
              Sign in
            </button>
          )}
        </nav>
      </div>
    </header>
  );
}

/* The server enforces all of this too. This only stops the UI rendering a
 * page it is about to be refused. */
function Protected({ role, children }) {
  const { user, checking } = useAuth();
  if (checking) return <div className="page center"><span className="spinner" /></div>;
  if (!user) return <Navigate to="/signin" replace />;
  if (role && user.role !== role) return <Navigate to="/" replace />;
  return children;
}

function Home() {
  const { user, checking } = useAuth();
  if (checking) return <div className="page center"><span className="spinner" /></div>;
  if (!user) return <Landing />;
  return user.role === "operator" ? <OperatorHome /> : <CandidateHome />;
}

export default function App() {
  return (
    <>
      <TopBar />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/signin" element={<SignIn />} />
        <Route
          path="/prepare/:sessionId"
          element={<Protected role="candidate"><Prepare /></Protected>}
        />
        <Route
          path="/interview/:sessionId"
          element={<Protected role="candidate"><Interview /></Protected>}
        />
        <Route
          path="/assessment/:sessionId"
          element={<Protected role="operator"><Assessment /></Protected>}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
