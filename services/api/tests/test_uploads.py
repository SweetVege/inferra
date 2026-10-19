"""Direct uploads: the browser PUTs to object storage with a presigned URL, then the API ingests."""

from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from tests.test_runs_e2e import login

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    from inferra.config import get_settings

    for key, value in {
        "INFERRA_STORAGE_BACKEND": "s3",
        "INFERRA_S3_BUCKET": "inferra-test",
        "INFERRA_S3_REGION": "us-east-1",
        "INFERRA_S3_ACCESS_KEY_ID": "test",
        "INFERRA_S3_SECRET_ACCESS_KEY": "test",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    with mock_aws():
        bucket = boto3.client("s3", region_name="us-east-1")
        bucket.create_bucket(Bucket="inferra-test")
        yield bucket


def test_local_storage_uses_the_form_upload(client: TestClient) -> None:
    ws = login(client)
    ticket = client.post(f"/api/workspaces/{ws}/datasets/uploads", json={"filename": "a.csv", "size": 10})
    assert ticket.json() == {"mode": "form", "upload_id": None, "url": None}


def test_direct_upload_round_trip(client: TestClient, s3) -> None:  # type: ignore[no-untyped-def]
    ws = login(client)
    path = SAMPLES / "checkout_experiment.csv"
    ticket = client.post(
        f"/api/workspaces/{ws}/datasets/uploads", json={"filename": path.name, "size": path.stat().st_size}
    ).json()
    assert ticket["mode"] == "direct"
    assert f"uploads/{ws}/{ticket['upload_id']}" in ticket["url"] and "Signature" in ticket["url"]

    # What the browser's PUT to the presigned URL does.
    s3.put_object(Bucket="inferra-test", Key=f"uploads/{ws}/{ticket['upload_id']}", Body=path.read_bytes())
    res = client.post(
        f"/api/workspaces/{ws}/datasets/uploads/{ticket['upload_id']}/complete", json={"filename": path.name}
    )
    assert res.status_code == 201, res.text
    dataset = res.json()
    assert dataset["table_name"] == "checkout_experiment" and dataset["row_count"] > 1000
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="inferra-test").get("Contents", [])]
    assert f"uploads/{ws}/{ticket['upload_id']}" not in keys  # the staging object is removed
    assert f"workspaces/{ws}/{dataset['id']}.parquet" in keys
    detail = client.get(f"/api/workspaces/{ws}/datasets/{dataset['id']}").json()
    assert len(detail["preview"]) == 20


def test_direct_upload_checks(client: TestClient, s3, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    ws = login(client)
    start = f"/api/workspaces/{ws}/datasets/uploads"
    assert client.post(start, json={"filename": "notes.pdf", "size": 10}).status_code == 400
    assert client.post(start, json={"filename": "big.csv", "size": 10**12}).status_code == 413

    missing = client.post(f"{start}/abc123/complete", json={"filename": "a.csv"})
    assert missing.status_code == 404

    # An upload can only be completed from the workspace that started it.
    ticket = client.post(start, json={"filename": "a.csv", "size": 10}).json()
    s3.put_object(Bucket="inferra-test", Key=f"uploads/{ws}/{ticket['upload_id']}", Body=b"a,b\n1,2\n")
    other = login(client, "bob")
    res = client.post(
        f"/api/workspaces/{other}/datasets/uploads/{ticket['upload_id']}/complete", json={"filename": "a.csv"}
    )
    assert res.status_code == 404

    # The size limit is enforced on the stored object, not just the declared size.
    monkeypatch.setenv("INFERRA_MAX_UPLOAD_MB", "0")
    from inferra.config import get_settings

    get_settings.cache_clear()
    login(client, "ada")
    res = client.post(f"{start}/{ticket['upload_id']}/complete", json={"filename": "a.csv"})
    assert res.status_code == 413
