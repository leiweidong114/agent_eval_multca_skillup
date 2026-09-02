from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import Database, utcnow


PBKDF2_ITERATIONS = 600_000
SESSION_HOURS = 12


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        observed = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(observed.hex(), expected_hex)
    except (TypeError, ValueError):
        return False


def ensure_bootstrap_admin(db: Database, data_dir: Path) -> dict[str, Any]:
    existing = db.row("SELECT * FROM users ORDER BY id LIMIT 1")
    if existing:
        return existing
    username = os.environ.get("PRISM_ADMIN_USERNAME", "admin").strip() or "admin"
    configured_password = os.environ.get("PRISM_ADMIN_PASSWORD")
    password = configured_password or secrets.token_urlsafe(16)
    now = utcnow()
    user_id = db.execute(
        """INSERT INTO users(username,display_name,password_hash,role,active,created_at,updated_at)
        VALUES(?,?,?,'admin',1,?,?)""",
        (username, "Platform Administrator", hash_password(password), now, now),
    )
    if not configured_password:
        credential_file = data_dir / "bootstrap-admin.txt"
        credential_file.write_text(
            f"username={username}\npassword={password}\n"
            "Delete this file after the first successful login and password rotation.\n",
            encoding="utf-8",
        )
    return db.row("SELECT * FROM users WHERE id=?", (user_id,))


def create_session(db: Database, user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    db.execute(
        "INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
        (token_hash, user_id, now.isoformat(), (now + timedelta(hours=SESSION_HOURS)).isoformat()),
    )
    return token


def session_user(db: Database, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    user = db.row(
        """SELECT u.id,u.username,u.display_name,u.role,u.active,u.created_at
        FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
        (token_hash, utcnow()),
    )
    return user


def revoke_session(db: Database, token: str | None) -> None:
    if token:
        db.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
        )


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        key: user[key]
        for key in (
            "id",
            "username",
            "display_name",
            "role",
            "active",
            "auth_source",
            "last_login_at",
            "created_at",
        )
        if key in user
    }
