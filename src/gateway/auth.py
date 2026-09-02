"""Who is calling, and what they are allowed to see.

The gateway lives on a public URL — that is the point, Agora has to reach it —
so every session endpoint is reachable by anyone who finds the address. Without
a check, guessing a session id is enough to read a candidate's whole interview,
or to call /start and spend the free agent-minutes.

TWO KINDS OF CALLER, deliberately:

  * The OPERATOR holds `GATEWAY_SHARED_SECRET`: our own scripts, and Agora.
    One key, held only by processes we run, never sent to a browser.
  * A BROWSER holds a session token, minted at /setup, bound to ONE session id
    and ONE role. A recruiter's token cannot read another candidate's
    interview, and a candidate's token cannot read their own marks.

That binding is the whole point. A single shared password answers "may you
talk to this gateway?" and never "may you see THIS interview?", so every
recruiter could read every candidate.

WHAT A TOKEN IS — signed, not encrypted:

    base64url("t-abc|recruiter|1756800000") . base64url(HMAC-SHA256)

Anyone may read the contents; nothing secret is in there. What nobody can do
is change a field, because the signature would no longer match.

WHAT THIS DEFENDS AGAINST, and where:

  forging a token          -> HMAC-SHA256 under a secret the caller lacks
  timing attacks           -> hmac.compare_digest, never ==
  replay on another session-> session id is INSIDE the signed payload
  role escalation          -> role is INSIDE the signed payload
  replay forever           -> expiry is INSIDE the signed payload
  alg:none confusion       -> there is no algorithm field to attack
  malformed input          -> every parse wrapped; failure is always denial

Order matters: the signature is checked FIRST, and the claims are only read
once it proves the payload is ours. Reading claims first means parsing
attacker-controlled bytes as though they were trusted.

WHAT IT DOES NOT DEFEND AGAINST: a leaked secret (total compromise — rotate,
and every token dies with it), a leaked token before it expires (no revocation
list; the TTL is the mitigation), plain HTTP (tokens are readable in transit —
never run this off localhost without TLS), and request flooding (there is no
rate limit here). All are real; none are solved by a token format.

No JWT library, on purpose. An HMAC over three fields is one stdlib import and
has no algorithm field to be talked out of. Kept out of `app.py` for the same
reason `agora/tokens.py` is its own module: minting and verifying are pure, so
the cases that matter — another session's token, an expired one, a tampered
one — are testable with no server running.
"""

import base64
import hmac
import logging
import time
from hashlib import sha256

from src import config

log = logging.getLogger("gateway.auth")

OPERATOR = "operator"
RECRUITER = "recruiter"
CANDIDATE = "candidate"

SESSION_ROLES = (RECRUITER, CANDIDATE)

# Different lives, because the two tokens have very different exposure.
#
# The candidate's rides in a URL — browser history, referrer headers, a link
# pasted into a chat — and it only has to outlast one interview. The
# recruiter's lives in a dashboard and is needed later, when somebody reads the
# report. One shared number gave the MORE exposed token the LONGER life.
#
# Neither can be short-and-refreshable: /setup resets the session, so re-minting
# would destroy the interview. Hence a generous floor plus real revocation
# below, rather than a tight expiry with no way back in.
TTL = {
    CANDIDATE: 2 * 60 * 60,
    RECRUITER: 12 * 60 * 60,
}
DEFAULT_TTL = TTL[RECRUITER]

# The signing key doubles as the operator key, so a guessable one undoes
# everything above: an attacker with one token can brute-force it offline,
# with no requests to us and no rate limit in the way, then mint any token for
# any session and role. 32 bytes from `secrets.token_urlsafe` is the intent.
MIN_SECRET_LENGTH = 24


class AuthError(Exception):
    """Credentials are missing, wrong, or not for this session.

    One exception for every failure. The caller is told nothing beyond "no" —
    distinguishing "expired" from "wrong session" from "forged" tells an
    attacker which part of their guess was right.
    """


def _secret() -> bytes:
    """Read at call time, not import time, so importing this module never
    fails without a .env and a test can set its own."""
    secret = config.GATEWAY_SHARED_SECRET
    if not secret:
        raise AuthError("GATEWAY_SHARED_SECRET is not set")
    return secret.encode()


def secret_is_weak() -> bool:
    """Is the signing key short enough to brute-force offline?

    Surfaced rather than enforced: refusing to start would be worse than the
    risk during a hackathon, but nobody should be able to say they were not
    told.
    """
    return len(config.GATEWAY_SHARED_SECRET) < MIN_SECRET_LENGTH


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(
    session_id: str, role: str, epoch: int = 0, ttl: int | None = None
) -> str:
    """A token good for one session, one role, and one epoch.

    The session id goes inside the signed payload rather than being compared
    against the URL later, so a valid token cannot be replayed against another
    interview by editing the path.

    `epoch` is the session's `token_epoch` at minting time. Raising that number
    invalidates every token issued before it, which is revocation without
    storing a single token — the check is still one hash and still survives a
    gateway restart.
    """
    if role not in SESSION_ROLES:
        raise AuthError(f"unknown role {role!r}")
    if "|" in session_id:
        # The payload is pipe-delimited; a session id containing one could
        # shift the later fields along and mint a candidate token that
        # verifies as a recruiter.
        raise AuthError("session id may not contain '|'")

    expires_at = int(time.time()) + (TTL[role] if ttl is None else ttl)
    # Leading "s" tags this as a SESSION token. User tokens carry "u". Without
    # the tag a user token and a session token are both four signed fields and
    # either could be read as the other — a token-confusion bug, and a nasty
    # one, because both signatures are genuinely valid.
    payload = f"s|{session_id}|{role}|{int(epoch)}|{expires_at}".encode()
    signature = hmac.new(_secret(), payload, sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify(token: str, session_id: str, epoch: int = 0) -> str:
    """The role this token grants on this session, or raise.

    Fails closed on everything: bad signature, expired, wrong session, revoked
    by an epoch bump, or anything that does not parse.
    """
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _unb64(encoded_payload)
        signature = _unb64(encoded_signature)
    except Exception:  # noqa: BLE001 — any malformed input is simply a denial
        raise AuthError("malformed token")

    # FIRST. Everything below reads fields out of `payload`, and until this
    # passes those bytes are attacker-controlled.
    expected = hmac.new(_secret(), payload, sha256).digest()
    if not hmac.compare_digest(signature, expected):
        # Not `==`: that returns on the first differing byte, so how long the
        # comparison takes reveals how much of the signature was correct, one
        # byte at a time, until a forgery can be assembled.
        raise AuthError("bad signature")

    try:
        kind, token_session, role, token_epoch, expires_at = payload.decode().split("|")
        expiry, minted_epoch = int(expires_at), int(token_epoch)
    except ValueError:
        raise AuthError("malformed payload")

    if kind != "s":
        raise AuthError("not a session token")

    if minted_epoch != int(epoch):
        # Revoked: the interview was totalled, set up again, or somebody hit
        # /revoke. Every token from before the bump dies at once.
        raise AuthError("token revoked")

    if not hmac.compare_digest(token_session, session_id):
        # A genuine token aimed at somebody else's interview. Logged because
        # it is never an accident.
        log.warning(
            "token for session %r presented on session %r", token_session, session_id
        )
        raise AuthError("token is for a different session")

    if expiry < time.time():
        raise AuthError("token expired")

    if role not in SESSION_ROLES:
        raise AuthError("unknown role in token")

    return role


# --- user tokens --------------------------------------------------------
# What a person gets when they log in. A SESSION token says "this interview";
# a USER token says "this person" — so it carries no session and outlives any
# one interview, and what it may reach is decided by looking up who owns the
# interview rather than by anything inside the token.

USER_TTL = 12 * 60 * 60


def mint_user(user_id: int, role: str, ttl: int = USER_TTL) -> str:
    """A signed proof of who someone is, issued at login."""
    expires_at = int(time.time()) + ttl
    payload = f"u|{int(user_id)}|{role}|{expires_at}".encode()
    signature = hmac.new(_secret(), payload, sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_user(token: str) -> tuple[int, str]:
    """(user_id, role) from a user token, or raise.

    Deliberately does NOT say what the user may access. That depends on who
    owns the interview being asked for, which is a database question — putting
    it in the token would mean re-issuing one every time an assignment changes.
    """
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _unb64(encoded_payload)
        signature = _unb64(encoded_signature)
    except Exception:  # noqa: BLE001
        raise AuthError("malformed token")

    expected = hmac.new(_secret(), payload, sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise AuthError("bad signature")

    try:
        kind, user_id, role, expires_at = payload.decode().split("|")
    except ValueError:
        raise AuthError("malformed payload")

    if kind != "u":
        # A session token presented where a user token belongs. Both are
        # correctly signed, so only the tag tells them apart.
        raise AuthError("not a user token")
    if int(expires_at) < time.time():
        raise AuthError("token expired")

    return int(user_id), role


def caller_role(authorization: str, session_id: str, epoch: int = 0) -> str:
    """Identify the caller from an Authorization header.

    Returns OPERATOR, RECRUITER or CANDIDATE, or raises AuthError. The
    operator key is checked first: it is what our own scripts and Agora send,
    and it is never a session token.
    """
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not presented:
        raise AuthError("no credentials")

    if config.GATEWAY_SHARED_SECRET and hmac.compare_digest(
        presented, config.GATEWAY_SHARED_SECRET
    ):
        return OPERATOR

    return verify(presented, session_id, epoch)


def tokens_for(session_id: str, epoch: int = 0) -> dict:
    """The pair handed out when an interview is prepared.

    Two tokens rather than one, because the candidate is the person being
    assessed. A candidate who could read /state would see "turn 6: 2.0/10,
    flagged vague" and simply answer again — the assessment would stop
    measuring the candidate and start measuring who read the API.

    Both carry the session's current epoch, so both die together when it is
    raised.
    """
    return {
        "candidate_token": mint(session_id, CANDIDATE, epoch),
        "recruiter_token": mint(session_id, RECRUITER, epoch),
        "candidate_expires_in": TTL[CANDIDATE],
        "recruiter_expires_in": TTL[RECRUITER],
    }
