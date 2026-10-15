from inferra.verify.verifier import resolve_path, verify_insight

ARTIFACTS = {
    "a1": {
        "kind": "table",
        "data": {"rows": [{"region": "North", "avg_amount": 92.4371, "n": 612, "share": 0.2537}]},
    },
    "a2": {"kind": "profile", "data": {"row_count": 2412, "columns": {"rating": {"null_rate": 0.1791}}}},
    "x1": {"kind": "experiment", "data": {"design": "randomized", "lift": 0.031, "n": 8000}},
    "s1": {"kind": "table", "data": {"rows": [{"n": 12, "rate": 0.5}]}},
}


def draft(claim: str, evidence: list[dict], **extra) -> dict:  # type: ignore[type-arg]
    return {
        "title": "Finding",
        "claim": claim,
        "evidence": evidence,
        "method": "SQL",
        "caveats": [],
        "confidence": "medium",
        **extra,
    }


def check(verdict, name):  # type: ignore[no-untyped-def]
    return next(c for c in verdict.checks if c.name == name)


def test_resolve_path_supports_dots_and_brackets() -> None:
    data = ARTIFACTS["a1"]["data"]
    assert resolve_path(data, "rows.0.n") == 612
    assert resolve_path(data, "rows[0].region") == "North"


def test_grounded_insight_passes_with_rounding_and_percentages() -> None:
    v = verify_insight(
        draft(
            "North averages $92.44 per order across 612 orders, 25.4% of all orders.",
            [
                {"artifact_id": "a1", "path": "rows.0.avg_amount", "value": 92.4371},
                {"artifact_id": "a1", "path": "rows.0.n", "value": 612},
                {"artifact_id": "a1", "path": "rows.0.share", "value": 0.2537},
            ],
        ),
        ARTIFACTS,
    )
    assert v.verified, v.failures()


def test_numbers_without_citations_fail() -> None:
    v = verify_insight(
        draft(
            "North averages $92.44, about 15% above the South.",
            [{"artifact_id": "a1", "path": "rows.0.avg_amount", "value": 92.4371}],
        ),
        ARTIFACTS,
    )
    assert not check(v, "number_grounding").passed
    assert "15%" in check(v, "number_grounding").detail


def test_wrong_precision_fails() -> None:
    v = verify_insight(
        draft(
            "North averages $92.50 per order.",
            [{"artifact_id": "a1", "path": "rows.0.avg_amount", "value": 92.4371}],
        ),
        ARTIFACTS,
    )
    assert not check(v, "number_grounding").passed


def test_misquoted_or_missing_evidence_fails() -> None:
    v = verify_insight(
        draft(
            "Rating is missing for 17.9% of orders.",
            [
                {"artifact_id": "a2", "path": "columns.rating.null_rate", "value": 0.25},
                {"artifact_id": "nope", "path": "x", "value": 1},
            ],
        ),
        ARTIFACTS,
    )
    detail = check(v, "evidence").detail
    assert not v.verified and "quotes 0.25" in detail and "unknown artifact" in detail


def test_numbers_from_context_are_allowed() -> None:
    v = verify_insight(
        draft(
            "Rating is missing for 17.9% of the 2026 orders.",
            [{"artifact_id": "a2", "path": "columns.rating.null_rate", "value": 0.1791}],
        ),
        ARTIFACTS,
        context_text="How complete is the 2026 order data?",
    )
    assert v.verified, v.failures()


def test_causal_language_needs_a_causal_design() -> None:
    observational = verify_insight(
        draft(
            "Discounts caused 612 orders in the North.",
            [{"artifact_id": "a1", "path": "rows.0.n", "value": 612}],
        ),
        ARTIFACTS,
    )
    assert not check(observational, "causal_language").passed
    experiment = verify_insight(
        draft(
            "The new checkout caused a 3.1% lift.", [{"artifact_id": "x1", "path": "lift", "value": 0.031}]
        ),
        ARTIFACTS,
    )
    assert check(experiment, "causal_language").passed
    hedged = verify_insight(
        draft(
            "North has 612 orders.",
            [{"artifact_id": "a1", "path": "rows.0.n", "value": 612}],
            caveats=["This does not establish what caused the difference."],
        ),
        ARTIFACTS,
    )
    assert check(hedged, "causal_language").passed


def test_high_confidence_needs_enough_observations() -> None:
    v = verify_insight(
        draft(
            "Half of the 12 sampled orders were returned.",
            [
                {"artifact_id": "s1", "path": "rows.0.n", "value": 12},
                {"artifact_id": "s1", "path": "rows.0.rate", "value": 0.5},
            ],
            confidence="high",
        ),
        ARTIFACTS,
    )
    assert not check(v, "sample_size").passed
