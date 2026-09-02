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
    if "transcript_json" not in have:
        conn.execute("ALTER TABLE interviews ADD COLUMN transcript_json TEXT")
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
