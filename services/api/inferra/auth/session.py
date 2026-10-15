"""Signed-cookie sessions and workspace access checks."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.config import get_settings
from inferra.db.models import Membership, User, Workspace
from inferra.db.session import get_db

COOKIE = "inferra_session"
MAX_AGE = 60 * 60 * 24 * 14
ROLE_RANK = {"viewer": 0, "editor": 1, "owner": 2}


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="inferra-session")


def sign(value: dict[str, str]) -> str:
    return _serializer().dumps(value)


def unsign(token: str, max_age: int = MAX_AGE) -> dict[str, str] | None:
    try:
        return _serializer().loads(token, max_age=max_age)
    except BadSignature:
        return None


def set_session(response: Response, user: User) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE,
        sign({"uid": user.id}),
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=s.secure_cookies,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    data = unsign(token) if token else None
    user = db.get(User, data["uid"]) if data else None
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


@dataclass
class WorkspaceAccess:
    user: User
    workspace: Workspace
    role: str

    def require(self, role: str) -> None:
        if ROLE_RANK[self.role] < ROLE_RANK[role]:
            if self.workspace.is_demo:
                raise HTTPException(403, "The demo workspace is read-only. Sign in to use your own.")
            raise HTTPException(403, f"This action needs the {role} role.")


def workspace_access(
    workspace_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> WorkspaceAccess:
    membership = db.scalars(
        select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id)
    ).first()
    if membership is None:
        raise HTTPException(404, "Workspace not found.")
    return WorkspaceAccess(user=user, workspace=membership.workspace, role=membership.role)
