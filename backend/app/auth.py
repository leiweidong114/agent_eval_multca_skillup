from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response


COOKIE_NAME = "agent_eval_session"
EMPLOYEE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SESSION_TTL_SECONDS = 12 * 60 * 60
_EPHEMERAL_SECRET = secrets.token_bytes(32)


@dataclass(frozen=True)
class LoginIdentity:
    employee_no: str
    expires_at: int


def _secret() -> bytes:
    configured = os.environ.get("AGENT_EVAL_SESSION_SECRET", "").strip()
    return configured.encode("utf-8") if configured else _EPHEMERAL_SECRET


def validate_employee_no(value: str) -> str:
    employee_no = value.strip()
    if not EMPLOYEE_PATTERN.fullmatch(employee_no):
        raise HTTPException(
            status_code=400,
            detail="工号只能包含字母、数字、点、下划线或连字符，且长度为 1-64",
        )
    return employee_no


def _encode(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(_secret(), body, hashlib.sha256).digest()
    return body.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _decode(token: str) -> LoginIdentity | None:
    try:
        body_text, signature_text = token.split(".", 1)
        body = body_text.encode("ascii")
        padding = "=" * (-len(signature_text) % 4)
        signature = base64.urlsafe_b64decode(signature_text + padding)
        expected = hmac.new(_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        body_padding = "=" * (-len(body_text) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body_text + body_padding))
        employee_no = validate_employee_no(str(payload.get("employee_no") or ""))
        expires_at = int(payload.get("expires_at") or 0)
        if expires_at <= int(time.time()):
            return None
        return LoginIdentity(employee_no=employee_no, expires_at=expires_at)
    except (ValueError, TypeError, json.JSONDecodeError, HTTPException):
        return None


def identity_from_request(request: Request, *, required: bool = True) -> LoginIdentity | None:
    identity = _decode(request.cookies.get(COOKIE_NAME, ""))
    if identity is None and required:
        raise HTTPException(status_code=401, detail="请先登录")
    return identity


def employee_from_request(request: Request) -> str:
    identity = identity_from_request(request)
    assert identity is not None
    return identity.employee_no


def set_login_cookie(response: Response, employee_no: str) -> LoginIdentity:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    identity = LoginIdentity(employee_no=validate_employee_no(employee_no), expires_at=expires_at)
    token = _encode({"employee_no": identity.employee_no, "expires_at": expires_at})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("AGENT_EVAL_SECURE_COOKIE", "").lower() in {"1", "true", "yes"},
        path="/",
    )
    return identity


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
