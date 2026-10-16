"""Reports: only reviewed insights, frozen snapshots on publish, and revocable public links."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from inferra.llm.demo_script import demo_script
from inferra.runtime.queue import drain
from tests.test_runs_e2e import login, upload, use_llm


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


def completed_run(client: TestClient, orders_csv: Path) -> tuple[str, dict[str, Any]]:
    ws = login(client)
    upload(client, ws, orders_csv)
    use_llm(demo_script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "Overview of my orders"}).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed" and len(run["insights"]) == 3
    return ws, run


def review(client: TestClient, ws: str, insight_id: str, status: str, claim: str | None = None) -> None:
    body = {"status": status, "claim": claim}
    client.patch(f"/api/workspaces/{ws}/insights/{insight_id}", json=body).raise_for_status()


def test_reports_only_take_reviewed_insights(client: TestClient, orders_csv: Path) -> None:
    ws, run = completed_run(client, orders_csv)
    first, second, third = (i["id"] for i in run["insights"])

    res = client.post(f"/api/workspaces/{ws}/reports", json={"title": "Orders", "insight_ids": [first]})
    assert res.status_code == 400 and "accepted or edited" in res.json()["detail"]

    review(client, ws, first, "accepted")
    review(client, ws, second, "edited", "Revised claim, checked by a person.")
    report = client.post(
        f"/api/workspaces/{ws}/reports", json={"title": "Orders", "insight_ids": [second, first]}
    ).json()
    assert report["state"] == "draft" and report["published"] is None
    content = report["content"]
    assert [i["id"] for i in content["insights"]] == [second, first]
    assert content["insights"][0]["edited"] and content["insights"][0]["claim"].startswith("Revised")
    cited = {e["artifact_id"] for i in content["insights"] for e in i["evidence"]}
    assert {a["id"] for a in content["artifacts"]} == cited
    computations = content["appendix"][0]["computations"]
    assert computations and all(c["code"] for c in computations)

    bad = client.patch(f"/api/workspaces/{ws}/reports/{report['id']}", json={"insight_ids": [first, third]})
    assert bad.status_code == 400
    other = login(client, "bob")
    assert client.get(f"/api/workspaces/{ws}/reports/{report['id']}").status_code == 404
    assert other != ws


def test_publish_freezes_and_share_links_can_be_revoked(client: TestClient, orders_csv: Path) -> None:
    ws, run = completed_run(client, orders_csv)
    first, second, _ = (i["id"] for i in run["insights"])
    review(client, ws, first, "accepted")
    review(client, ws, second, "accepted")
    rid = client.post(
        f"/api/workspaces/{ws}/reports", json={"title": "Orders review", "insight_ids": [first]}
    ).json()["id"]

    assert client.post(f"/api/workspaces/{ws}/reports/{rid}/share").status_code == 409  # not published
    published = client.post(f"/api/workspaces/{ws}/reports/{rid}/publish").json()
    assert published["state"] == "published" and published["published"]["published_by"] == "ada"

    edited = client.patch(
        f"/api/workspaces/{ws}/reports/{rid}",
        json={"title": "Orders review v2", "insight_ids": [first, second]},
    ).json()
    assert edited["state"] == "changed"
    assert edited["published"]["title"] == "Orders review" and len(edited["published"]["insights"]) == 1

    token = client.post(f"/api/workspaces/{ws}/reports/{rid}/share").json()["share_token"]
    assert token and len(token) >= 24
    client.post("/api/auth/logout")
    public = client.get(f"/api/public/reports/{token}")
    assert (
        public.status_code == 200 and public.json()["title"] == "Orders review"
    )  # the snapshot, not the draft

    login(client)
    # A reviewer changes their mind: the report can no longer be published until it is fixed.
    review(client, ws, second, "rejected")
    res = client.post(f"/api/workspaces/{ws}/reports/{rid}/publish")
    assert res.status_code == 400 and "Review first" in res.json()["detail"]
    client.patch(f"/api/workspaces/{ws}/reports/{rid}", json={"insight_ids": [first]})
    republished = client.post(f"/api/workspaces/{ws}/reports/{rid}/publish").json()
    assert republished["published"]["title"] == "Orders review v2"
    assert client.get(f"/api/public/reports/{token}").json()["title"] == "Orders review v2"

    client.delete(f"/api/workspaces/{ws}/reports/{rid}/share")
    assert client.get(f"/api/public/reports/{token}").status_code == 404
    assert client.get("/api/public/reports/short").status_code == 404
    assert client.get(f"/api/workspaces/{ws}/overview").json()["published_reports"] == 1


def test_demo_report_is_shared_and_read_only(client: TestClient) -> None:
    from inferra.seed import seed_demo

    seed_demo()
    client.post("/api/auth/demo").raise_for_status()
    demo = client.get("/api/workspaces").json()[0]
    reports = client.get(f"/api/workspaces/{demo['id']}/reports").json()
    assert len(reports) == 1 and reports[0]["shared"] and reports[0]["state"] == "published"
    detail = client.get(f"/api/workspaces/{demo['id']}/reports/{reports[0]['id']}").json()
    snapshot = client.get(f"/api/public/reports/{detail['share_token']}").json()
    assert len(snapshot["insights"]) == 4 and all(i["verified"] for i in snapshot["insights"])
    assert client.post(f"/api/workspaces/{demo['id']}/reports/{reports[0]['id']}/publish").status_code == 403

    schedules = client.get(f"/api/workspaces/{demo['id']}/schedules").json()
    assert len(schedules) == 1 and schedules[0]["enabled"] is False
    alerts = client.get(f"/api/workspaces/{demo['id']}/alerts?status=open").json()
    assert len(alerts) == 1


def test_demo_rebuilds_when_outdated_and_keeps_its_link(client: TestClient) -> None:
    from inferra.db.models import Report
    from inferra.db.session import session_scope
    from inferra.seed import demo_share_token, seed_demo

    first = seed_demo()
    assert seed_demo() == first  # current: kept as is
    login(client, "grace")  # a visitor with read access to the demo
    with session_scope() as db:
        db.query(Report).delete()  # what an older release's demo looks like
    rebuilt = seed_demo()
    assert rebuilt != first

    demo = next(w for w in client.get("/api/workspaces").json() if w["is_demo"])
    assert demo["id"] == rebuilt  # grace kept her access
    snapshot = client.get(f"/api/public/reports/{demo_share_token()}")
    assert snapshot.status_code == 200 and snapshot.json()["title"] == "Weekly product review"
