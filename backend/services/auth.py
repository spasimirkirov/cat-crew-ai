"""Users, sessions and invites for remote access (``python -m backend --host 0.0.0.0``).

On loopback CatCrewAI stays a single-person desktop app: no login, everyone is the local admin.
Bound to a network address it requires a login: the first account ever registered becomes the
admin, and after that accounts are created only from a one-time invite an admin hands out.

* Passwords: ``hashlib.scrypt`` (n=2**14, r=8, p=1, 16-byte salt), compared in constant time.
* Sessions: a random token in an HttpOnly cookie; only its SHA-256 is stored, so a leaked
  database does not hand out live sessions. Sliding expiry of ``SESSION_DAYS``.
* Invites: one-time tokens (stored hashed), valid for ``INVITE_HOURS``.
* Login throttling: ``MAX_FAILURES`` failed logins per username or address in ``FAILURE_WINDOW``
  seconds lock that key until the window passes.

Storage is its own SQLite file (``data/platform/auth.db``), additive schema only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import BASE_DIR

AUTH_DB = BASE_DIR / "data" / "platform" / "auth.db"
SESSION_DAYS = 30
INVITE_HOURS = 72
MAX_FAILURES = 5
FAILURE_WINDOW = 300
ROLES = ("admin", "user")
_USERNAME = re.compile(r"[A-Za-z0-9_.-]{3,32}")
_MIN_PASSWORD = 10
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}

_lock = threading.RLock()
_failures: dict[str, list[float]] = {}
# The one-time code the first-admin registration needs (printed in the server console), so whoever
# reaches a fresh public server first cannot make themselves its admin.
_setup_code = ""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at REAL NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS invites (
    token_hash TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'user',
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_by TEXT NOT NULL DEFAULT '',
    used_at REAL NOT NULL DEFAULT 0
);
"""


class AuthError(ValueError):
    """A refusal the person may see (bad input, taken name, expired invite...)."""


@dataclass(frozen=True)
class User:
    id: str
    username: str
    role: str
    created_at: float = 0.0

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "role": self.role, "created_at": self.created_at}


# The person at the keyboard when CatCrewAI runs on loopback: no login, full rights.
LOCAL_ADMIN = User(id="local", username="local", role="admin")


def _connect() -> sqlite3.Connection:
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AUTH_DB), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(digest) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


_DUMMY_HASH = hash_password(secrets.token_hex(8))


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(id=row["id"], username=row["username"], role=row["role"], created_at=row["created_at"])


def _validate(username: str, password: str) -> tuple[str, str]:
    name = (username or "").strip()
    if not _USERNAME.fullmatch(name):
        raise AuthError("The username must be 3-32 characters: letters, digits, dot, dash or underscore.")
    if len(password or "") < _MIN_PASSWORD:
        raise AuthError(f"The password must be at least {_MIN_PASSWORD} characters.")
    return name, password


def user_count() -> int:
    with _lock, _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def needs_setup() -> bool:
    """No account yet: the next registration creates the admin."""
    return user_count() == 0


def _insert_user(conn: sqlite3.Connection, username: str, password: str, role: str) -> User:
    user = User(id=f"u_{uuid.uuid4().hex[:12]}", username=username, role=role, created_at=time.time())
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.username, hash_password(password), role, user.created_at),
        )
    except sqlite3.IntegrityError as exc:
        raise AuthError("That username is taken.") from exc
    return user


def issue_setup_code() -> str:
    """A fresh code for the first-admin registration (shown only in the server's console)."""
    global _setup_code
    with _lock:
        _setup_code = secrets.token_hex(4).upper()
        return _setup_code


def register_first_admin(username: str, password: str, setup_code: str = "") -> User:
    """Create the admin, only while there is no account at all (atomic against a race), and only
    with the setup code from the server console."""
    global _setup_code
    with _lock:
        expected = _setup_code
    if not expected or not hmac.compare_digest((setup_code or "").strip().upper(), expected):
        raise AuthError("Wrong setup code: it is printed in the server's console.")
    name, password = _validate(username, password)
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise AuthError("Registration is closed: ask an admin for an invite.")
            user = _insert_user(conn, name, password, "admin")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    with _lock:
        _setup_code = ""  # used: no second admin through this door
    return user


def create_invite(created_by: str, role: str = "user", hours: float = INVITE_HOURS) -> str:
    if role not in ROLES:
        raise AuthError(f"Unknown role {role!r}.")
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO invites (token_hash, role, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (_sha(token), role, created_by, now, now + hours * 3600),
        )
    return token


def invite_info(token: str) -> dict[str, Any] | None:
    """``{"role", "expires_at"}`` for a usable invite, else ``None``."""
    with _lock, _connect() as conn:
        row = conn.execute("SELECT role, expires_at, used_by FROM invites WHERE token_hash = ?", (_sha(token or ""),)).fetchone()
    if row is None or row["used_by"] or row["expires_at"] < time.time():
        return None
    return {"role": row["role"], "expires_at": row["expires_at"]}


def redeem_invite(token: str, username: str, password: str) -> User:
    name, password = _validate(username, password)
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT role, expires_at, used_by FROM invites WHERE token_hash = ?", (_sha(token or ""),)).fetchone()
            if row is None or row["used_by"] or row["expires_at"] < time.time():
                raise AuthError("This invite is not valid any more: ask an admin for a new one.")
            user = _insert_user(conn, name, password, row["role"])
            conn.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE token_hash = ?", (user.id, time.time(), _sha(token)))
            conn.execute("COMMIT")
            return user
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def list_invites() -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT role, created_by, created_at, expires_at, used_by, used_at FROM invites ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]


def _throttle_keys(username: str, address: str) -> list[str]:
    return [f"u:{(username or '').strip().lower()}", f"a:{address or ''}"]


def login_locked(username: str, address: str = "") -> bool:
    now = time.time()
    with _lock:
        for key in _throttle_keys(username, address):
            recent = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW]
            _failures[key] = recent
            if len(recent) >= MAX_FAILURES:
                return True
    return False


def authenticate(username: str, password: str, address: str = "") -> User | None:
    """The user for a correct username and password, else ``None`` (also while throttled)."""
    if login_locked(username, address):
        return None
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ? AND disabled = 0", ((username or "").strip(),)).fetchone()
    stored = row["password_hash"] if row is not None else _DUMMY_HASH  # same work for unknown names
    ok = verify_password(password or "", stored) and row is not None
    if not ok:
        with _lock:
            for key in _throttle_keys(username, address):
                _failures.setdefault(key, []).append(time.time())
        return None
    return _user(row)


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_sha(token), user_id, now, now + SESSION_DAYS * 86400),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token


def session_user(token: str | None) -> User | None:
    """The signed-in user for a session token (refreshing its expiry), else ``None``."""
    if not token:
        return None
    now = time.time()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT u.*, s.expires_at AS s_exp FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND u.disabled = 0",
            (_sha(token),),
        ).fetchone()
        if row is None or row["s_exp"] < now:
            return None
        if row["s_exp"] - now < (SESSION_DAYS - 1) * 86400:  # slide at most once a day
            conn.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (now + SESSION_DAYS * 86400, _sha(token)))
    return _user(row)


def revoke_session(token: str | None) -> None:
    if token:
        with _lock, _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(token),))


def list_users() -> list[User]:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT * FROM users WHERE disabled = 0 ORDER BY created_at").fetchall()
    return [u for u in (_user(r) for r in rows) if u is not None]


def get_user(user_id: str) -> User | None:
    with _lock, _connect() as conn:
        return _user(conn.execute("SELECT * FROM users WHERE id = ? AND disabled = 0", (user_id,)).fetchone())


def _admins(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0").fetchone()[0])


def set_role(user_id: str, role: str) -> User:
    if role not in ROLES:
        raise AuthError(f"Unknown role {role!r}.")
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ? AND disabled = 0", (user_id,)).fetchone()
        if row is None:
            raise AuthError("Unknown user.")
        if row["role"] == "admin" and role != "admin" and _admins(conn) <= 1:
            raise AuthError("There must always be at least one admin.")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    user = get_user(user_id)
    assert user is not None
    return user


def disable_user(user_id: str) -> None:
    """Remove an account's access (its sessions end; its files stay for the admin to handle)."""
    with _lock, _connect() as conn:
        row = conn.execute("SELECT role FROM users WHERE id = ? AND disabled = 0", (user_id,)).fetchone()
        if row is None:
            raise AuthError("Unknown user.")
        if row["role"] == "admin" and _admins(conn) <= 1:
            raise AuthError("There must always be at least one admin.")
        conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def change_password(user_id: str, old: str, new: str) -> None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ? AND disabled = 0", (user_id,)).fetchone()
        if row is None or not verify_password(old or "", row["password_hash"]):
            raise AuthError("The current password is wrong.")
        _validate(row["username"], new)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def reset_throttle() -> None:
    with _lock:
        _failures.clear()


def auth_db_path() -> Path:
    return AUTH_DB
