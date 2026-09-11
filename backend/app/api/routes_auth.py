from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.auth import clear_login_cookie, identity_from_request, set_login_cookie


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    employee_no: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict[str, object]:
    # This is deliberately a claimed-identity login. The password is accepted
    # for UI compatibility but is never validated, stored, or logged.
    identity = set_login_cookie(response, payload.employee_no)
    return {
        "employee_no": identity.employee_no,
        "expires_at": identity.expires_at,
        "auth_strength": "claimed",
    }


@router.get("/me")
def me(request: Request) -> dict[str, object]:
    identity = identity_from_request(request)
    assert identity is not None
    return {
        "employee_no": identity.employee_no,
        "expires_at": identity.expires_at,
        "auth_strength": "claimed",
    }


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    clear_login_cookie(response)
    return {"ok": True}
