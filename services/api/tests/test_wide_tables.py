"""Wide tables: prompts show a bounded, goal-relevant set of columns; tools can find the rest."""

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from inferra.agents.orchestrator import MAX_CATALOG_COLUMNS, pick_columns
from inferra.llm.demo_script import demo_script
from inferra.runtime.queue import drain
from tests.test_runs_e2e import login, upload, use_llm


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


def test_pick_columns_keeps_relevant_ones_in_table_order() -> None:
    columns = [{"name": f"feature_{i:03d}", "type": "DOUBLE"} for i in range(300)]
    columns[250] = {"name": "Churn_Label", "type": "BOOLEAN"}
    columns[5] = {"name": "tenure_months", "type": "BIGINT"}
    picked = pick_columns(columns, "Which customers will churn, and does tenure matter?")
    names = [c["name"] for c in picked]
    assert len(picked) == MAX_CATALOG_COLUMNS
    assert "Churn_Label" in names and "tenure_months" in names
    assert names == [c["name"] for c in columns if c["name"] in set(names)]  # original order
    assert pick_columns(columns[:10], "anything") == columns[:10]


def test_planning_prompt_stays_bounded_on_a_wide_table(client: TestClient, tmp_path: Path) -> None:
    frame = pd.DataFrame({f"sensor_{i:03d}": range(50) for i in range(300)})
    frame["defect"] = [i % 2 for i in range(50)]
    path = tmp_path / "wide_readings.csv"
    frame.to_csv(path, index=False)

    ws = login(client)
    dataset = upload(client, ws, path)
    assert len(dataset["columns"]) == 301
    llm = use_llm(demo_script)
    client.post(f"/api/workspaces/{ws}/runs", json={"goal": "What drives a defect?"}).raise_for_status()
    drain()

    prompt = llm.requests[0].first_user_text
    start = prompt.index("Dataset catalog (JSON):") + len("Dataset catalog (JSON):")
    catalog = json.loads(prompt[start : prompt.index("\n\nDatasets the user selected", start)])
    assert len(catalog[0]["columns"]) == MAX_CATALOG_COLUMNS
    assert any(c.startswith("defect ") for c in catalog[0]["columns"])
    assert "261 more columns" in catalog[0]["more_columns"]


def test_list_tables_filters(tmp_path: Path) -> None:
    from inferra.tools.base import ToolError, get_tool
    from tests.test_metric_tools import ctx_for

    frame = pd.DataFrame({**{f"x_{i}": [1] for i in range(200)}, "price_usd": [2.0], "unit_price": [1.0]})
    ctx = ctx_for(frame, tmp_path)
    tool = get_tool("list_tables")

    full = tool.run(ctx, {}).result["tables"][0]
    assert full["column_count"] == 202 and len(full["columns"]) == 150 and "note" in full
    found = tool.run(ctx, {"table": "t", "search": "PRICE"}).result["tables"][0]
    assert [c["name"] for c in found["columns"]] == ["price_usd", "unit_price"]
    with pytest.raises(ToolError):
        tool.run(ctx, {"table": "nope"})
