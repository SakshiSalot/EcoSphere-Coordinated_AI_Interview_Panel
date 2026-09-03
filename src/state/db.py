"""The one thing that has to survive a restart.

Everything else in `src/state` lives in memory, and that is the right call for
an interview: a few hundred rows that exist for twenty minutes. Accounts are
different. A candidate who cannot log back in after the gateway restarts does
not have an account, they have a session.

SQLITE, and not as a hedge:

  * `sqlite3` is in the standard library — nothing to install, nothing that
    breaks on somebody else's machine, no connection string in .env.
  * One file, gitignored, that can be deleted to start clean.
  * It handles the concurrency this gateway actually has. A JSON file would
    not: the gateway is threaded, and two writes at once lose one.

THREADING. FastAPI serves requests on a thread pool and SQLite connections are
not safe to share across threads, so each thread gets its own via
threading.local. WAL mode lets readers continue while a write is in flight,
which is what stops a dashboard poll blocking on an interview being saved.
"""

import json
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from src import config

log = logging.getLogger("state.db")

DB_PATH = Path(config.ROOT) / "data" / "echosphere.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    role          TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    full_name     TEXT    NOT NULL DEFAULT '',
    created_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS interviews (
    session_id      TEXT    PRIMARY KEY,
    candidate_id    INTEGER REFERENCES users(id),
    operator_id     INTEGER REFERENCES users(id),
    job_title       TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'ready',
    score           REAL,
    assessment_json TEXT,
    decision        TEXT,
    decided_by      INTEGER REFERENCES users(id),
    decided_at      REAL,
    created_at      REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_interviews_candidate ON interviews(candidate_id);
CREATE INDEX IF NOT EXISTS ix_interviews_operator  ON interviews(operator_id);

-- An opening. One job advert, several candidates, one leaderboard.
--
-- The advert is written once here rather than pasted into every interview:
-- candidates for the same opening must be measured against the same
-- requirements, or their scores are not comparable and the ranking is
-- meaningless.
CREATE TABLE IF NOT EXISTS jobs (
    job_id         TEXT    PRIMARY KEY,
    title          TEXT    NOT NULL,
    description    TEXT    NOT NULL DEFAULT '',
    operator_id    INTEGER REFERENCES users(id),
    coding_enabled INTEGER NOT NULL DEFAULT 1,
    voice_weight   REAL    NOT NULL DEFAULT 0.7,
    closed         INTEGER NOT NULL DEFAULT 0,
    created_at     REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_jobs_operator ON jobs(operator_id);
"""

_local = threading.local()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to a database that already exists.

    Interviews used to live only in memory, so a gateway restart — or a
    candidate simply closing the tab — lost the transcript entirely and the
    operator opened an empty record. The transcript is the one artefact the
    whole product exists to produce; it cannot be the only thing not written
    down.
    """
    have = {row[1] for row in conn.execute("PRAGMA table_info(interviews)")}

    # Each column is added on its own so a database halfway through a previous
    # migration finishes rather than failing on the first one it already has.
    added = {
        # The transcript used to live only in memory, so a restart — or a
        # candidate closing the tab — lost it and the operator opened an empty
        # record. It is the artefact the whole product exists to produce.
        "transcript_json": "TEXT",

        # An interview belongs to an opening, and the two rounds are taken
        # separately: a candidate may do the conversation now and the coding
        # exercise tomorrow, so each round carries its own score and the stage
        # says where they are.
        "job_id":        "TEXT REFERENCES jobs(job_id)",
        "invite_code":   "TEXT",
        "candidate_name": "TEXT NOT NULL DEFAULT ''",
        "resume_text":   "TEXT NOT NULL DEFAULT ''",
        "stage":         "TEXT NOT NULL DEFAULT 'invited'",
        "voice_score":   "REAL",
        "coding_score":  "REAL",
        "coding_json":   "TEXT",

        # Focus and camera signals raised by the candidate's own browser. Kept
        # in its own column rather than inside assessment_json because the two
        # have different lifetimes: the assessment is written once when the
        # conversation is totalled, and the coding round — taken days later —
        # keeps adding integrity events after that. Separate columns also make
        # the point structural: nothing in here can move a mark.
        "integrity_json": "TEXT",
    }
    for column, ddl in added.items():
        if column not in have:
            conn.execute(f"ALTER TABLE interviews ADD COLUMN {column} {ddl}")

    # The candidate's own profile links. On the USER and not the interview: a
    # person's GitHub does not change between two applications, and asking them
    # to prove ownership once per interview would be a reason not to bother.
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for column, ddl in {
        "github_username":  "TEXT NOT NULL DEFAULT ''",
        "github_json":      "TEXT",
        "github_checked_at": "REAL",
        "linkedin_url":     "TEXT NOT NULL DEFAULT ''",
    }.items():
        if column not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")

    # Not part of CREATE TABLE: the column arrives by migration on an existing
    # database, and a UNIQUE constraint cannot be added by ALTER in SQLite.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_interviews_invite "
        "ON interviews(invite_code) WHERE invite_code IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_interviews_job ON interviews(job_id)"
    )
    conn.commit()


def connect() -> sqlite3.Connection:
    """This thread's connection, created on first use.

    A connection may not be shared between threads, and FastAPI hands requests
    to a pool — so one per thread rather than one per process.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Readers do not block on a writer. Without this the operator dashboard
    # polling for scores would stall every time an interview is saved.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)

    _local.conn = conn
    return conn


def query(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, args).fetchall()


def one(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    return connect().execute(sql, args).fetchone()


def write(sql: str, args: tuple = ()) -> int:
    """Run a statement and commit. Returns lastrowid where there is one."""
    conn = connect()
    cursor = conn.execute(sql, args)
    conn.commit()
    return cursor.lastrowid


# --- interviews ---------------------------------------------------------
# Users live in `users.py`; these are here because they are plain rows with no
# password handling in them.


def create_interview(
    session_id: str,
    candidate_id: int | None,
    operator_id: int | None,
    job_title: str = "",
) -> None:
    """Record an interview, or re-point an existing one at a new candidate.

    Upsert rather than insert: /setup can be called again on the same session
    id, and failing there would leave the gateway with a live interview it
    cannot record.
    """
    write(
        """
        INSERT INTO interviews (session_id, candidate_id, operator_id,
                                job_title, status, created_at)
        VALUES (?, ?, ?, ?, 'ready', ?)
        ON CONFLICT(session_id) DO UPDATE SET
            candidate_id = excluded.candidate_id,
            operator_id  = excluded.operator_id,
            job_title    = excluded.job_title,
            status       = 'ready',
            score        = NULL,
            assessment_json = NULL,
            decision     = NULL
        """,
        (session_id, candidate_id, operator_id, job_title, time.time()),
    )


def set_status(session_id: str, status: str) -> None:
    write("UPDATE interviews SET status = ? WHERE session_id = ?",
          (status, session_id))


def save_transcript(session_id: str, turns: list[dict]) -> None:
    """Persist the conversation. Called when the call ends, so an operator can
    still read and mark it after a restart."""
    write(
        "UPDATE interviews SET transcript_json = ? WHERE session_id = ?",
        (json.dumps(turns), session_id),
    )


def load_transcript(session_id: str) -> list[dict]:
    row = one("SELECT transcript_json FROM interviews WHERE session_id = ?",
              (session_id,))
    if not row or not row["transcript_json"]:
        return []
    try:
        return json.loads(row["transcript_json"])
    except json.JSONDecodeError:
        return []


def save_assessment(session_id: str, assessment: dict) -> None:
    """Store the finished marks.

    The whole assessment goes in as JSON rather than being spread across
    columns: it already carries every quote and turn number the report needs,
    and shredding it into a schema would mean a migration every time the
    marking engine learns something new.
    """
    write(
        """UPDATE interviews
              SET status = 'ended', score = ?, assessment_json = ?
            WHERE session_id = ?""",
        (assessment.get("fraction"), json.dumps(assessment), session_id),
    )


def delete_interview(session_id: str) -> bool:
    """Remove an interview and everything stored with it.

    A hard delete, deliberately. This exists to clear test runs and abandoned
    sessions, and a soft-deleted row that still shows up in a COUNT is not
    cleared. Assessments are evidence, so the endpoint refuses to delete one
    that already carries a hiring decision — see the route.
    """
    # Not via write(): that returns lastrowid, which says nothing about how
    # many rows a DELETE removed.
    conn = connect()
    cursor = conn.execute("DELETE FROM interviews WHERE session_id = ?", (session_id,))
    conn.commit()
    return cursor.rowcount > 0


def record_decision(session_id: str, decision: str, operator_id: int) -> None:
    """The human in the loop. The point of the whole system is that a person
    makes this call, so it is stored with who made it and when."""
    write(
        """UPDATE interviews
              SET decision = ?, decided_by = ?, decided_at = ?
            WHERE session_id = ?""",
        (decision, operator_id, time.time(), session_id),
    )


def interview(session_id: str) -> sqlite3.Row | None:
    return one("SELECT * FROM interviews WHERE session_id = ?", (session_id,))


def interviews_for_operator(operator_id: int) -> list[sqlite3.Row]:
    """An operator sees the interviews they set up, newest first, with the
    candidate's name joined in so the dashboard needs one query."""
    return query(
        """SELECT i.*, u.username AS candidate_username,
                  u.full_name AS candidate_name
             FROM interviews i
             LEFT JOIN users u ON u.id = i.candidate_id
            WHERE i.operator_id = ?
            ORDER BY i.created_at DESC""",
        (operator_id,),
    )


def interviews_for_candidate(candidate_id: int) -> list[sqlite3.Row]:
    """A candidate sees only their own, and never the score — that filtering
    happens in the endpoint, not here, so this stays a plain query."""
    return query(
        """SELECT * FROM interviews
            WHERE candidate_id = ?
            ORDER BY created_at DESC""",
        (candidate_id,),
    )


def row_to_dict(row: sqlite3.Row | None) -> dict:
    return dict(row) if row is not None else {}


# --- jobs, and the candidates under them --------------------------------
# An opening groups the interviews that must be comparable with each other.


def create_job(
    title: str,
    description: str,
    operator_id: int,
    coding_enabled: bool = True,
    voice_weight: float = 0.7,
) -> str:
    job_id = "job-" + secrets.token_urlsafe(9)
    write(
        "INSERT INTO jobs (job_id, title, description, operator_id, "
        "coding_enabled, voice_weight, created_at) VALUES (?,?,?,?,?,?,?)",
        (job_id, title.strip(), description.strip(), operator_id,
         1 if coding_enabled else 0, float(voice_weight), time.time()),
    )
    return job_id


def job(job_id: str) -> sqlite3.Row | None:
    return one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))


def jobs_for_operator(operator_id: int) -> list[sqlite3.Row]:
    return query(
        "SELECT j.*, "
        "  (SELECT COUNT(*) FROM interviews i WHERE i.job_id = j.job_id) "
        "     AS candidates, "
        "  (SELECT COUNT(*) FROM interviews i WHERE i.job_id = j.job_id "
        "     AND i.stage = 'complete') AS completed "
        "FROM jobs j WHERE j.operator_id = ? ORDER BY j.created_at DESC",
        (operator_id,),
    )


def close_job(job_id: str, closed: bool = True) -> None:
    write("UPDATE jobs SET closed = ? WHERE job_id = ?",
          (1 if closed else 0, job_id))


def delete_job(job_id: str) -> int:
    """Remove an opening and every interview under it. Returns how many
    interviews went with it."""
    conn = connect()
    n = conn.execute("DELETE FROM interviews WHERE job_id = ?", (job_id,)).rowcount
    conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    conn.commit()
    return n


def new_invite_code() -> str:
    """Short enough to read down a phone, long enough not to be guessed.

    Ambiguous characters are left out: a candidate reading a code aloud should
    never have to ask whether that was a zero or an O.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if one("SELECT 1 FROM interviews WHERE invite_code = ?", (code,)) is None:
            return code


def create_invited_interview(
    job_id: str,
    operator_id: int,
    candidate_name: str,
    resume_text: str,
    job_title: str,
) -> tuple[str, str]:
    """An interview waiting for its candidate. Returns (session_id, code).

    No `candidate_id` yet — the person may not have an account. They claim it
    with the code, which is what binds the interview to them.
    """
    session_id = "iv-" + secrets.token_urlsafe(9)
    code = new_invite_code()
    write(
        "INSERT INTO interviews (session_id, candidate_id, operator_id, "
        "job_id, invite_code, candidate_name, resume_text, job_title, "
        "status, stage, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (session_id, None, operator_id, job_id, code, candidate_name.strip(),
         resume_text, job_title, "ready", "invited", time.time()),
    )
    return session_id, code


def by_invite_code(code: str) -> sqlite3.Row | None:
    return one("SELECT * FROM interviews WHERE invite_code = ?",
               (code.strip().upper(),))


def claim_interview(session_id: str, candidate_id: int) -> None:
    """Bind an invited interview to the person who entered its code."""
    write("UPDATE interviews SET candidate_id = ? WHERE session_id = ?",
          (candidate_id, session_id))


def set_stage(session_id: str, stage: str) -> None:
    write("UPDATE interviews SET stage = ? WHERE session_id = ?",
          (stage, session_id))


def set_round_score(session_id: str, which: str, score: float) -> None:
    """Record one round's normalised score, 0..1. `which` is voice or coding."""
    column = {"voice": "voice_score", "coding": "coding_score"}[which]
    write(f"UPDATE interviews SET {column} = ? WHERE session_id = ?",
          (float(score), session_id))


def save_coding(session_id: str, payload: dict) -> None:
    write("UPDATE interviews SET coding_json = ? WHERE session_id = ?",
          (json.dumps(payload), session_id))


def load_coding(session_id: str) -> dict:
    row = one("SELECT coding_json FROM interviews WHERE session_id = ?",
              (session_id,))
    if not row or not row["coding_json"]:
        return {}
    try:
        return json.loads(row["coding_json"])
    except json.JSONDecodeError:
        return {}


# --- the candidate's own profile links ----------------------------------
# Stored against the person rather than the interview: a GitHub account does
# not change between two applications, and proving ownership once should count
# for every interview that person sits.


def save_profile_links(user_id: int, github_username: str,
                       linkedin_url: str) -> None:
    """Record what the candidate claims. Claiming is not verifying — the
    verification result is written separately by `save_github`, so a changed
    username cannot inherit the previous account's proof."""
    row = one("SELECT github_username FROM users WHERE id = ?", (user_id,))
    changed = row and (row["github_username"] or "") != github_username

    if changed:
        # The proof was for the OLD account. Dropping it here is the whole
        # reason this is not one UPDATE: without it, a candidate could verify
        # an account they own, then point the field at somebody else's and keep
        # the tick.
        write(
            "UPDATE users SET github_username = ?, linkedin_url = ?, "
            "github_json = NULL, github_checked_at = NULL WHERE id = ?",
            (github_username, linkedin_url, user_id),
        )
        return

    write("UPDATE users SET github_username = ?, linkedin_url = ? WHERE id = ?",
          (github_username, linkedin_url, user_id))


def save_github(user_id: int, result: dict) -> None:
    write(
        "UPDATE users SET github_json = ?, github_checked_at = ? WHERE id = ?",
        (json.dumps(result), time.time(), user_id),
    )


def profile_for(user_id: int | None) -> dict:
    """The links and the last verification, as the report renders them."""
    if user_id is None:
        return {}
    row = one(
        "SELECT full_name, github_username, github_json, github_checked_at, "
        "linkedin_url FROM users WHERE id = ?", (user_id,)
    )
    if row is None:
        return {}

    github = None
    if row["github_json"]:
        try:
            github = json.loads(row["github_json"])
        except json.JSONDecodeError:
            github = None

    return {
        "full_name": row["full_name"],
        "github_username": row["github_username"] or "",
        "github": github,
        "github_checked_at": row["github_checked_at"],
        "linkedin_url": row["linkedin_url"] or "",
    }


def interviews_for_job(job_id: str) -> list[sqlite3.Row]:
    return query(
        "SELECT * FROM interviews WHERE job_id = ? ORDER BY created_at",
        (job_id,),
    )
