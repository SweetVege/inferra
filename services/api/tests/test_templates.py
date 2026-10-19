"""Workflow templates read cleanly whatever parameters the Orchestrator filled in."""

from inferra.workflows.templates import WORKFLOWS, fill, instantiate


def test_optional_clauses_drop_out_when_their_params_are_missing() -> None:
    plan = instantiate(
        "experiment_analysis",
        {"variant_column": "version", "primary_metric": "retention_7", "covariate": "", "segments": "none"},
    )
    analyze = next(s for s in plan["steps"] if s["key"] == "analyze")["task"]
    assert analyze.startswith(
        "Analyze the test. Check sample ratio first. Test the primary metric 'retention_7'."
    )
    assert "CUPED" not in analyze and "segment" not in analyze and "sequential" not in analyze

    full = instantiate(
        "experiment_analysis",
        {"variant_column": "version", "primary_metric": "retention_7", "covariate": "pre_rounds"},
    )
    assert "(with CUPED on pre_rounds)" in next(s for s in full["steps"] if s["key"] == "analyze")["task"]


def test_no_template_ever_says_not_specified() -> None:
    for intent, workflow in WORKFLOWS.items():
        required = {p.name: "x" for p in workflow.params if p.required}
        for step in instantiate(intent, required)["steps"]:
            assert "not specified" not in step["task"], (intent, step["key"])
            assert "[" not in step["task"] and "{" not in step["task"] and "  " not in step["task"]


def test_fill_keeps_clauses_whose_params_are_all_set() -> None:
    task = "Check {a}[ by {b}][ and {b} with {c}]."
    assert fill(task, {"a": "A", "b": "B"}) == "Check A by B."
    assert fill(task, {"a": "A", "b": "B", "c": "C"}) == "Check A by B and B with C."
