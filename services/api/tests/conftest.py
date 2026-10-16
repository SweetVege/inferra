import os
from collections.abc import Iterator
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("INFERRA_ENV", "test")
    # Set INFERRA_TEST_DATABASE_URL to run the suite against Postgres instead of SQLite.
    db_url = os.environ.get("INFERRA_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("INFERRA_DATABASE_URL", db_url)
    monkeypatch.setenv("INFERRA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INFERRA_INLINE_WORKER", "false")
    # Tests upload the datasets they need; signup samples are covered in test_samples.py.
    monkeypatch.setenv("INFERRA_SAMPLE_DATA_ON_SIGNUP", "false")
    monkeypatch.delenv("INFERRA_LLM_MODEL", raising=False)
    monkeypatch.delenv("INFERRA_GITHUB_CLIENT_ID", raising=False)

    from inferra.config import get_settings
    from inferra.db.models import Base
    from inferra.db.session import get_engine, reset_engine
    from inferra.llm.gateway import set_llm_factory

    get_settings.cache_clear()
    reset_engine()
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    yield
    set_llm_factory(None)
    reset_engine()
    get_settings.cache_clear()
    os.environ.pop("INFERRA_LLM_MODEL", None)


@pytest.fixture
def orders_csv() -> Path:
    return SAMPLES / "ecommerce_orders.csv"
