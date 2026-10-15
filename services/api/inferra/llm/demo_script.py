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


MODELING_ROUTES: list[tuple[re.Pattern[str], str, str, str, dict[str, str]]] = [
    (
        re.compile(r"star schema|data model|dimensional|\bmodel the\b", re.I),
        "retail_order_lines",
        "model_data",
        "The user wants raw data turned into an analytics model: a data modeling task.",
        {"subject": "order lines", "requirements": ""},
    ),
    (
        re.compile(r"\b(churn|predict)", re.I),
        "customer_churn",
        "predict",
        "Predicting a label for each customer is a predictive modeling task.",
        {"target": "churned", "features": "", "population": "all customers"},
    ),
    (
        re.compile(r"\b(loyalty|program)\b", re.I),
        "loyalty_program",
        "estimate_effect",
        "Stores joined the program at a known date and others did not: a difference-in-differences design.",
        {
            "treatment": "in_program",
            "outcome": "weekly_sales",
            "method": "diff_in_diff",
            "unit_column": "store_id",
            "time_column": "week_start",
            "post_start": "2026-04-06",
            "covariates": "",
        },
    ),
    (
        re.compile(r"\b(why|drop|declin)", re.I),
        "daily_funnel",
        "diagnose",
        "The user wants to know why a metric moved: a diagnosis.",
        {
            "metric": "conversion_rate",
            "comparison_period": "",
            "baseline_period": "",
            "dimensions": "platform, app_version, channel, country",
        },
    ),
    (
        re.compile(r"\bforecast", re.I),
        "daily_sales",
        "forecast",
        "The user wants future values of a time series.",
        {"metric": "orders", "date_column": "date", "freq": "day", "horizon": "28"},
    ),
]


def _modeling_plan(catalog: list[dict[str, Any]], goal: str) -> list[Any] | None:
    for pattern, table, intent, rationale, params in MODELING_ROUTES:
        dataset = next((d for d in catalog if d["table"] == table), None)
        if dataset is not None and pattern.search(goal):
            return [
                TextBlock(rationale),
                ToolUseBlock(
                    "submit_plan",
                    {
                        "intent": intent,
                        "dataset_ids": [dataset["id"]],
                        "params": [{"name": k, "value": v} for k, v in params.items()],
                        "rationale": rationale,
                        "confidence": "high",
                    },
                ),
            ]
    return None


def plan(req: Request) -> list[Any]:
    catalog = _catalog(req)
    goal = req.first_user_text.splitlines()[0]
    routed = _modeling_plan(catalog, goal)
    if routed is not None:
        return routed
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
    modeling = "(intent: model_data)" in req.first_user_text
    if modeling and len(results) == 1:
        return [ToolUseBlock("analyze_keys", {"table": _table(req)})]
    profile = results[0]
    if modeling:
        keys = results[1]["result"]
        unstable = ", ".join(
            f"{u['column']} varies for {u['violations']} {u['entity']} values"
            for u in keys["unstable_attributes"]
        )
        return [
            ToolUseBlock(
                "finish_step",
                {
                    "summary": f"Profiled {_table(req)} ({profile['artifact_id']}) and found its grain "
                    f"({' + '.join(keys['grain'])}) with analyze_keys ({results[1]['artifact_id']}). "
                    f"Duplicate rows: {keys['duplicate_rows']}. Unstable attributes: {unstable or 'none'}.",
                    "key_artifact_ids": [profile["artifact_id"], results[1]["artifact_id"]],
                },
            )
        ]
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
    if "(intent: estimate_effect)" in req.first_user_text:
        if not results:
            return [
                ToolUseBlock(
                    "diff_in_diff",
                    {
                        "table": table,
                        "outcome": "weekly_sales",
                        "unit_column": "store_id",
                        "time_column": "week_start",
                        "treated_column": "in_program",
                        "post_start": "2026-04-06",
                    },
                )
            ]
        did = results[0]
        return [
            ToolUseBlock(
                "finish_step",
                {
                    "summary": f"Difference-in-differences ({did['artifact_id']}): effect "
                    f"{did['result']['att']:.2f} per store-week, pre-trend p = {did['result']['pretrend_p_value']:.3f}.",
                    "key_artifact_ids": [did["artifact_id"]],
                },
            )
        ]
    if "(intent: diagnose)" in req.first_user_text:
        return _diagnose_steps(table, results)
    if "(intent: forecast)" in req.first_user_text:
        if not results:
            return [
                ToolUseBlock(
                    "forecast",
                    {
                        "table": table,
                        "date_column": "date",
                        "value_column": "orders",
                        "freq": "day",
                        "horizon": 28,
                    },
                )
            ]
        fc = results[0]
        return [
            ToolUseBlock(
                "finish_step",
                {
                    "summary": f"Forecast ({fc['artifact_id']}) with backtest MAPE {fc['result']['backtest']['mape']:.3f}.",
                    "key_artifact_ids": [fc["artifact_id"]],
                },
            )
        ]
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
    if any(k.startswith("Data model:") for k in arts):
        return datamodel_insights(arts)
    if any(k.startswith("A/B test:") for k in arts):
        return experiment_insights(arts)
    if any(k.startswith("Difference-in-differences:") for k in arts):
        return did_insights(arts)
    if any(k.startswith("What moved") for k in arts):
        return diagnose_insights(arts)
    if any(k.startswith("Forecast:") for k in arts):
        return forecast_insights(arts)
    if any(k.startswith("Model:") for k in arts):
        return model_insights(arts)
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


def ml_agent(req: Request) -> list[Any]:
    results = _results(req)
    if not results:
        return [
            ToolUseBlock(
                "train_model", {"table": _table(req), "target": "churned", "model_name": "Churn risk"}
            )
        ]
    m = results[0]["result"]
    return [
        ToolUseBlock(
            "finish_step",
            {
                "summary": f"Trained churn model ({results[0]['artifact_id']}); selected {m['selected_model']} "
                f"with holdout AUC {m['holdout']['auc']:.3f}.",
                "key_artifact_ids": [results[0]["artifact_id"]],
            },
        )
    ]


def modeler_agent(req: Request) -> list[Any]:
    table = _table(req)
    results = _results(req)
    if not results:
        return [ToolUseBlock("suggest_star_schema", {"table": table})]
    if len(results) == 1:
        return [
            ToolUseBlock(
                "build_data_model",
                {
                    "name": "Retail order lines star schema",
                    "description": "Order lines as a fact table with customer, product, and date dimensions.",
                    "nodes": results[0]["nodes"],
                },
            )
        ]
    built = results[1]
    summary = built["result"]["summary"]
    failing = "; ".join(f"{t['id']} ({t['failures']} rows)" for t in built["result"]["failing_tests"])
    return [
        ToolUseBlock(
            "finish_step",
            {
                "summary": f"Built the star schema ({built['artifact_id']}): {summary['nodes_built']} nodes, "
                f"{summary['tests_passed']} of {summary['tests']} tests pass. Failing: {failing or 'none'}.",
                "key_artifact_ids": [built["artifact_id"]],
            },
        )
    ]


def datamodel_insights(arts: dict[str, dict[str, Any]]) -> list[Any]:
    model = next(v for k, v in arts.items() if k.startswith("Data model:"))
    keys = next(v for k, v in arts.items() if k.startswith("Keys and entities:"))
    d = model["data"]
    index = {n["name"]: i for i, n in enumerate(d["nodes"])}
    fact = next(n for n in d["nodes"] if n["layer"] == "fact")
    dims = [n for n in d["nodes"] if n["layer"] == "dimension"]
    fi = index[fact["name"]]
    dim_text = ", ".join(f"{n['name']} ({n['row_count']:,} rows)" for n in dims)
    out = [
        {
            "title": f"The export fits a star schema with {len(dims)} dimensions",
            "claim": f"{fact['name']} keeps one row per {' + '.join(keys['data']['grain'])} "
            f"({fact['row_count']:,} rows) and joins to {dim_text}.",
            "evidence": [_ev(model, f"nodes.{fi}.row_count")]
            + [_ev(model, f"nodes.{index[n['name']]}.row_count") for n in dims]
            + [_ev(model, "summary.tests_failed"), _ev(model, "summary.tests")],
            "method": "Grain and entities from exact uniqueness and functional-dependency checks; nodes "
            "built in the sandbox and tested for uniqueness, nulls, accepted values, and referential integrity.",
            "caveats": [
                f"{d['summary']['tests_failed']} of {d['summary']['tests']} tests fail; the failing rows are "
                "described in the next insight."
            ],
            "recommended_action": "Review the draft data model, then publish the fact and dimensions as datasets.",
            "confidence": "medium",
        }
    ]
    failed = [(i, t) for i, t in enumerate(d["tests"]) if t["status"] == "fail"]
    if failed:
        ti, test = failed[0]
        out.append(
            {
                "title": f"{test['failures']} order lines have no customer",
                "claim": f"The {test['description']} test on {test['node']} fails for {test['failures']} of "
                f"{fact['row_count']:,} rows; every customer id that is present resolves to dim_customer.",
                "evidence": [_ev(model, f"tests.{ti}.failures"), _ev(model, f"nodes.{fi}.row_count")],
                "method": "not_null and relationships tests on the fact's foreign keys.",
                "caveats": [
                    "The test fails because of the source, not the transform: these look like guest checkouts."
                ],
                "recommended_action": "Add an 'unknown customer' member to dim_customer and map missing ids to "
                "it, so revenue from guest orders is not dropped by inner joins.",
                "confidence": "medium",
            }
        )
    unstable = keys["data"]["unstable_attributes"]
    if unstable:
        u = unstable[0]
        out.append(
            {
                "title": f"{u['column']} is not stable for {u['violations']} customers",
                "claim": f"{u['column']} takes more than one value for {u['violations']} of {u['groups']} "
                f"customers (for example {u['examples'][0]}), so the dimension keeps each customer's latest value.",
                "evidence": [
                    _ev(keys, "unstable_attributes.0.violations"),
                    _ev(keys, "unstable_attributes.0.groups"),
                    _ev(keys, "unstable_attributes.0.examples.0"),
                ],
                "method": "Functional-dependency check of each attribute against its entity key.",
                "caveats": [
                    "Reports by region will place these customers' earlier orders in their new region."
                ],
                "recommended_action": "If history matters, model the customer as a slowly changing dimension with "
                "one row per customer and validity period.",
                "confidence": "medium",
            }
        )
    return [ToolUseBlock("submit_insights", {"insights": out})]


def _ev(art: dict[str, Any], path: str) -> dict[str, Any]:
    from inferra.verify.verifier import resolve_path

    return {"artifact_id": art["id"], "path": path, "value": resolve_path(art["data"], path)}


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _diagnose_steps(table: str, results: list[dict[str, Any]]) -> list[Any]:
    import datetime as dt

    if not results:
        return [ToolUseBlock("detect_anomalies", {"metric": "conversion_rate", "grain": "day"})]
    change = results[0]["result"]["change_point"]
    start = dt.date.fromisoformat(change["period"])
    days = change["periods_after"]
    comp_end = start + dt.timedelta(days=days - 1)
    base_start, base_end = start - dt.timedelta(days=days), start - dt.timedelta(days=1)
    if len(results) == 1:
        return [
            ToolUseBlock(
                "decompose_change",
                {
                    "metric": "conversion_rate",
                    "baseline_start": base_start.isoformat(),
                    "baseline_end": base_end.isoformat(),
                    "comparison_start": start.isoformat(),
                    "comparison_end": comp_end.isoformat(),
                    "dimensions": ["platform", "app_version", "channel", "country"],
                },
            )
        ]
    if len(results) == 2:
        top = results[1]["result"]["dimensions"][0]
        return [
            ToolUseBlock(
                "compute_metric",
                {
                    "metric": "conversion_rate",
                    "group_by": ["app_version"],
                    "where": "platform = 'android'",
                    "start": start.isoformat(),
                    "end": comp_end.isoformat(),
                    "title": f"Android conversion by app version since {start.isoformat()} (top: {top['dimension']})",
                },
            )
        ]
    ids = [r["artifact_id"] for r in results]
    return [
        ToolUseBlock(
            "finish_step",
            {
                "summary": f"Change point {change['period']} ({ids[0]}); decomposition ({ids[1]}) ranks "
                f"{results[1]['result']['dimensions'][0]['dimension']} first; Android by version ({ids[2]}).",
                "key_artifact_ids": ids,
            },
        )
    ]


def diagnose_insights(arts: dict[str, dict[str, Any]]) -> list[Any]:
    anomalies = next(v for k, v in arts.items() if k.startswith("Anomalies in"))
    decomposition = next(v for k, v in arts.items() if k.startswith("What moved"))
    drill = next(v for k, v in arts.items() if k.startswith("Android conversion by app version"))
    cp = anomalies["data"]["change_point"]
    dims = decomposition["data"]["dimensions"]
    di = next(i for i, d in enumerate(dims) if d["dimension"] == "app_version")
    segs = dims[di]["segments"]
    si = next(i for i, sg in enumerate(segs) if sg["segment"] == dims[di]["top_segment"])
    seg = segs[si]
    rows = drill["data"]["rows"]
    old_i = next(i for i, r in enumerate(rows) if r["app_version"] != seg["segment"])
    new_i = next(i for i, r in enumerate(rows) if r["app_version"] == seg["segment"])
    out = [
        {
            "title": f"Conversion stepped down on {cp['period']}",
            "claim": f"Signup conversion averaged {_pct(cp['before_mean'], 2)} before {cp['period']} and "
            f"{_pct(cp['after_mean'], 2)} after, a {_pct(abs(cp['relative_change']))} decline.",
            "evidence": [
                _ev(anomalies, "change_point.period"),
                _ev(anomalies, "change_point.before_mean"),
                _ev(anomalies, "change_point.after_mean"),
                _ev(anomalies, "change_point.relative_change"),
            ],
            "method": "Least-squares change point on the daily conversion series, with robust anomaly scores.",
            "caveats": [
                f"Daily averages; the {cp['periods_after']} days after the change are compared with all earlier days."
            ],
            "recommended_action": "Check releases and campaigns that shipped around that date.",
            "confidence": "high",
        },
        {
            "title": f"App version {seg['segment']} accounts for most of the decline",
            "claim": f"Version {seg['segment']} accounts for {_pct(seg['share_of_change'])} of the drop in conversion "
            f"while carrying {_pct(seg['comparison_weight'])} of sessions; it converts at "
            f"{_pct(seg['comparison_value'], 2)} against a baseline of {_pct(decomposition['data']['baseline']['value'], 2)}.",
            "evidence": [
                _ev(decomposition, f"dimensions.{di}.segments.{si}.share_of_change"),
                _ev(decomposition, f"dimensions.{di}.segments.{si}.comparison_weight"),
                _ev(decomposition, f"dimensions.{di}.segments.{si}.comparison_value"),
                _ev(decomposition, "baseline.value"),
            ],
            "method": "Mix/rate decomposition of the conversion-rate change across platform, app version, channel, and country.",
            "caveats": [
                "A decomposition attributes the change arithmetically; it does not prove what is broken in the release."
            ],
            "recommended_action": f"Roll back or hotfix version {seg['segment']} and test its signup flow end to end.",
            "confidence": "high",
        },
        {
            "title": "Older Android builds still convert normally",
            "claim": f"On Android over the same days, version {rows[old_i]['app_version']} converts at "
            f"{_pct(rows[old_i]['value'], 2)} while version {rows[new_i]['app_version']} converts at "
            f"{_pct(rows[new_i]['value'], 2)}.",
            "evidence": [_ev(drill, f"rows.{old_i}.value"), _ev(drill, f"rows.{new_i}.value")],
            "method": "Conversion rate by app version, Android only, during the changed period.",
            "caveats": [
                "Users self-select into updating, so early adopters may differ; the gap is large enough to act on."
            ],
            "recommended_action": "Compare funnels step by step between the two versions to locate the failing step.",
            "confidence": "medium",
        },
    ]
    return [ToolUseBlock("submit_insights", {"insights": out})]


def did_insights(arts: dict[str, dict[str, Any]]) -> list[Any]:
    did = next(v for k, v in arts.items() if k.startswith("Difference-in-differences:"))
    d = did["data"]
    credible = d["pretrend_ok"]
    out = [
        {
            "title": "The loyalty program lifted weekly sales"
            if credible
            else "Effect estimate is not credible",
            "claim": f"Joining the loyalty program caused an estimated {_pct(d['relative_effect'])} increase in "
            f"weekly store sales (95% CI {_pct(d['relative_effect_ci_low'])} to "
            f"{_pct(d['relative_effect_ci_high'])}), across {d['n_treated_units']} program stores.",
            "evidence": [
                _ev(did, "relative_effect"),
                _ev(did, "relative_effect_ci_low"),
                _ev(did, "relative_effect_ci_high"),
                _ev(did, "n_treated_units"),
            ],
            "method": "Difference-in-differences with store and week fixed effects; standard errors clustered by store.",
            "caveats": [
                "This relies on parallel trends: without the program, program and non-program stores would "
                f"have moved together. The pre-launch trend test does not reject this (p = {d['pretrend_p_value']:.3f}).",
                "Stores were not randomly chosen for the program; differences that changed at launch would bias the estimate.",
            ],
            "recommended_action": "Expand the program to more stores and keep a holdout group to confirm the lift.",
            "confidence": "high" if credible else "low",
        },
        {
            "title": "Worth about two thousand dollars per store each week",
            "claim": f"In dollars, the program adds {_money(d['att'])} per store per week "
            f"(95% CI {_money(d['ci_low'])} to {_money(d['ci_high'])}), against a pre-launch average of "
            f"{_money(d['treated_pre_mean'])} for program stores.",
            "evidence": [
                _ev(did, "att"),
                _ev(did, "ci_low"),
                _ev(did, "ci_high"),
                _ev(did, "treated_pre_mean"),
            ],
            "method": "Same difference-in-differences model, effect in dollars.",
            "caveats": [f"Pre-trend test p = {d['pretrend_p_value']:.3f}; program costs are not netted out."],
            "recommended_action": "Compare this lift with the program's cost per store to judge ROI.",
            "confidence": "medium",
        },
    ]
    out[0]["evidence"].append(_ev(did, "pretrend_p_value"))
    out[1]["evidence"].append(_ev(did, "pretrend_p_value"))
    return [ToolUseBlock("submit_insights", {"insights": out})]


def forecast_insights(arts: dict[str, dict[str, Any]]) -> list[Any]:
    fc = next(v for k, v in arts.items() if k.startswith("Forecast:"))
    d = fc["data"]
    total, bt = d["horizon_total"], d["backtest"]
    out = [
        {
            "title": f"Expect about {total['forecast']:,.0f} orders over the next {d['horizon']} days",
            "claim": f"The forecast for the next {d['horizon']} days totals {total['forecast']:,.0f} orders "
            f"(95% interval {total['lower']:,.0f} to {total['upper']:,.0f}).",
            "evidence": [
                _ev(fc, "horizon"),
                _ev(fc, "horizon_total.forecast"),
                _ev(fc, "horizon_total.lower"),
                _ev(fc, "horizon_total.upper"),
            ],
            "method": d["model"] + " on daily totals; intervals from 1,000 simulated paths.",
            "caveats": ["The interval reflects past volatility; promotions or outages are not modeled."],
            "recommended_action": "Plan fulfillment capacity for the upper end of the interval.",
            "confidence": "medium",
        },
        {
            "title": "The model beats a seasonal-naive forecast",
            "claim": f"In a {bt['holdout_periods']}-day backtest the model's average error was {_pct(bt['mape'])}, "
            f"compared with {_pct(bt['seasonal_naive_mape'])} for repeating last week.",
            "evidence": [
                _ev(fc, "backtest.holdout_periods"),
                _ev(fc, "backtest.mape"),
                _ev(fc, "backtest.seasonal_naive_mape"),
            ],
            "method": "Holdout backtest on the most recent periods (mean absolute percentage error).",
            "caveats": ["One backtest window; accuracy can differ in peak season."],
            "recommended_action": "Re-run the forecast weekly as new data arrives.",
            "confidence": "high",
        },
    ]
    out[0]["evidence"].append(_ev(fc, "history_periods"))
    out[0]["caveats"][0] += f" Trained on {d['history_periods']} days of history."
    return [ToolUseBlock("submit_insights", {"insights": out})]


def model_insights(arts: dict[str, dict[str, Any]]) -> list[Any]:
    model = next(v for k, v in arts.items() if k.startswith("Model:"))
    d = model["data"]
    h = d["holdout"]
    top, second = d["drivers"][0], d["drivers"][1]
    out = [
        {
            "title": "The churn model ranks customers usefully",
            "claim": f"On held-out customers the model reaches an AUC of {h['auc']:.3f}, and its highest-scored "
            f"decile contains {_pct(h['top_decile_capture'])} of churners, a {h['top_decile_lift']:.1f}x lift.",
            "evidence": [
                _ev(model, "holdout.auc"),
                _ev(model, "holdout.top_decile_capture"),
                _ev(model, "holdout.top_decile_lift"),
            ],
            "method": f"{d['algorithm']}; evaluated on a stratified holdout of {d['n_test']:,} customers.",
            "caveats": [
                "Evaluated on a random split from the same period; check performance on next month's customers."
            ],
            "recommended_action": "Use the registered model to score active customers and target the top decile for retention.",
            "confidence": "high",
        },
        {
            "title": "A simple model is enough"
            if d["selected_model"] != "gradient-boosted trees"
            else "Trees beat the linear baseline",
            "claim": f"The {d['selected_model']} reached a holdout AUC of {h['auc']:.3f} versus "
            f"{d['tree_model_holdout']['auc']:.3f} for gradient-boosted trees, so the registry keeps the "
            f"{d['selected_model']}.",
            "evidence": [_ev(model, "holdout.auc"), _ev(model, "tree_model_holdout.auc")],
            "method": "Both models trained on the same split; the better holdout AUC is registered.",
            "caveats": [
                "The difference is small relative to holdout noise; either model would rank similarly."
            ],
            "recommended_action": "Prefer the simpler model for easier monitoring and explanation.",
            "confidence": "medium",
        },
        {
            "title": f"{top['feature']} and {second['feature']} drive the predictions",
            "claim": f"{top['feature']} accounts for {_pct(top['share_of_importance'])} of the model's attributions "
            f"and {second['feature']} for {_pct(second['share_of_importance'])}.",
            "evidence": [
                _ev(model, "drivers.0.share_of_importance"),
                _ev(model, "drivers.1.share_of_importance"),
            ],
            "method": "SHAP values on the holdout set (mean absolute contribution).",
            "caveats": ["Drivers describe how the model scores customers, not what causes churn."],
            "recommended_action": "Test re-engagement for low-activity, new customers before assuming it reduces churn.",
            "confidence": "medium",
        },
    ]
    return [ToolUseBlock("submit_insights", {"insights": out})]


def demo_script(req: Request) -> list[Any]:
    if req.role == "planning":
        return plan(req)
    if req.role == "insight":
        return insights(req)
    if "build_data_model" in req.tool_names:
        return modeler_agent(req)
    if "train_model" in req.tool_names:
        return ml_agent(req)
    if "power_analysis" in req.tool_names:
        return experiment_agent(req)
    if "profile_dataset" in req.tool_names and "group_summary" not in req.tool_names:
        return data_agent(req)
    return analyst_agent(req)
