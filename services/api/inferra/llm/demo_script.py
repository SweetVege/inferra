"""A deterministic stand-in for the model on the demo orders dataset.

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


def plan(req: Request) -> list[Any]:
    ids = re.findall(r'"id": "(\w+)"', req.first_user_text)
    return [
        TextBlock("The user wants an overview of the orders data, which is an exploration."),
        ToolUseBlock(
            "submit_plan",
            {
                "intent": "explore",
                "dataset_ids": ids[:1],
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


def demo_script(req: Request) -> list[Any]:
    if req.role == "planning":
        return plan(req)
    if req.role == "insight":
        return insights(req)
    if "profile_dataset" in req.tool_names and "group_summary" not in req.tool_names:
        return data_agent(req)
    return analyst_agent(req)
