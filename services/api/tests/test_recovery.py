"""Interrupted runs are failed with a clear message instead of spinning forever, and can be retried."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from inferra.llm.demo_script import demo_script
from inferra.runtime.queue import drain
from tests.test_runs_e2e import login, upload, use_llm


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


def approved_run(client: TestClient, orders_csv: Path) -> tuple[str, str]:
    ws = login(client)
    upload(client, ws, orders_csv)
    use_llm(demo_script)
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "Overview of my orders"}).json()
    drain()  # planned, awaiting approval
    client.post(f"/api/workspaces/{ws}/runs/{run['id']}/approve").raise_for_status()
    return ws, run["id"]


def test_interrupted_runs_are_failed_and_can_be_retried(client: TestClient, orders_csv: Path) -> None:
    from inferra.db.models import Job
    from inferra.db.session import session_scope
    from inferra.runtime.executor import execute_run
    from inferra.runtime.recovery import INTERRUPTED, recover_stalled

    ws, run_id = approved_run(client, orders_csv)
    later = datetime.now(UTC) + timedelta(hours=1)
    with session_scope() as db:
        assert recover_stalled(db) == []  # recent
        assert recover_stalled(db, later) == []  # old, but its job is still waiting in the queue
        # A worker claimed the job and then died.
        job = db.query(Job).filter(Job.status == "queued").one()
        job.status, job.locked_at = "running", datetime.now(UTC) - timedelta(hours=2)
        db.commit()
        assert recover_stalled(db, later) == [run_id]
        assert db.get(Job, job.id).status == "failed"

    run = client.get(f"/api/workspaces/{ws}/runs/{run_id}").json()
    assert run["status"] == "failed" and run["error"] == INTERRUPTED
    assert {s["status"] for s in run["steps"]} == {"skipped"}

    # A late worker for the same run stops instead of overwriting the outcome.
    with session_scope() as db:
        execute_run(db, {"run_id": run_id})
    assert client.get(f"/api/workspaces/{ws}/runs/{run_id}").json()["status"] == "failed"

    retry = client.post(f"/api/workspaces/{ws}/runs/{run_id}/retry")
    assert retry.status_code == 201
    new = retry.json()
    assert new["id"] != run_id and new["status"] == "running" and new["plan"] == run["plan"]
    drain()
    new = client.get(f"/api/workspaces/{ws}/runs/{new['id']}").json()
    assert new["status"] == "completed", new["error"]
    assert any(e["message"] == "Retry of an earlier run" for e in new["events"])


def test_retry_rules(client: TestClient, orders_csv: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from inferra.db.models import Run
    from inferra.db.session import session_scope

    ws, run_id = approved_run(client, orders_csv)
    assert client.post(f"/api/workspaces/{ws}/runs/{run_id}/retry").status_code == 409  # not failed

    # A run that failed while planning plans again.
    with session_scope() as db:
        run = db.get(Run, run_id)
        assert run is not None
        run.status, run.plan, run.error = "failed", None, "Planning failed."
        db.commit()
    retried = client.post(f"/api/workspaces/{ws}/runs/{run_id}/retry").json()
    assert retried["status"] == "planning"
    drain()
    assert client.get(f"/api/workspaces/{ws}/runs/{retried['id']}").json()["status"] == "awaiting_approval"

    # Retries count against the daily limit like any run.
    from inferra.config import get_settings

    monkeypatch.setenv("INFERRA_DAILY_RUN_LIMIT", "2")
    get_settings.cache_clear()
    assert client.post(f"/api/workspaces/{ws}/runs/{run_id}/retry").status_code == 429


def test_a_silent_model_request_times_out_with_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic
    import httpx

    from inferra.llm.gateway import AnthropicLLM, LLMUnavailable

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    llm = AnthropicLLM("claude-test")
    assert llm._client.timeout == 120 and llm._client.max_retries == 1

    def stalled(**_: object) -> None:
        raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr(llm._client.beta.messages, "stream", stalled)
    with pytest.raises(LLMUnavailable, match="did not respond within 120 seconds"):
        llm.create(role="specialist", system="", tools=[], messages=[])
