"""Monitoring: the monitor workflow, schedules, scheduled re-runs, and alerts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from inferra.llm.demo_script import demo_script
from inferra.runtime.queue import drain
from tests.test_runs_e2e import login, upload, use_llm

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


def monitor_run(client: TestClient) -> tuple[str, dict[str, Any]]:
    ws = login(client)
    dataset = upload(client, ws, SAMPLES / "daily_funnel.csv")
    client.post(
        f"/api/workspaces/{ws}/metrics",
        json={
            "dataset_id": dataset["id"],
            "name": "conversion_rate",
            "label": "Signup conversion rate",
            "kind": "ratio",
            "numerator": {"kind": "sum", "column": "signups"},
            "denominator": {"kind": "sum", "column": "sessions"},
            "time_column": "date",
            "format": "percent",
        },
    ).raise_for_status()
    use_llm(demo_script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "Alert me when conversion drops"}).json()
    drain()
    return ws, client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()


def test_monitor_run_alerts_and_schedules_itself(client: TestClient) -> None:
    ws, run = monitor_run(client)
    assert run["status"] == "completed", run["error"]
    assert run["intent"] == "monitor"
    check = next(a for a in run["artifacts"] if a["kind"] == "metric_check")["data"]
    # The shift began inside the baseline window; the median baseline still sees it.
    assert check["status"] == "alert", check["reason"]
    assert check["relative_change"] < -0.1 and check["latest"]["period"] == "2026-10-05"
    assert run["insights"] and all(i["verified"] for i in run["insights"]), [
        i["checks"] for i in run["insights"]
    ]
    assert any(c["name"] == "alert_status" and c["passed"] for c in run["insights"][0]["checks"])

    assert len(run["alerts"]) == 1 and run["alerts"][0]["status"] == "open"
    assert run["alerts"][0]["schedule_id"] == run["schedules"][0]["id"]
    assert run["schedules"] and run["schedules"][0]["description"] == "Every Monday at 08:00 UTC"
    overview = client.get(f"/api/workspaces/{ws}/overview").json()
    assert overview["open_alerts"] == 1 and overview["schedules"] == 1

    alert = run["alerts"][0]
    res = client.post(f"/api/workspaces/{ws}/alerts/{alert['id']}/acknowledge")
    assert res.json()["status"] == "acknowledged"
    assert client.get(f"/api/workspaces/{ws}/alerts?status=open").json() == []


def test_due_schedules_re_run_the_approved_plan(client: TestClient) -> None:
    from inferra.db.session import session_scope
    from inferra.runtime.scheduler import fire_due

    ws, run = monitor_run(client)
    schedule = run["schedules"][0]
    planning_calls = sum(1 for r in use_llm(demo_script).requests if r.role == "planning")

    with session_scope() as db:
        assert fire_due(db) == []  # not due yet
        later = datetime.now(UTC) + timedelta(days=8)
        assert fire_due(db, later) == [schedule["id"]]
        assert fire_due(db, later) == []  # claimed: next_run_at moved past `later`
    drain()

    schedules = client.get(f"/api/workspaces/{ws}/schedules").json()
    assert len(schedules) == 1 and len(schedules[0]["recent_runs"]) == 1
    rerun = client.get(f"/api/workspaces/{ws}/runs/{schedules[0]['recent_runs'][0]['id']}").json()
    assert rerun["status"] == "completed", rerun["error"]
    assert rerun["schedule_id"] == schedule["id"] and rerun["plan"]["steps"] == run["plan"]["steps"]
    assert rerun["schedules"] == []  # a scheduled run never schedules itself again
    assert len(rerun["alerts"]) == 1 and rerun["alerts"][0]["schedule_id"] == schedule["id"]
    assert planning_calls == 0  # the plan is reused, not re-planned
    assert any("approved when the schedule was created" in e["message"] for e in rerun["events"])
    assert schedules[0]["open_alerts"] == 2  # the first run's alert and the re-run's


def test_schedule_api(client: TestClient) -> None:
    ws, run = monitor_run(client)
    schedule = run["schedules"][0]

    assert client.post(f"/api/workspaces/{ws}/schedules", json={"run_id": run["id"]}).status_code == 409
    res = client.patch(
        f"/api/workspaces/{ws}/schedules/{schedule['id']}",
        json={"cadence": "daily", "hour": 6, "enabled": False},
    ).json()
    assert res["description"] == "Every day at 06:00 UTC" and res["enabled"] is False

    from inferra.db.session import session_scope
    from inferra.runtime.scheduler import fire_due

    with session_scope() as db:
        assert fire_due(db, datetime.now(UTC) + timedelta(days=30)) == []  # paused

    res = client.post(f"/api/workspaces/{ws}/schedules/{schedule['id']}/run").json()
    drain()
    runs = client.get(f"/api/workspaces/{ws}/runs").json()
    assert sum(1 for r in runs if r["schedule_id"] == schedule["id"]) == 1

    assert client.delete(f"/api/workspaces/{ws}/schedules/{schedule['id']}").status_code == 204
    assert client.get(f"/api/workspaces/{ws}/schedules").json() == []
    assert all(r["schedule_id"] is None for r in client.get(f"/api/workspaces/{ws}/runs").json())


def test_any_completed_run_can_be_scheduled(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    upload(client, ws, orders_csv)
    use_llm(demo_script)
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "Overview of my orders"}).json()
    drain()
    res = client.post(f"/api/workspaces/{ws}/schedules", json={"run_id": run["id"]})
    assert res.status_code == 409  # still awaiting approval
    client.post(f"/api/workspaces/{ws}/runs/{run['id']}/approve")
    drain()
    res = client.post(
        f"/api/workspaces/{ws}/schedules", json={"run_id": run["id"], "cadence": "weekly", "weekday": 4}
    )
    assert res.status_code == 201 and res.json()["description"] == "Every Friday at 08:00 UTC"
    assert datetime.fromisoformat(res.json()["next_run_at"]).weekday() == 4


def test_next_run_times() -> None:
    from inferra.runtime.scheduler import next_run

    wed = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    assert next_run("daily", 0, 8, wed) == datetime(2026, 10, 15, 8, tzinfo=UTC)
    assert next_run("daily", 0, 10, wed) == datetime(2026, 10, 14, 10, tzinfo=UTC)
    assert next_run("weekly", 0, 8, wed) == datetime(2026, 10, 19, 8, tzinfo=UTC)
    assert next_run("weekly", 2, 10, wed) == datetime(2026, 10, 14, 10, tzinfo=UTC)
    assert next_run("weekly", 2, 9, wed) == datetime(2026, 10, 21, 9, tzinfo=UTC)
    assert next_run("weekly", 2, 9, wed.replace(tzinfo=None)) == datetime(2026, 10, 21, 9, tzinfo=UTC)


def test_check_metric_skips_partial_periods_and_respects_direction(tmp_path: Path) -> None:
    import pandas as pd

    from inferra.tools.base import get_tool
    from tests.test_metric_tools import ctx_for

    days = pd.date_range("2025-12-01", "2026-03-04", freq="D")  # ends on a Wednesday
    sessions = [100.0] * len(days)
    sessions[-10:-3] = [70.0] * 7  # the last complete week dropped 30%
    ctx = ctx_for(pd.DataFrame({"date": days.date, "sessions": sessions, "signups": 1}), tmp_path)
    check = get_tool("check_metric")

    down = check.run(ctx, {"metric": "sessions", "grain": "week", "direction": "down"}).artifact
    assert down is not None
    assert down.data["excluded_partial_period"] == "2026-03-02"
    assert down.data["latest"]["period"] == "2026-02-23" and down.data["status"] == "alert"
    assert down.data["relative_change"] == pytest.approx(-0.3)
    up = check.run(ctx, {"metric": "sessions", "grain": "week", "direction": "up"}).artifact
    assert up is not None and up.data["status"] == "ok"
    loose = check.run(ctx, {"metric": "sessions", "grain": "week", "threshold": 0.5}).artifact
    assert loose is not None and loose.data["status"] == "ok"


def test_check_metric_uses_the_latest_week_of_weekly_stamped_data(tmp_path: Path) -> None:
    import pandas as pd

    from inferra.tools.base import get_tool
    from tests.test_metric_tools import ctx_for

    fridays = pd.date_range("2025-10-03", periods=20, freq="W-FRI")  # one row per week, dated Friday
    sessions = [100.0] * 19 + [60.0]  # the latest, complete week dropped
    ctx = ctx_for(pd.DataFrame({"date": fridays.date, "sessions": sessions, "signups": 1}), tmp_path)
    check = get_tool("check_metric").run(ctx, {"metric": "sessions", "grain": "week", "direction": "down"})
    data = check.artifact.data  # type: ignore[union-attr]
    assert data["excluded_partial_period"] is None
    assert data["latest"]["value"] == pytest.approx(60.0) and data["status"] == "alert"
