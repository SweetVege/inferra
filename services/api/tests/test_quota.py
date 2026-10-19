"""Run limits: an optional allowlist of logins and a daily cap per person, schedules included."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from inferra.llm.demo_script import demo_script
from inferra.runtime.queue import drain
from tests.test_monitoring import monitor_run
from tests.test_runs_e2e import login, upload, use_llm


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


def configure(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    from inferra.config import get_settings

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def start(client: TestClient, ws: str, goal: str = "Overview of my orders"):  # type: ignore[no-untyped-def]
    return client.post(f"/api/workspaces/{ws}/runs", json={"goal": goal})


def test_no_limits_by_default(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    upload(client, ws, orders_csv)
    use_llm(demo_script)
    assert client.get("/api/auth/quota").json() == {
        "allowed": True,
        "limit": None,
        "used": 0,
        "remaining": None,
        "reason": None,
    }
    assert all(start(client, ws).status_code == 201 for _ in range(4))


def test_allowlist_limits_runs_to_invited_logins(
    client: TestClient, orders_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, INFERRA_RUN_ALLOWLIST="Grace, linus")
    use_llm(demo_script)

    ws = login(client, "ada")
    upload(client, ws, orders_csv)
    quota = client.get("/api/auth/quota").json()
    assert quota["allowed"] is False and "invited" in quota["reason"]
    res = start(client, ws)
    assert res.status_code == 403 and "invited" in res.json()["detail"]
    # Everything that spends no model budget still works.
    assert client.get(f"/api/workspaces/{ws}/datasets").json()

    ws = login(client, "grace")  # matched case-insensitively
    upload(client, ws, orders_csv)
    assert client.get("/api/auth/quota").json()["allowed"] is True
    assert start(client, ws).status_code == 201


def test_daily_limit_is_per_person(
    client: TestClient, orders_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, INFERRA_DAILY_RUN_LIMIT="2")
    use_llm(demo_script)

    ws = login(client, "ada")
    upload(client, ws, orders_csv)
    assert start(client, ws).status_code == 201
    assert client.get("/api/auth/quota").json()["remaining"] == 1
    assert start(client, ws).status_code == 201
    res = start(client, ws)
    assert res.status_code == 429 and "today's 2 agent runs" in res.json()["detail"]
    assert client.get("/api/auth/quota").json() == {
        "allowed": False,
        "limit": 2,
        "used": 2,
        "remaining": 0,
        "reason": res.json()["detail"],
    }

    other = login(client, "bob")
    upload(client, other, orders_csv)
    assert start(client, other).status_code == 201

    # Yesterday's runs do not count.
    from inferra.db.models import Run
    from inferra.db.session import session_scope

    with session_scope() as db:
        for run in db.query(Run).all():
            run.created_at = datetime.now(UTC) - timedelta(days=1)
    login(client, "ada")
    assert start(client, ws).status_code == 201


def test_schedules_count_against_their_owner(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from inferra.db.session import session_scope
    from inferra.runtime.scheduler import fire_due

    configure(monkeypatch, INFERRA_DAILY_RUN_LIMIT="1")
    ws, run = monitor_run(client)  # uses ada's one run for today
    schedule = run["schedules"][0]

    res = client.post(f"/api/workspaces/{ws}/schedules/{schedule['id']}/run")
    assert res.status_code == 429

    with session_scope() as db:
        assert fire_due(db, datetime.now(UTC) + timedelta(days=8)) == [schedule["id"]]
    drain()
    history = client.get(f"/api/workspaces/{ws}/schedules").json()[0]["recent_runs"]
    assert [r["status"] for r in history] == ["failed"]
    skipped = client.get(f"/api/workspaces/{ws}/runs/{history[0]['id']}").json()
    assert "today's 1 agent run" in skipped["error"] and skipped["steps"] == []


def test_uninvited_users_cannot_schedule(
    client: TestClient, orders_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = login(client, "ada")
    upload(client, ws, orders_csv)
    use_llm(demo_script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = start(client, ws).json()
    drain()

    configure(monkeypatch, INFERRA_RUN_ALLOWLIST="grace")
    res = client.post(f"/api/workspaces/{ws}/schedules", json={"run_id": run["id"]})
    assert res.status_code == 403 and "invited" in res.json()["detail"]


def test_unlimited_logins_skip_the_allowlist_and_the_daily_limit(
    client: TestClient, orders_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(
        monkeypatch,
        INFERRA_DAILY_RUN_LIMIT="1",
        INFERRA_RUN_ALLOWLIST="grace",
        INFERRA_RUN_UNLIMITED="Owner",
    )
    use_llm(demo_script)

    ws = login(client, "owner")  # matched case-insensitively
    upload(client, ws, orders_csv)
    assert all(start(client, ws).status_code == 201 for _ in range(3))
    quota = client.get("/api/auth/quota").json()
    assert quota["allowed"] is True and quota["limit"] is None and quota["remaining"] is None

    ws = login(client, "grace")  # everyone else keeps the limit
    upload(client, ws, orders_csv)
    assert start(client, ws).status_code == 201
    assert start(client, ws).status_code == 429
