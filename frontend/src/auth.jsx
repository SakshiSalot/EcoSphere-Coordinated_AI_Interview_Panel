import { createContext, useContext, useEffect, useState } from "react";
import { api, token } from "./api";

/* Who is signed in, for the whole app.
 *
 * The stored token is checked against /auth/me on load rather than trusted.
 * A token in sessionStorage may be expired, or revoked, or from a gateway
 * that has since restarted with a different signing key — and a UI that
 * assumes it is valid shows a dashboard that then 401s on every request.
 */

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  // True only for the moments right after an account was created on first
  // sign-in. The home screen says so, which is what turns a mistyped username
  // from "my interview is gone" into "ah, I am new here".
  const [justCreated, setJustCreated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!token.get()) { setChecking(false); return; }
      try {
        const { user } = await api.me();
        if (!cancelled) setUser(user);
      } catch {
        token.clear();
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const signIn = async (username, password) => {
    const result = await api.login(username, password);
    token.set(result.token);
    setUser(result.user);
    setJustCreated(Boolean(result.created));
    // `created` says the account did not exist a moment ago. The page shows
    // that, so a mistyped username reads as "welcome, you are new here"
    // rather than "your interview has vanished".
    return result;
  };

  const register = async (fields) => {
    const result = await api.register(fields);
    token.set(result.token);
    setUser(result.user);
    return result.user;
  };

  const signOut = () => {
    token.clear();
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{ user, checking, justCreated, signIn, register, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
