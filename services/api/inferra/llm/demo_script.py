"""A deterministic stand-in for the model on the demo datasets (orders and the checkout test).

It drives the real orchestrator, tools, executor, and verifier, reading actual tool results, so
the seeded demo run and the end-to-end tests exercise the same code path as production. Only the
language-model decisions are scripted."""

import json
import re
from typing import Any

from inferra.llm.scripted import Request, TextBlock, ToolUseBlock


def _results(req: Request) -> list[dict[str, Any]]:
    """All tool results so far, parsed, in order."""
    out = []
    for msg in req.messages:
        if msg["role"] == "user" and isinstance(msg["content"], list):
            for block in msg["content"]:
                if block.get("type") == "tool_result" and not block.get("is_error"):
                    try:
                        out.append(json.loads(block["content"]))
                    except (TypeError, ValueError):
                        out.append({"text": block["content"]})
    return out


def _table(req: Request) -> str:
    m = re.search(r'"table": "(\w+)"', req.first_user_text)
    return m.group(1) if m else "ecommerce_orders"


EXPERIMENT_WORDS = re.compile(r"\b(test|variant|experiment|treatment|win|won|a/b)\b", re.I)


def _catalog(req: Request) -> list[dict[str, Any]]:
    text = req.first_user_text
    start = text.index("Dataset catalog (JSON):") + len("Dataset catalog (JSON):")
    end = text.index("\n\nDatasets the user selected", start)
    return json.loads(text[start:end])


def plan(req: Request) -> list[Any]:
    catalog = _catalog(req)
    goal = req.first_user_text.splitlines()[0]
    experiment = next((d for d in catalog if any(c.startswith("variant ") for c in d["columns"])), None)
    if experiment is not None and EXPERIMENT_WORDS.search(goal):
        params = {
            "variant_column": "variant",
            "control": "control",
            "primary_metric": "converted",
            "guardrail_metrics": "checkout_latency_ms, revenue",
            "covariate": "pre_orders",
            "segments": "device",
            "date_column": "assigned_on",
        }
        return [
            TextBlock("The user is asking whether a randomized test worked."),
            ToolUseBlock(
                "submit_plan",
                {
                    "intent": "experiment_analysis",
                    "dataset_ids": [experiment["id"]],
                    "params": [{"name": k, "value": v} for k, v in params.items()],
                    "rationale": "A variant's impact on randomized users is an A/B analysis.",
                    "confidence": "high",
                },
            ),
        ]
    orders = next((d for d in catalog if d["table"] == "ecommerce_orders"), catalog[0])
    return [
        TextBlock("The user wants an overview of the orders data, which is an exploration."),
        ToolUseBlock(
            "submit_plan",
            {
                "intent": "explore",
                "dataset_ids": [orders["id"]],
                "params": [{"name": "focus", "value": "order value, returns, and data quality"}],
                "rationale": "An overview question maps to the explore workflow.",
                "confidence": "high",
            },
        ),
    ]


def data_agent(req: Request) -> list[Any]:
    results = _results(req)
    if not results:
        return [ToolUseBlock("profile_dataset", {"table": _table(req)})]
    profile = results[0]
    findings = profile["result"].get("findings", [])
    issues = ", ".join(f"{f['issue']} ({f['column'] or 'table'})" for f in findings) or "none"
    return [
        ToolUseBlock(
            "finish_step",
            {
                "summary": f"Profiled {_table(req)} (artifact {profile['artifact_id']}): "
                f"{profile['result']['row_count']} rows. Data-quality findings: {issues}.",
                "key_artifact_ids": [profile["artifact_id"]],
            },
        )
    ]


def analyst_agent(req: Request) -> list[Any]:
    table = _table(req)
    results = _results(req)
    if len(results) == 0:
        return [
            ToolUseBlock(
                "group_summary",
                {
                    "table": table,
                    "group_by": ["region"],
                    "metrics": [{"agg": "avg", "column": "amount"}, {"agg": "count"}],
                    "order_by_metric": 0,
                    "limit": 10,
                    "title": "Average order value by region",
                },
            )
        ]
    if len(results) == 1:
        return [
            ToolUseBlock(
                "run_sql",
                {
                    "title": "Return rate by discount level",
                    "sql": f"SELECT discount, count(*) AS orders, "
                    f"avg(CASE WHEN returned::VARCHAR = 'true' THEN 1 ELSE 0 END) AS return_rate "
                    f'FROM "{table}" GROUP BY 1 ORDER BY 1',
                },
            )
        ]
    ids = [r["artifact_id"] for r in results]
    return [
        ToolUseBlock(
            "finish_step",
            {
                "summary": f"Compared order value across regions ({ids[0]}) and return rates across "
                f"discount levels ({ids[1]}).",
                "key_artifact_ids": ids,
            },
        )
    ]


def _artifacts(req: Request) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for line in req.first_user_text.splitlines():
        if line.startswith('{"artifact_id"'):
            item = json.loads(line)
            found[item["title"]] = {"id": item["artifact_id"], "data": json.loads(item["data"])}
    return found


def insights(req: Request) -> list[Any]:
    arts = _artifacts(req)
    if any(k.startswith("A/B test:") for k in arts):
        return experiment_insights(arts)
    profile = next(v for k, v in arts.items() if k.startswith("Profile of"))
    region = arts["Average order value by region"]
    returns = arts["Return rate by discount level"]
    p, cols = profile["data"], profile["data"]["columns"]
    top, bottom = region["data"]["rows"][0], region["data"]["rows"][-1]
    last = len(region["data"]["rows"]) - 1
    rrows = returns["data"]["rows"]
    base, high = rrows[0], rrows[-1]
    hi = len(rrows) - 1

    def ev(art: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
        return {"artifact_id": art["id"], "path": path, "value": value}

    out = [
        {
            "title": "Clean the data before reporting on it",
            "claim": f"{p['duplicate_rows']} rows are exact duplicates, "
            f"{cols['rating']['null_rate'] * 100:.1f}% of orders have no rating, and "
            f"{cols['amount']['negative_count']} orders carry a negative amount.",
            "evidence": [
                ev(profile, "duplicate_rows", p["duplicate_rows"]),
                ev(profile, "columns.rating.null_rate", cols["rating"]["null_rate"]),
                ev(profile, "columns.amount.negative_count", cols["amount"]["negative_count"]),
            ],
            "method": "Column profiling with DuckDB aggregates and rule-based quality checks.",
            "caveats": ["Negative amounts may be refunds recorded as orders; confirm with the source."],
            "recommended_action": "Deduplicate rows and move refunds to a separate table before "
            "computing revenue.",
            "confidence": "high",
        },
        {
            "title": f"{top['region']} has the highest average order value",
            "claim": f"{top['region']} averages ${top['avg_amount']:.2f} per order across "
            f"{top['count']} orders, compared with ${bottom['avg_amount']:.2f} in "
            f"{bottom['region']}.",
            "evidence": [
                ev(region, "rows.0.avg_amount", top["avg_amount"]),
                ev(region, "rows.0.count", top["count"]),
                ev(region, f"rows.{last}.avg_amount", bottom["avg_amount"]),
            ],
            "method": "Mean of order amount grouped by region.",
            "caveats": [
                "Averages include duplicate rows and negative amounts flagged above.",
                "This is a descriptive difference, not an explanation of its cause.",
            ],
            "recommended_action": "Check whether product mix or pricing differs by region.",
            "confidence": "medium",
        },
        {
            "title": "Heavily discounted orders come back more often",
            "claim": f"Orders with a {high['discount'] * 100:.0f}% discount are returned "
            f"{high['return_rate'] * 100:.1f}% of the time, compared with "
            f"{base['return_rate'] * 100:.1f}% for orders without a discount.",
            "evidence": [
                ev(returns, f"rows.{hi}.discount", high["discount"]),
                ev(returns, f"rows.{hi}.return_rate", high["return_rate"]),
                ev(returns, "rows.0.return_rate", base["return_rate"]),
            ],
            "method": "Share of returned orders grouped by discount level.",
            "caveats": [
                "This is an association; discounts may target products that are returned more often."
            ],
            "recommended_action": "Run an A/B test on discount depth with return rate as a guardrail metric.",
            "confidence": "medium",
        },
    ]
    return [ToolUseBlock("submit_insights", {"insights": out})]


EXPERIMENT_STEPS: list[tuple[str, dict[str, Any]]] = [
    ("srm_check", {"variant_column": "variant"}),
    ("ab_test", {"variant_column": "variant", "control": "control", "metric_column": "converted"}),
    (
        "cuped_test",
        {
            "variant_column": "variant",
            "control": "control",
            "metric_column": "converted",
            "covariate_column": "pre_orders",
        },
    ),
    ("ab_test", {"variant_column": "variant", "control": "control", "metric_column": "checkout_latency_ms"}),
    (
        "segment_effects",
        {
            "variant_column": "variant",
            "control": "control",
            "treatment": "treatment",
            "metric_column": "converted",
            "segment_column": "device",
        },
    ),
    (
        "bayesian_ab",
        {
            "variant_column": "variant",
            "control": "control",
            "treatment": "treatment",
            "metric_column": "converted",
        },
    ),
    (
        "sequential_test",
        {
            "variant_column": "variant",
            "control": "control",
            "treatment": "treatment",
            "metric_column": "converted",
            "date_column": "assigned_on",
        },
    ),
]


def experiment_agent(req: Request) -> list[Any]:
    results = _results(req)
    if len(results) < len(EXPERIMENT_STEPS):
        name, args = EXPERIMENT_STEPS[len(results)]
        return [ToolUseBlock(name, {"table": _table(req), **args})]
    ids = [r["artifact_id"] for r in results]
    srm, primary, cuped = results[0]["result"], results[1]["result"], results[2]["result"]
    lift = primary["comparisons"][0]["relative_lift"]
    return [
        ToolUseBlock(
            "finish_step",
            {
                "summary": f"SRM check passed={srm['srm_ok']} ({ids[0]}). Conversion lift {lift:.4f} "
                f"({ids[1]}); CUPED variance reduction {cuped['variance_reduction']:.4f} ({ids[2]}). "
                f"Latency guardrail ({ids[3]}), device segments ({ids[4]}), Bayesian ({ids[5]}), "
                f"sequential ({ids[6]}).",
                "key_artifact_ids": ids,
            },
        )
    ]


def _pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def _p(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def experiment_insights(arts: dict[str, dict[str, Any]]) -> list[Any]:
    primary = arts["A/B test: converted"]
    cuped = arts["CUPED test: converted"]
    latency = arts["A/B test: checkout_latency_ms"]
    segments = next(v for k, v in arts.items() if k.startswith("Effect of"))
    bayes = arts["Bayesian comparison: converted"]
    seq = arts["Sequential test: converted"]

    def ev(art: dict[str, Any], path: str) -> dict[str, Any]:
        from inferra.verify.verifier import resolve_path

        return {"artifact_id": art["id"], "path": path, "value": resolve_path(art["data"], path)}

    c = cuped["data"]["comparisons"][0]
    p0 = primary["data"]["comparisons"][0]
    lat = latency["data"]["comparisons"][0]
    segs = segments["data"]["segments"]
    top = max(range(len(segs)), key=lambda i: segs[i]["relative_lift"])
    s = segs[top]
    b = bayes["data"]
    het = segments["data"]["heterogeneity_p_value"]
    seq_p, seq_alpha, seq_ok = (
        seq["data"]["always_valid_p_value"],
        seq["data"]["alpha"],
        seq["data"]["significant"],
    )
    out = [
        {
            "title": "The new checkout increased conversion",
            "claim": f"Conversion rose from {_pct(p0['mean_control'], 2)} to "
            f"{_pct(p0['mean_treatment'], 2)}. "
            f"After CUPED adjustment for pre-period orders the relative lift is {_pct(c['relative_lift'])} "
            f"(95% CI {_pct(c['relative_lift_ci_low'])} to {_pct(c['relative_lift_ci_high'])}, "
            f"{_p(c['p_value'])}).",
            "evidence": [
                ev(primary, "comparisons.0.mean_control"),
                ev(primary, "comparisons.0.mean_treatment"),
                ev(cuped, "comparisons.0.relative_lift"),
                ev(cuped, "comparisons.0.relative_lift_ci_low"),
                ev(cuped, "comparisons.0.relative_lift_ci_high"),
                ev(cuped, "comparisons.0.p_value"),
            ],
            "method": "Two-proportion z-test on conversion, then a CUPED-adjusted Welch t-test using "
            "pre-period orders as the covariate. The sample ratio check passed.",
            "caveats": [
                f"CUPED reduced variance by {_pct(cuped['data']['variance_reduction'])}; the unadjusted "
                "estimate tells the same story."
            ],
            "recommended_action": "Plan the rollout, weighing the latency guardrail below.",
            "confidence": "high",
        },
        {
            "title": "Checkout got slower",
            "claim": f"Average checkout latency rose by {lat['diff']:.1f} ms "
            f"(95% CI {lat['diff_ci_low']:.1f} to {lat['diff_ci_high']:.1f} ms, {_p(lat['p_value'])}), "
            f"a {_pct(lat['relative_lift'])} increase.",
            "evidence": [
                ev(latency, "comparisons.0.diff"),
                ev(latency, "comparisons.0.diff_ci_low"),
                ev(latency, "comparisons.0.diff_ci_high"),
                ev(latency, "comparisons.0.p_value"),
                ev(latency, "comparisons.0.relative_lift"),
            ],
            "method": "Welch's t-test on checkout latency (guardrail metric).",
            "caveats": ["Latency is a page-level average; check the slowest percentiles before launch."],
            "recommended_action": "Profile the new payment step and set a latency budget before rollout.",
            "confidence": "high",
        },
        {
            "title": f"The lift is largest on {s['segment']}",
            "claim": f"On {s['segment']} the relative lift is {_pct(s['relative_lift'])} "
            f"(95% CI {_pct(s['relative_lift_ci_low'])} to {_pct(s['relative_lift_ci_high'])}, "
            f"Holm-adjusted p = {s['p_value_adjusted']:.3f}). "
            + (
                f"Effects differ across devices (heterogeneity p = {het:.3f})."
                if het < segments["data"]["alpha"]
                else f"The difference between devices is not established (heterogeneity p = {het:.3f})."
            ),
            "evidence": [
                ev(segments, f"segments.{top}.relative_lift"),
                ev(segments, f"segments.{top}.relative_lift_ci_low"),
                ev(segments, f"segments.{top}.relative_lift_ci_high"),
                ev(segments, f"segments.{top}.p_value_adjusted"),
                ev(segments, "heterogeneity_p_value"),
            ],
            "method": "Per-device z-tests with Holm correction; Cochran's Q for heterogeneity.",
            "caveats": ["Segment differences are hypotheses unless the heterogeneity test is significant."],
            "recommended_action": f"Prioritize {s['segment']} and confirm it in a follow-up test.",
            "confidence": "medium",
        },
        {
            "title": "Peeking-safe read agrees" if seq_ok else "Peeking-safe read is not yet conclusive",
            "claim": f"The probability that treatment converts better is {_pct(b['prob_treatment_better'])}. "
            + (
                f"The always-valid sequential p-value is {seq_p:.4f}, so the result holds even though "
                "the test was checked daily."
                if seq_ok
                else f"The always-valid sequential p-value is {seq_p:.4f}, above the {seq_alpha} threshold: "
                "if the test was stopped early because it looked good, the evidence is weaker than the "
                "fixed-horizon test suggests."
            ),
            "evidence": [
                ev(bayes, "prob_treatment_better"),
                ev(seq, "always_valid_p_value"),
                ev(seq, "alpha"),
            ],
            "method": "Beta-Binomial posterior with a uniform prior; mixture SPRT over daily looks.",
            "caveats": ["The Bayesian read uses an uninformative prior and ignores the latency trade-off."],
            "recommended_action": "Roll out, monitoring latency."
            if seq_ok
            else "Ship only if the test ran its planned duration; otherwise let it finish.",
            "confidence": "high" if seq_ok else "medium",
        },
    ]
    return [ToolUseBlock("submit_insights", {"insights": out})]


def demo_script(req: Request) -> list[Any]:
    if req.role == "planning":
        return plan(req)
    if req.role == "insight":
        return insights(req)
    if "power_analysis" in req.tool_names:
        return experiment_agent(req)
    if "profile_dataset" in req.tool_names and "group_summary" not in req.tool_names:
        return data_agent(req)
    return analyst_agent(req)
