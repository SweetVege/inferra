"""New workspaces start with the sample datasets; existing ones can add them on request."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from inferra.runtime.queue import drain
from inferra.samples import SAMPLE_DATASETS
from tests.test_runs_e2e import login


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


@pytest.fixture
def samples_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from inferra.config import get_settings

    monkeypatch.setenv("INFERRA_SAMPLE_DATA_ON_SIGNUP", "true")
    get_settings.cache_clear()


def test_new_workspaces_start_with_samples(client: TestClient, samples_on: None) -> None:
    started = time.monotonic()
    ws = login(client, "ada")
    assert time.monotonic() - started < 5  # sign-in does not wait for the samples
    assert client.get(f"/api/workspaces/{ws}/datasets").json() == []
    drain()
    datasets = client.get(f"/api/workspaces/{ws}/datasets").json()
    assert sorted(d["original_filename"] for d in datasets) == sorted(SAMPLE_DATASETS)
    assert all(d["has_profile"] and d["description"] for d in datasets)
    metrics = client.get(f"/api/workspaces/{ws}/metrics").json()
    assert {m["name"] for m in metrics} == {"conversion_rate", "sessions", "revenue_per_session"}

    # Signing in again does not add them twice.
    client.post("/api/auth/logout")
    login(client, "ada")
    drain()
    assert len(client.get(f"/api/workspaces/{ws}/datasets").json()) == len(SAMPLE_DATASETS)


def test_samples_can_be_added_later(client: TestClient) -> None:
    ws = login(client, "ada")  # signup samples are off in tests by default
    assert client.get(f"/api/workspaces/{ws}/datasets").json() == []
    added = client.post(f"/api/workspaces/{ws}/datasets/samples")
    assert added.status_code == 201 and len(added.json()) == len(SAMPLE_DATASETS)

    funnel = next(d for d in added.json() if d["original_filename"] == "daily_funnel.csv")
    assert client.delete(f"/api/workspaces/{ws}/datasets/{funnel['id']}").status_code == 204
    again = client.post(f"/api/workspaces/{ws}/datasets/samples").json()
    assert [d["original_filename"] for d in again] == ["daily_funnel.csv"]
    assert client.post(f"/api/workspaces/{ws}/datasets/samples").json() == []


def test_demo_visitors_cannot_add_samples(client: TestClient) -> None:
    from inferra.seed import seed_demo

    demo_id = seed_demo()
    client.post("/api/auth/demo").raise_for_status()
    assert client.post(f"/api/workspaces/{demo_id}/datasets/samples").status_code == 403


def test_signup_survives_a_broken_sample(
    client: TestClient, samples_on: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("INFERRA_SAMPLES_DIR", str(tmp_path / "missing"))
    ws = login(client, "ada")
    drain()  # the job fails and is recorded as failed; sign-in and the workspace are unaffected
    assert client.get(f"/api/workspaces/{ws}/datasets").json() == []
    assert client.get("/api/auth/me").status_code == 200
