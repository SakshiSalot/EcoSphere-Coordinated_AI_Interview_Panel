"""Accounts, and the one place a password is ever handled.

Two kinds of person use this system, and they are not the same kind of user:

  * A CANDIDATE signs in to take their interview. They see their own
    interviews and nothing about how they scored.
  * An OPERATOR is the human in the loop. They set interviews up, read the
    assessments with their evidence, and record the hire decision. The whole
    point of the product is that a person makes that call, not the model.

The AI interviewers are NOT users. Priya, Arjun and Meera are the panel;
Agora and our own scripts authenticate with the shared key.

PASSWORDS — `hashlib.scrypt`, from the standard library:

  * It is a key-derivation function, deliberately slow and memory-hard. A
    stolen database cannot be run through a GPU at billions of guesses a
    second, which is exactly what SHA-256 would allow — SHA-256 is built to be
    fast, and fast is the wrong property here.
  * Per-user random salt, so two people with the same password get different
    hashes and one cracked account tells you nothing about the next.
  * Constant-time comparison, so how long a login takes does not reveal how
    much of the hash was right.
  * Stdlib, so no bcrypt or argon2 dependency to install and pin.

A plaintext password exists in exactly two places in this codebase: the
argument to `create` and the argument to `authenticate`. It is never logged,
never stored, and never returned.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time

from src.state import db

log = logging.getLogger("gateway.users")

CANDIDATE = "candidate"
OPERATOR = "operator"
ROLES = (CANDIDATE, OPERATOR)

# scrypt parameters. n is the work factor: raising it makes both a legitimate
# login and an attacker's guess proportionally slower, which is the trade we
# want. 2**14 keeps a login imperceptible while making offline cracking
# expensive. r and n together set the memory cost, which is what stops the
# attack being parallelised cheaply on a GPU.
_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16
_KEY_BYTES = 32

MIN_PASSWORD = 8
_USERNAME = re.compile(r"^[a-zA-Z0-9._@-]{3,64}$")


class UserError(Exception):
    """Something the caller did wrong, and may safely be told about —
    a taken username, a password too short. NOT used for a failed login."""


def _hash(password: str, salt: bytes) -> str:
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_BYTES
    )
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def _verify(password: str, stored: str) -> bool:
    """Re-derive with the stored parameters and compare in constant time.

    The parameters live in the hash string rather than in code, so raising the
    work factor later does not lock out everyone who registered before.
    """
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(key_hex) // 2,
        )
    except Exception:  # noqa: BLE001 — a malformed hash is a failed login
        return False
    return hmac.compare_digest(key.hex(), key_hex)


def _is_duplicate(exc: Exception) -> bool:
    """Is this the database refusing a username that already exists?

    SQLite says "UNIQUE constraint failed: users.username" and libsql wraps
    the same message from the same engine, so the text is the reliable signal
    across both — the exception TYPES are what differ.
    """
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    message = str(exc).lower()
    return "unique" in message and "constraint" in message


def create(username: str, password: str, role: str, full_name: str = "") -> int:
    """Register an account. Returns the new user id."""
    username = (username or "").strip()
    if not _USERNAME.match(username):
        raise UserError(
            "Username must be 3-64 characters: letters, numbers, . _ - or @"
        )
    if role not in ROLES:
        raise UserError(f"Unknown role {role!r}")
    if len(password or "") < MIN_PASSWORD:
        raise UserError(f"Password must be at least {MIN_PASSWORD} characters")

    try:
        user_id = db.write(
            """INSERT INTO users (username, role, password_hash, full_name,
                                  created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (username, role, _hash(password, os.urandom(_SALT_BYTES)),
             full_name.strip(), time.time()),
        )
    except Exception as exc:
        # Both drivers, by BEHAVIOUR rather than by type.
        #
        # `except sqlite3.IntegrityError` was correct for exactly as long as
        # the database was a local file. libsql — the Turso driver — exports a
        # single `Error` class that is not related to sqlite3's at all, so on
        # Turso this never fired and a taken username escaped as an unhandled
        # 500. On the sign-up screen, which is the first thing anyone touches.
        #
        # Matching on the message rather than catching libsql.Error wholesale:
        # that class covers every failure the driver can have, and reporting a
        # dropped connection as "that username is taken" would send somebody
        # debugging the wrong thing entirely.
        if not _is_duplicate(exc):
            raise
        raise UserError("That username is already taken")

    log.info("created %s account %r (id %d)", role, username, user_id)
    return user_id


def authenticate(username: str, password: str) -> sqlite3.Row | None:
    """The user, or None. Never says which half was wrong.

    Two details that are easy to get wrong and matter:

    A wrong USERNAME still runs a hash. Returning immediately for an unknown
    user makes that case measurably faster than a wrong password, and timing
    the difference enumerates who has an account here — which for a hiring
    system means learning who is interviewing.

    The caller is told nothing beyond "no". "Password incorrect" confirms the
    username exists.
    """
    row = db.one("SELECT * FROM users WHERE username = ?", ((username or "").strip(),))

    if row is None:
        # Deliberate: burn the same work an existing user would, so the two
        # paths cost the same.
        _hash(password or "", b"\x00" * _SALT_BYTES)
        log.info("failed login for unknown user %r", (username or "")[:40])
        return None

    if not _verify(password or "", row["password_hash"]):
        log.info("failed login for %r", row["username"])
        return None

    return row


def by_id(user_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM users WHERE id = ?", (user_id,))


def by_username(username: str) -> sqlite3.Row | None:
    return db.one("SELECT * FROM users WHERE username = ?", ((username or "").strip(),))


def public(row: sqlite3.Row | None) -> dict:
    """A user as the browser may see them. The hash never leaves this module."""
    if row is None:
        return {}
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "full_name": row["full_name"],
    }


def count(role: str | None = None) -> int:
    if role:
        return db.one("SELECT COUNT(*) c FROM users WHERE role = ?", (role,))["c"]
    return db.one("SELECT COUNT(*) c FROM users")["c"]


def suggest_password() -> str:
    """For the seeding script — a password nobody has to invent."""
    return secrets.token_urlsafe(12)
