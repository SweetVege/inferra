import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from inferra.api.schemas import UserOut
from inferra.auth.accounts import demo_user, upsert_user
from inferra.auth.session import clear_session, current_user, set_session, sign, unsign
from inferra.config import get_settings
from inferra.db.models import User
from inferra.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])
STATE_COOKIE = "inferra_oauth_state"


@router.get("/providers")
def providers() -> dict[str, bool]:
    s = get_settings()
    return {"github": bool(s.github_client_id), "dev_login": s.dev_login_enabled, "demo": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    clear_session(response)
    return {"ok": True}


@router.post("/demo", response_model=UserOut)
def demo_login(response: Response, db: Session = Depends(get_db)) -> User:
    user = demo_user(db)
    set_session(response, user)
    return user


class DevLoginIn(BaseModel):
    login: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9-]+$")


@router.post("/dev-login", response_model=UserOut)
def dev_login(body: DevLoginIn, response: Response, db: Session = Depends(get_db)) -> User:
    if not get_settings().dev_login_enabled:
        raise HTTPException(404, "Not found.")
    if body.login.lower() == "demo":
        raise HTTPException(400, "Choose another name.")
    user = upsert_user(db, login=body.login.lower(), name=body.login)
    set_session(response, user)
    return user


@router.get("/github/login")
def github_login(request: Request) -> RedirectResponse:
    s = get_settings()
    if not s.github_client_id:
        raise HTTPException(404, "GitHub sign-in is not configured.")
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": s.github_client_id,
        "redirect_uri": f"{s.web_origin}/api/auth/github/callback",
        "scope": "read:user",
        "state": state,
    }
    response = RedirectResponse(f"https://github.com/login/oauth/authorize?{urlencode(params)}")
    response.set_cookie(
        STATE_COOKIE,
        sign({"state": state}),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=s.secure_cookies,
        path="/",
    )
    return response


@router.get("/github/callback")
def github_callback(
    request: Request, code: str = "", state: str = "", db: Session = Depends(get_db)
) -> RedirectResponse:
    s = get_settings()
    cookie = request.cookies.get(STATE_COOKIE)
    expected = unsign(cookie, max_age=600) if cookie else None
    if not code or not expected or not secrets.compare_digest(expected["state"], state):
        return RedirectResponse(f"{s.web_origin}/login?error=state")
    try:
        with httpx.Client(timeout=10) as client:
            token = (
                client.post(
                    "https://github.com/login/oauth/access_token",
                    data={
                        "client_id": s.github_client_id,
                        "client_secret": s.github_client_secret,
                        "code": code,
                    },
                    headers={"Accept": "application/json"},
                )
                .json()
                .get("access_token")
            )
            if not token:
                return RedirectResponse(f"{s.web_origin}/login?error=github")
            profile = client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            ).json()
    except httpx.HTTPError:
        return RedirectResponse(f"{s.web_origin}/login?error=github")
    user = upsert_user(
        db,
        login=profile["login"],
        github_id=profile["id"],
        name=profile.get("name"),
        avatar_url=profile.get("avatar_url"),
    )
    response = RedirectResponse(f"{s.web_origin}/")
    response.delete_cookie(STATE_COOKIE, path="/")
    set_session(response, user)
    return response
