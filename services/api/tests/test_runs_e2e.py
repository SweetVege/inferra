"""End-to-end: API -> queue -> orchestrator -> specialists -> tools -> insight agent -> verifier.

Only the language model is scripted (inferra.llm.demo_script); everything else is real."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from inferra.llm.demo_script import demo_script
from inferra.llm.gateway import set_llm_factory
from inferra.llm.scripted import Request, ScriptedLLM, TextBlock, ToolUseBlock
from inferra.runtime.queue import drain


@pytest.fixture
def client() -> TestClient:
    from inferra.main import create_app

    return TestClient(create_app())


def login(client: TestClient, name: str = "ada") -> str:
    assert client.post("/api/auth/dev-login", json={"login": name}).status_code == 200
    workspaces = client.get("/api/workspaces").json()
    return next(w["id"] for w in workspaces if not w["is_demo"])


def upload(client: TestClient, ws: str, path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        res = client.post(f"/api/workspaces/{ws}/datasets", files={"file": (path.name, fh, "text/csv")})
    assert res.status_code == 201, res.text
    return res.json()


def use_llm(script) -> ScriptedLLM:  # type: ignore[no-untyped-def]
    llm = ScriptedLLM(script)
    set_llm_factory(lambda: llm)
    return llm


def test_runs_need_a_configured_model(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    upload(client, ws, orders_csv)
    res = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "What is in my data?"})
    assert res.status_code == 503


def test_explore_run_end_to_end(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    dataset = upload(client, ws, orders_csv)
    assert dataset["row_count"] == 2412 and dataset["table_name"] == "ecommerce_orders"
    llm = use_llm(demo_script)

    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "Give me an overview of my orders"}).json()
    assert run["status"] == "planning"
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "awaiting_approval"
    assert run["intent"] == "explore"
    assert [s["agent"] for s in run["steps"]] == ["data", "analyst", "insight"]

    client.post(f"/api/workspaces/{ws}/runs/{run['id']}/approve").raise_for_status()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed", run["error"]
    assert all(s["status"] == "completed" for s in run["steps"])
    assert {t["tool"] for t in run["tool_calls"]} == {"profile_dataset", "group_summary", "run_sql"}
    assert all(t["code"] for t in run["tool_calls"])
    assert len(run["insights"]) == 3
    assert all(i["verified"] for i in run["insights"]), [i["checks"] for i in run["insights"]]
    assert run["usage"]["requests"] == len(llm.requests)
    kinds = [e["kind"] for e in run["events"]]
    assert kinds[0] == "created" and kinds[-1] == "completed" and "verification" in kinds

    # Human review: accept one, edit another, reject the third.
    first, second, third = run["insights"]
    url = f"/api/workspaces/{ws}/insights"
    assert client.patch(f"{url}/{first['id']}", json={"status": "accepted"}).json()["status"] == "accepted"
    edited = client.patch(f"{url}/{second['id']}", json={"status": "edited", "claim": "Reworded."}).json()
    assert edited["claim"] == "Reworded." and edited["original_claim"] == second["claim"]
    assert client.patch(f"{url}/{third['id']}", json={"status": "rejected"}).json()["status"] == "rejected"

    runs = client.get(f"/api/workspaces/{ws}/runs").json()
    assert runs[0]["insight_count"] == 3


def test_verifier_feedback_triggers_a_revision(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    upload(client, ws, orders_csv)
    insight_turns = 0

    def script(req: Request) -> list[Any]:
        nonlocal insight_turns
        if req.role != "insight":
            return demo_script(req)
        insight_turns += 1
        blocks = demo_script(req)
        if insight_turns == 1:  # first draft invents a number
            blocks[0].input["insights"][1]["claim"] += " That is roughly 40% higher."
        return blocks

    use_llm(script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "overview"}).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed"
    assert insight_turns == 2
    assert all(i["verified"] for i in run["insights"])
    failures = [e for e in run["events"] if e["kind"] == "verification"][0]["data"]["failures"]
    assert "40%" in failures[0]


def test_persistently_ungrounded_insights_are_flagged_not_dropped(
    client: TestClient, orders_csv: Path
) -> None:
    ws = login(client)
    upload(client, ws, orders_csv)

    def script(req: Request) -> list[Any]:
        blocks = demo_script(req)
        if req.role == "insight":
            blocks[0].input["insights"][2]["claim"] += " Discounts caused this."
        return blocks

    use_llm(script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "overview"}).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    flagged = [i for i in run["insights"] if not i["verified"]]
    assert len(flagged) == 1 and flagged[0]["confidence"] == "low"
    assert any(c["name"] == "causal_language" and not c["passed"] for c in flagged[0]["checks"])


def test_clarification_round_trip(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    upload(client, ws, orders_csv)

    def script(req: Request) -> list[Any]:
        if req.role == "planning" and "Clarifications so far" not in req.first_user_text:
            return [ToolUseBlock("ask_clarification", {"question": "Which metric matters most?"})]
        return demo_script(req)

    use_llm(script)
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "How are we doing?"}).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "needs_input"
    assert run["clarifications"] == [{"question": "Which metric matters most?", "answer": None}]
    client.post(f"/api/workspaces/{ws}/runs/{run['id']}/answer", json={"answer": "Order value"})
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "awaiting_approval"


def test_roadmap_intents_are_reported_not_improvised(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    dataset = upload(client, ws, orders_csv)

    def script(req: Request) -> list[Any]:
        return [
            TextBlock("This is a data modeling request."),
            ToolUseBlock(
                "submit_plan",
                {
                    "intent": "monitor",
                    "dataset_ids": [dataset["id"]],
                    "params": [],
                    "rationale": "A recurring alert request.",
                    "confidence": "high",
                },
            ),
        ]

    use_llm(script)
    run = client.post(
        f"/api/workspaces/{ws}/runs", json={"goal": "Alert me every Monday if revenue drops"}
    ).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "unsupported" and "future release" in run["error"]


def test_invalid_plans_are_sent_back_to_the_orchestrator(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    upload(client, ws, orders_csv)
    attempts = 0

    def script(req: Request) -> list[Any]:
        nonlocal attempts
        if req.role == "planning":
            attempts += 1
            if attempts == 1:
                return [
                    ToolUseBlock(
                        "submit_plan",
                        {
                            "intent": "metric_query",
                            "dataset_ids": ["bogus"],
                            "params": [],
                            "rationale": "",
                            "confidence": "low",
                        },
                    )
                ]
        return demo_script(req)

    llm = use_llm(script)
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "overview"}).json()
    drain()
    assert attempts == 2
    feedback = llm.requests[1].last_tool_results()[0]["content"]
    assert "dataset id" in feedback and "metric" in feedback
    assert client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()["status"] == "awaiting_approval"


def test_workspaces_are_isolated_and_demo_is_read_only(client: TestClient, orders_csv: Path) -> None:
    from inferra.seed import seed_demo

    seed_demo()
    ws_ada = login(client, "ada")
    upload(client, ws_ada, orders_csv)
    client.post("/api/auth/logout")

    ws_bob = login(client, "bob")
    assert client.get(f"/api/workspaces/{ws_ada}/datasets").status_code == 404

    demo = next(w for w in client.get("/api/workspaces").json() if w["is_demo"])
    assert demo["role"] == "viewer"
    datasets = client.get(f"/api/workspaces/{demo['id']}/datasets").json()
    assert datasets and ws_bob != demo["id"]
    with orders_csv.open("rb") as fh:
        res = client.post(f"/api/workspaces/{demo['id']}/datasets", files={"file": ("x.csv", fh)})
    assert res.status_code == 403 and "read-only" in res.json()["detail"]

    client.post("/api/auth/logout")
    client.post("/api/auth/demo").raise_for_status()
    workspaces = client.get("/api/workspaces").json()
    assert [w["id"] for w in workspaces] == [demo["id"]]
    runs = client.get(f"/api/workspaces/{demo['id']}/runs").json()
    assert runs and runs[0]["status"] == "completed"


def test_dataset_profile_endpoint(client: TestClient, orders_csv: Path) -> None:
    ws = login(client)
    dataset = upload(client, ws, orders_csv)
    detail = client.get(f"/api/workspaces/{ws}/datasets/{dataset['id']}").json()
    assert len(detail["preview"]) == 20 and detail["profile"] is None
    detail = client.post(f"/api/workspaces/{ws}/datasets/{dataset['id']}/profile").json()
    assert detail["profile"]["row_count"] == 2412 and detail["has_profile"]
    assert client.delete(f"/api/workspaces/{ws}/datasets/{dataset['id']}").status_code == 204
    assert client.get(f"/api/workspaces/{ws}/datasets").json() == []


EXPERIMENT_CSV = Path(__file__).resolve().parents[3] / "samples" / "checkout_experiment.csv"


def test_experiment_analysis_end_to_end(client: TestClient) -> None:
    ws = login(client)
    upload(client, ws, EXPERIMENT_CSV)
    use_llm(demo_script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(
        f"/api/workspaces/{ws}/runs", json={"goal": "Did the new checkout test win without hurting latency?"}
    ).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed", run["error"]
    assert run["intent"] == "experiment_analysis"
    assert [s["agent"] for s in run["steps"]] == ["data", "experiment", "insight"]
    kinds = {a["kind"] for a in run["artifacts"]}
    assert {"srm", "ab_test", "cuped", "segment_effects", "bayesian", "sequential"} <= kinds
    assert len(run["insights"]) == 4
    assert all(i["verified"] for i in run["insights"]), [
        [c for c in i["checks"] if not c["passed"]] for i in run["insights"]
    ]
    names = {c["name"] for c in run["insights"][0]["checks"]}
    assert {"interval_reported", "multiple_comparisons", "sample_ratio"} <= names


def test_experiment_design_end_to_end(client: TestClient) -> None:
    ws = login(client)
    dataset = upload(client, ws, EXPERIMENT_CSV)

    def script(req: Request) -> list[Any]:
        if req.role == "planning":
            return [
                ToolUseBlock(
                    "submit_plan",
                    {
                        "intent": "experiment_design",
                        "dataset_ids": [dataset["id"]],
                        "params": [
                            {"name": "metric", "value": "converted"},
                            {"name": "mde", "value": "5% relative"},
                            {"name": "date_column", "value": "assigned_on"},
                        ],
                        "rationale": "Sizing a test.",
                        "confidence": "high",
                    },
                )
            ]
        if "power_analysis" in req.tool_names:
            from inferra.llm.demo_script import _results

            results = _results(req)
            if not results:
                return [
                    ToolUseBlock(
                        "metric_baseline",
                        {
                            "table": "checkout_experiment",
                            "metric_column": "converted",
                            "date_column": "assigned_on",
                        },
                    )
                ]
            if len(results) == 1:
                b = results[0]["result"]
                return [
                    ToolUseBlock(
                        "power_analysis",
                        {
                            "metric_type": "binary",
                            "baseline_mean": b["mean"],
                            "mde": 0.05,
                            "daily_units": b["daily_units"],
                        },
                    )
                ]
            return [ToolUseBlock("finish_step", {"summary": "Sized the test."})]
        if req.role == "insight":
            from inferra.llm.demo_script import _artifacts

            power = _artifacts(req)["Sample size and duration"]
            d = power["data"]
            return [
                ToolUseBlock(
                    "submit_insights",
                    {
                        "insights": [
                            {
                                "title": "Plan for a long test",
                                "claim": f"Detecting a 5% relative lift needs {d['sample_size_per_variant']:,} users "
                                "per variant, "
                                f"about {d['estimated_days']} days at current traffic.",
                                "evidence": [
                                    {
                                        "artifact_id": power["id"],
                                        "path": "mde_relative",
                                        "value": d["mde_relative"],
                                    },
                                    {
                                        "artifact_id": power["id"],
                                        "path": "sample_size_per_variant",
                                        "value": d["sample_size_per_variant"],
                                    },
                                    {
                                        "artifact_id": power["id"],
                                        "path": "estimated_days",
                                        "value": d["estimated_days"],
                                    },
                                ],
                                "method": "Two-sided z-test power analysis at alpha 0.05 and 80% power.",
                                "caveats": [],
                                "recommended_action": "Consider a larger MDE.",
                                "confidence": "high",
                            }
                        ]
                    },
                )
            ]
        return demo_script(req)

    use_llm(script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": "How long to detect a 5% lift?"}).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed", run["error"]
    assert [s["agent"] for s in run["steps"]] == ["data", "experiment", "insight"]
    assert {t["tool"] for t in run["tool_calls"]} >= {"metric_baseline", "power_analysis"}
    insight = run["insights"][0]
    assert insight["verified"], insight["checks"]


SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"


def test_predict_run_registers_a_model_that_scores_datasets(client: TestClient) -> None:
    ws = login(client)
    dataset = upload(client, ws, SAMPLES_DIR / "customer_churn.csv")
    use_llm(demo_script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(
        f"/api/workspaces/{ws}/runs", json={"goal": "Predict which customers will churn"}
    ).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed", run["error"]
    assert [s["agent"] for s in run["steps"]] == ["data", "ml", "insight"]
    assert all(i["verified"] for i in run["insights"]), [i["checks"] for i in run["insights"]]
    model_artifact = next(a for a in run["artifacts"] if a["kind"] == "model")

    models = client.get(f"/api/workspaces/{ws}/models").json()
    assert len(models) == 1 and models[0]["id"] == model_artifact["data"]["model_id"]
    detail = client.get(f"/api/workspaces/{ws}/models/{models[0]['id']}").json()
    assert detail["card"]["target"] == "churned" and detail["card"]["limitations"]

    scored = client.post(
        f"/api/workspaces/{ws}/models/{models[0]['id']}/score", json={"dataset_id": dataset["id"]}
    )
    assert scored.status_code == 201, scored.text
    scored = scored.json()
    assert scored["row_count"] == 6000
    assert "churned_probability" in [c["name"] for c in scored["columns"]]

    other = upload(client, ws, SAMPLES_DIR / "daily_sales.csv")
    bad = client.post(
        f"/api/workspaces/{ws}/models/{models[0]['id']}/score", json={"dataset_id": other["id"]}
    )
    assert bad.status_code == 400 and "missing model features" in bad.json()["detail"]


@pytest.mark.parametrize(
    ("file", "goal", "intent"),
    [
        ("loyalty_program.csv", "Did the loyalty program increase weekly sales?", "estimate_effect"),
        ("daily_sales.csv", "Forecast daily orders for the next four weeks", "forecast"),
    ],
)
def test_modeling_runs_end_to_end(client: TestClient, file: str, goal: str, intent: str) -> None:
    ws = login(client)
    upload(client, ws, SAMPLES_DIR / file)
    use_llm(demo_script)
    client.patch(f"/api/workspaces/{ws}", json={"auto_approve_plans": True})
    run = client.post(f"/api/workspaces/{ws}/runs", json={"goal": goal}).json()
    drain()
    run = client.get(f"/api/workspaces/{ws}/runs/{run['id']}").json()
    assert run["status"] == "completed", run["error"]
    assert run["intent"] == intent
    assert [s["agent"] for s in run["steps"]] == ["data", "analyst", "insight"]
    assert run["insights"] and all(i["verified"] for i in run["insights"]), [
        i["checks"] for i in run["insights"]
    ]
