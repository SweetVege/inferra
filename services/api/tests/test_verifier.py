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


EXPERIMENTS = {
    "ab": {
        "kind": "ab_test",
        "data": {
            "design": "randomized",
            "srm_ok": True,
            "comparisons": [
                {
                    "relative_lift": 0.1045,
                    "relative_lift_ci_low": 0.034,
                    "relative_lift_ci_high": 0.175,
                    "p_value": 0.0042,
                    "n_treatment": 12036,
                }
            ],
        },
    },
    "seg": {
        "kind": "segment_effects",
        "data": {
            "design": "randomized",
            "segments": [
                {
                    "relative_lift": 0.154,
                    "relative_lift_ci_low": 0.05,
                    "relative_lift_ci_high": 0.26,
                    "p_value": 0.004,
                    "p_value_adjusted": 0.012,
                }
            ],
        },
    },
    "broken": {
        "kind": "ab_test",
        "data": {
            "design": "randomized_srm_failed",
            "srm_ok": False,
            "comparisons": [
                {
                    "relative_lift": 0.08,
                    "relative_lift_ci_low": 0.01,
                    "relative_lift_ci_high": 0.15,
                    "p_value": 0.02,
                }
            ],
        },
    },
}


def ev(artifact: str, path: str) -> dict:  # type: ignore[type-arg]
    return {"artifact_id": artifact, "path": path, "value": resolve_path(EXPERIMENTS[artifact]["data"], path)}


def test_experiment_insight_with_interval_passes_and_may_use_causal_wording() -> None:
    v = verify_insight(
        draft(
            "The new checkout increased conversion by 10.4% (95% CI 3.4% to 17.5%, p = 0.004).",
            [
                ev("ab", "comparisons.0.relative_lift"),
                ev("ab", "comparisons.0.relative_lift_ci_low"),
                ev("ab", "comparisons.0.relative_lift_ci_high"),
                ev("ab", "comparisons.0.p_value"),
            ],
            confidence="high",
        ),
        EXPERIMENTS,
    )
    assert v.verified, v.failures()


def test_p_value_without_interval_fails() -> None:
    v = verify_insight(
        draft(
            "Conversion improved by 10.4% (p = 0.004).",
            [ev("ab", "comparisons.0.relative_lift"), ev("ab", "comparisons.0.p_value")],
        ),
        EXPERIMENTS,
    )
    assert not check(v, "interval_reported").passed


def test_segment_results_need_adjusted_p_values() -> None:
    evidence = [
        ev("seg", "segments.0.relative_lift"),
        ev("seg", "segments.0.relative_lift_ci_low"),
        ev("seg", "segments.0.relative_lift_ci_high"),
    ]
    raw = verify_insight(
        draft(
            "Mobile lift is 15.4% (95% CI 5.0% to 26.0%, p = 0.004).",
            [*evidence, ev("seg", "segments.0.p_value")],
        ),
        EXPERIMENTS,
    )
    assert not check(raw, "multiple_comparisons").passed
    adjusted = verify_insight(
        draft(
            "Mobile lift is 15.4% (95% CI 5.0% to 26.0%, adjusted p = 0.012).",
            [*evidence, ev("seg", "segments.0.p_value_adjusted")],
        ),
        EXPERIMENTS,
    )
    assert adjusted.verified, adjusted.failures()


def test_failed_sample_ratio_must_be_disclosed() -> None:
    evidence = [
        ev("broken", "comparisons.0.relative_lift"),
        ev("broken", "comparisons.0.relative_lift_ci_low"),
        ev("broken", "comparisons.0.relative_lift_ci_high"),
    ]
    hidden = verify_insight(draft("Treatment is 8.0% better (95% CI 1.0% to 15.0%).", evidence), EXPERIMENTS)
    assert not check(hidden, "sample_ratio").passed
    disclosed = verify_insight(
        draft(
            "The measured difference of 8.0% (95% CI 1.0% to 15.0%) cannot be trusted.",
            evidence,
            confidence="low",
            caveats=["Assignment counts fail the sample ratio check."],
        ),
        EXPERIMENTS,
    )
    assert check(disclosed, "sample_ratio").passed


def test_p_value_thresholds_must_be_true() -> None:
    evidence = [
        ev("ab", "comparisons.0.relative_lift"),
        ev("ab", "comparisons.0.relative_lift_ci_low"),
        ev("ab", "comparisons.0.relative_lift_ci_high"),
        ev("ab", "comparisons.0.p_value"),
    ]
    honest = verify_insight(draft("Lift 10.4% (95% CI 3.4% to 17.5%, p < 0.01).", evidence), EXPERIMENTS)
    assert honest.verified, honest.failures()
    overstated = verify_insight(draft("Lift 10.4% (95% CI 3.4% to 17.5%, p < 0.001).", evidence), EXPERIMENTS)
    assert "p < 0.001" in check(overstated, "number_grounding").detail


def test_model_names_and_interval_conventions_are_not_flagged() -> None:
    v = verify_insight(
        draft(
            "Gradient-boosted trees reached 612 orders (95% interval 612 to 612).",
            [{"artifact_id": "a1", "path": "rows.0.n", "value": 612}],
        ),
        ARTIFACTS,
    )
    assert check(v, "causal_language").passed
    assert check(v, "number_grounding").passed
    boosted = verify_insight(
        draft(
            "The promotion boosted orders to 612.", [{"artifact_id": "a1", "path": "rows.0.n", "value": 612}]
        ),
        ARTIFACTS,
    )
    assert not check(boosted, "causal_language").passed


MODELING = {
    "did": {
        "kind": "did",
        "data": {
            "design": "causal",
            "assumption_keywords": ["parallel trend"],
            "att": 1974.8,
            "ci_low": 1532.7,
            "ci_high": 2417.0,
            "relative_effect": 0.0568,
            "relative_effect_ci_low": 0.044,
            "relative_effect_ci_high": 0.07,
        },
    },
    "did_bad": {
        "kind": "did",
        "data": {
            "design": "causal_pretrend_failed",
            "assumption_keywords": ["parallel trend"],
            "relative_effect": 0.0568,
            "relative_effect_ci_low": 0.044,
            "relative_effect_ci_high": 0.07,
        },
    },
    "model": {"kind": "model", "data": {"holdout": {"auc": 0.792}, "train_metrics": {"auc": 0.981}}},
    "fc": {
        "kind": "forecast",
        "data": {"horizon_total": {"forecast": 16737.3, "lower": 13222.0, "upper": 20395.3}},
    },
}


def mev(artifact: str, path: str) -> dict:  # type: ignore[type-arg]
    return {"artifact_id": artifact, "path": path, "value": resolve_path(MODELING[artifact]["data"], path)}


def test_observational_causal_claims_must_state_their_assumption() -> None:
    evidence = [
        mev("did", "relative_effect"),
        mev("did", "relative_effect_ci_low"),
        mev("did", "relative_effect_ci_high"),
    ]
    claim = "The program caused a 5.7% increase (95% CI 4.4% to 7.0%)."
    silent = verify_insight(draft(claim, evidence), MODELING)
    assert not check(silent, "assumptions_stated").passed
    stated = verify_insight(
        draft(claim, evidence, caveats=["Assumes parallel trends between store groups."]), MODELING
    )
    assert stated.verified, stated.failures()
    associational = verify_insight(
        draft("Program stores grew 5.7% more (95% CI 4.4% to 7.0%).", evidence), MODELING
    )
    assert associational.verified, associational.failures()


def test_failed_pretrend_blocks_causal_wording() -> None:
    evidence = [
        mev("did_bad", "relative_effect"),
        mev("did_bad", "relative_effect_ci_low"),
        mev("did_bad", "relative_effect_ci_high"),
    ]
    v = verify_insight(
        draft(
            "The program caused a 5.7% increase (95% CI 4.4% to 7.0%).",
            evidence,
            caveats=["Assumes parallel trends."],
        ),
        MODELING,
    )
    assert not check(v, "causal_language").passed


def test_effect_without_interval_fails() -> None:
    v = verify_insight(draft("Program stores grew $1,974.80 per week.", [mev("did", "att")]), MODELING)
    assert not check(v, "interval_reported").passed


def test_model_claims_need_holdout_metrics() -> None:
    train = verify_insight(
        draft("The model reaches an AUC of 0.981.", [mev("model", "train_metrics.auc")]), MODELING
    )
    assert not check(train, "holdout_metrics").passed
    holdout = verify_insight(
        draft("The model reaches an AUC of 0.792.", [mev("model", "holdout.auc")]), MODELING
    )
    assert holdout.verified, holdout.failures()


def test_forecasts_need_their_interval() -> None:
    point = verify_insight(draft("Expect 16,737 orders.", [mev("fc", "horizon_total.forecast")]), MODELING)
    assert not check(point, "interval_reported").passed
    ranged = verify_insight(
        draft(
            "Expect 16,737 orders (95% interval 13,222 to 20,395).",
            [
                mev("fc", "horizon_total.forecast"),
                mev("fc", "horizon_total.lower"),
                mev("fc", "horizon_total.upper"),
            ],
        ),
        MODELING,
    )
    assert ranged.verified, ranged.failures()


def test_labels_in_cited_results_may_be_repeated() -> None:
    artifacts = {
        "d": {
            "kind": "metric",
            "data": {
                "sql": "SELECT 1 LIMIT 50",
                "rows": [{"app_version": "5.2", "value": 0.032}, {"app_version": "5.1", "value": 0.0585}],
            },
        }
    }
    v = verify_insight(
        draft(
            "Version 5.2 converts at 3.2% versus 5.85% for 5.1.",
            [
                {"artifact_id": "d", "path": "rows.0.value", "value": 0.032},
                {"artifact_id": "d", "path": "rows.1.value", "value": 0.0585},
            ],
        ),
        artifacts,
    )
    assert v.verified, v.failures()
    sql_number = verify_insight(
        draft(
            "Version 5.2 converts at 3.2% in 50 regions.",
            [{"artifact_id": "d", "path": "rows.0.value", "value": 0.032}],
        ),
        artifacts,
    )
    assert "50" in check(sql_number, "number_grounding").detail


def test_failing_data_model_tests_must_be_disclosed() -> None:
    artifacts = {
        "m": {
            "kind": "data_model",
            "data": {"tests_failed": 1, "summary": {"tests": 18}, "nodes": [{"row_count": 6573}]},
        }
    }
    evidence = [{"artifact_id": "m", "path": "nodes.0.row_count", "value": 6573}]
    hidden = verify_insight(draft("The fact has 6,573 rows.", evidence, confidence="high"), artifacts)
    assert not check(hidden, "model_tests").passed
    disclosed = verify_insight(
        draft("The fact has 6,573 rows.", evidence, caveats=["One test fails on missing customers."]),
        artifacts,
    )
    assert check(disclosed, "model_tests").passed
    clean = {"m": {**artifacts["m"], "data": {**artifacts["m"]["data"], "tests_failed": 0}}}
    v = verify_insight(draft("The fact has 6,573 rows.", evidence, confidence="high"), clean)
    assert v.verified and all(c.name != "model_tests" for c in v.checks)


def test_a_range_shares_its_unit() -> None:
    artifacts = {
        "t": {
            "kind": "experiment",
            "data": {
                "design": "randomized",
                "comparisons": [{"diff": -0.0082, "diff_ci_low": -0.0133, "diff_ci_high": -0.0031}],
            },
        }
    }
    evidence = [
        {"artifact_id": "t", "path": f"comparisons.0.{k}", "value": v}
        for k, v in [("diff", -0.0082), ("diff_ci_low", -0.0133), ("diff_ci_high", -0.0031)]
    ]
    for claim in [
        "The difference was -0.82 pp (95% CI -1.33 to -0.31 pp).",
        "The difference was -0.82% (95% CI -1.33–-0.31%).",
        "The difference was -0.82 percentage points (95% CI -1.33 to -0.31 percent).",
    ]:
        v = verify_insight(draft(claim, evidence), artifacts)
        assert check(v, "number_grounding").passed, (claim, v.failures())
    # The unit is shared only across a range, not with an unrelated neighbor.
    v = verify_insight(draft("Retention fell 1.33 points, -0.82 pp.", evidence), artifacts)
    assert not check(v, "number_grounding").passed
