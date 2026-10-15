from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.db.models import Membership, User, Workspace

DEMO_LOGIN = "demo"


def demo_workspace(db: Session) -> Workspace | None:
    return db.scalars(select(Workspace).where(Workspace.is_demo.is_(True))).first()


def ensure_membership(db: Session, user: User, workspace: Workspace, role: str) -> None:
    exists = db.scalars(
        select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == workspace.id)
    ).first()
    if exists is None:
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))


def upsert_user(
    db: Session,
    *,
    login: str,
    github_id: int | None = None,
    name: str | None = None,
    avatar_url: str | None = None,
) -> User:
    """Find or create a user; new users get a personal workspace and read access to the demo."""
    user = None
    if github_id is not None:
        user = db.scalars(select(User).where(User.github_id == github_id)).first()
    elif login != DEMO_LOGIN:
        # Local dev accounts are keyed by login.
        user = db.scalars(select(User).where(User.login == login)).first()
    if user is None:
        if db.scalars(select(User).where(User.login == login)).first() is not None:
            login = f"{login}-{github_id}"
        user = User(login=login, github_id=github_id, name=name, avatar_url=avatar_url)
        db.add(user)
        db.flush()
        personal = Workspace(name=f"{name or login}'s workspace")
        db.add(personal)
        db.flush()
        ensure_membership(db, user, personal, "owner")
    else:
        user.name = name or user.name
        user.avatar_url = avatar_url or user.avatar_url
    demo = demo_workspace(db)
    if demo is not None:
        ensure_membership(db, user, demo, "viewer")
    db.commit()
    return user


def demo_user(db: Session) -> User:
    user = db.scalars(select(User).where(User.login == DEMO_LOGIN)).first()
    if user is None:
        user = User(login=DEMO_LOGIN, name="Demo visitor", is_demo=True)
        db.add(user)
        db.flush()
    demo = demo_workspace(db)
    if demo is not None:
        ensure_membership(db, user, demo, "viewer")
    db.commit()
    return user
