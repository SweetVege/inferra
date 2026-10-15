"""Deterministic checks on every insight an agent drafts.

Language models write the narrative; this module makes sure the narrative is grounded in values
the tools actually computed and follows basic statistical hygiene."""

import math
import re
from dataclasses import dataclass, field
from typing import Any

NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-−])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suffix>\s?%|\s?pp\b|[kKMB]\b|x\b|×)?"
)
CAUSAL = re.compile(
    r"(?<![\w-])(caus(?:e|es|ed|ing)|drove|drives|driven by|leads? to|led to|results? in|resulted in|"
    r"because of|impact(?:s|ed)? on|effect of|increased? by doing|boost(?:s|ed)?)\b",
    re.I,
)
CAUSAL_DESIGNS = {"randomized", "causal"}
# Statistical conventions, not measurements: "95% CI", "90% credible interval", "p < 0.001".
CONVENTIONS = re.compile(
    r"\b(?:80|90|95|99)\s?%\s?(?:CI|confidence|credible|prediction|interval)|\bp\s?[<≤]\s?0?\.0*[15]\b",
    re.I,
)
SAMPLE_KEYS = {"n", "count", "row_count", "sample_size"}
MIN_SAMPLE = 30


@dataclass
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class Verdict:
    checks: list[Check] = field(default_factory=list)
    resolved_evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return all(c.passed for c in self.checks)

    def failures(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]


def resolve_path(data: Any, path: str) -> Any:
    """Follow 'columns.age.mean' or 'rows[2].revenue' into nested JSON."""
    parts = [p for p in re.split(r"\.|\[|\]", path) if p != ""]
    node = data
    for part in parts:
        if isinstance(node, list):
            if not part.lstrip("-").isdigit():
                raise KeyError(path)
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                raise KeyError(path)
            node = node[part]
        else:
            raise KeyError(path)
    return node


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _matches_display(value: float, shown: float, decimals: int) -> bool:
    tolerance = 0.5 * 10 ** (-decimals) + 1e-9
    return abs(value - shown) <= tolerance


def _number_tokens(text: str) -> list[tuple[str, float, int, str]]:
    tokens = []
    for m in NUMBER.finditer(text):
        raw = m.group("num")
        value = float(raw.replace(",", ""))
        if m.group("sign"):
            value = -value
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        suffix = (m.group("suffix") or "").strip()
        tokens.append((m.group(0).strip(), value, decimals, suffix))
    return tokens


def _grounded(value: float, decimals: int, suffix: str, numbers: list[float]) -> bool:
    for v in numbers:
        candidates = [v, abs(v)]
        if suffix in {"%", "pp"}:
            candidates += [v * 100, abs(v) * 100]
        elif suffix == "k":
            candidates.append(v / 1e3)
        elif suffix in {"M"}:
            candidates.append(v / 1e6)
        elif suffix == "B":
            candidates.append(v / 1e9)
        if any(
            _matches_display(c, abs(value), decimals) or _matches_display(c, value, decimals)
            for c in candidates
        ):
            return True
    return False


def verify_insight(
    draft: dict[str, Any], artifacts: dict[str, dict[str, Any]], context_text: str = ""
) -> Verdict:
    """`artifacts` maps artifact id -> {"kind", "data"}; `context_text` holds the goal and table
    and column names, whose numbers may be repeated without a citation."""
    verdict = Verdict()

    # 1. Evidence: each citation must resolve, and the quoted value must match the artifact.
    evidence_numbers: list[float] = []
    evidence_problems: list[str] = []
    cited_kinds: set[str] = set()
    cited_designs: set[str] = set()
    sample_sizes: list[float] = []
    for ev in draft.get("evidence", []):
        art = artifacts.get(ev.get("artifact_id", ""))
        if art is None:
            evidence_problems.append(f"unknown artifact '{ev.get('artifact_id')}'")
            continue
        cited_kinds.add(art["kind"])
        if isinstance(art["data"], dict) and art["data"].get("design"):
            cited_designs.add(str(art["data"]["design"]))
        try:
            actual = resolve_path(art["data"], ev.get("path", ""))
        except (KeyError, IndexError, ValueError):
            evidence_problems.append(f"path '{ev.get('path')}' not found in {ev.get('artifact_id')}")
            continue
        quoted = ev.get("value")
        if _is_number(actual):
            if not _is_number(quoted) or not math.isclose(
                float(quoted), float(actual), rel_tol=1e-4, abs_tol=1e-6
            ):
                evidence_problems.append(f"{ev.get('path')} is {actual}, but the insight quotes {quoted}")
            evidence_numbers.append(float(actual))
            last_key = re.split(r"\.|\[|\]", ev.get("path", ""))[-1] or ""
            if last_key in SAMPLE_KEYS:
                sample_sizes.append(float(actual))
        elif str(quoted) != str(actual):
            evidence_problems.append(f"{ev.get('path')} is '{actual}', not '{quoted}'")
        verdict.resolved_evidence.append({**ev, "value": actual, "kind": art["kind"]})
    if not draft.get("evidence"):
        evidence_problems.append("no evidence cited")
    verdict.checks.append(
        Check(
            "evidence",
            not evidence_problems,
            "; ".join(evidence_problems) or "All citations resolve to artifact values.",
        )
    )

    # 2. Number grounding: every number in the prose must come from cited evidence or context.
    prose = " ".join(
        [
            draft.get("title", ""),
            draft.get("claim", ""),
            draft.get("recommended_action") or "",
            *draft.get("caveats", []),
        ]
    )
    # "p < 0.001" is a convention, but it still has to be true of a cited p-value.
    thresholds = [float(m) for m in re.findall(r"\bp\s?[<≤]\s?(0?\.0*[15])\b", prose, re.I)]
    cited_p = [
        float(ev["value"])
        for ev in verdict.resolved_evidence
        if _is_number(ev["value"]) and (_parts(ev["path"])[-1:] or [""])[0].endswith("p_value")
    ]
    false_thresholds = [f"p < {t:g}" for t in thresholds if not any(p < t for p in cited_p)]
    prose = CONVENTIONS.sub(" ", prose)
    context_numbers = [t[1] for t in _number_tokens(context_text)]
    # Labels inside cited results (version "5.2", date "2026-09-21", segment "Q3") may be repeated.
    for artifact_id in {ev["artifact_id"] for ev in verdict.resolved_evidence}:
        for label in _labels(artifacts[artifact_id]["data"]):
            context_numbers += [t[1] for t in _number_tokens(label)]
    for ev in verdict.resolved_evidence:
        if isinstance(ev["value"], str):
            context_numbers += [t[1] for t in _number_tokens(ev["value"])]
    ungrounded = [
        raw
        for raw, value, decimals, suffix in _number_tokens(prose)
        if not _grounded(value, decimals, suffix, evidence_numbers)
        and not _grounded(value, decimals, "", context_numbers)
    ]
    ungrounded += false_thresholds
    verdict.checks.append(
        Check(
            "number_grounding",
            not ungrounded,
            f"Numbers without a matching citation: {', '.join(ungrounded)}"
            if ungrounded
            else "Every number matches a cited value.",
        )
    )

    # 3. Causal language is reserved for randomized experiments and explicit causal methods.
    # Caveats are exempt: "this does not show the cause" is exactly what they should say.
    assertions = " ".join(
        [draft.get("title", ""), draft.get("claim", ""), draft.get("recommended_action") or ""]
    )
    causal_terms = sorted({m.group(0).lower() for m in CAUSAL.finditer(assertions)})
    causal_ok = not causal_terms or bool(cited_designs & CAUSAL_DESIGNS)
    verdict.checks.append(
        Check(
            "causal_language",
            causal_ok,
            "Causal wording without a randomized or causal design: " + ", ".join(causal_terms)
            if not causal_ok
            else "Wording matches the strength of the evidence.",
        )
    )

    # 4. Small samples cannot support high-confidence claims.
    small = [n for n in sample_sizes if n < MIN_SAMPLE]
    sample_ok = not (small and draft.get("confidence") == "high")
    verdict.checks.append(
        Check(
            "sample_size",
            sample_ok,
            f"High confidence with only {int(min(small))} observations."
            if not sample_ok
            else "Confidence is consistent with the sample sizes cited.",
        )
    )

    cited = [(ev, artifacts[ev["artifact_id"]]) for ev in verdict.resolved_evidence]
    estimates_cited = [(ev, art) for ev, art in cited if art["kind"] in ESTIMATE_KINDS]

    # 5. Estimates need their uncertainty: a p-value, effect, or forecast cites its interval.
    if estimates_cited:
        missing = []
        for ev, _art in estimates_cited:
            parts = _parts(ev["path"])
            needs = NEEDS_INTERVAL.get(parts[-1]) if parts else None
            if needs is None:
                continue
            prefix, key = parts[:-1], parts[-1]
            has_interval = any(
                other["artifact_id"] == ev["artifact_id"]
                and _parts(other["path"])[:-1] == prefix
                and _interval_matches(key, _parts(other["path"])[-1], needs)
                for other in verdict.resolved_evidence
                if _parts(other["path"])
            )
            if not has_interval:
                missing.append(ev["path"])
        verdict.checks.append(
            Check(
                "interval_reported",
                not missing,
                "Cite the interval alongside: " + ", ".join(missing)
                if missing
                else "Estimates are reported with their intervals.",
            )
        )

    # 6. Observational causal claims must name the assumption they rest on.
    causal_methods = [
        art for _ev, art in cited if art["data"].get("assumption_keywords") if isinstance(art["data"], dict)
    ]
    if causal_terms and causal_methods:
        keywords = {k for art in causal_methods for k in art["data"]["assumption_keywords"]}
        caveats = " ".join(draft.get("caveats", [])).lower()
        stated = any(k.lower() in caveats for k in keywords)
        verdict.checks.append(
            Check(
                "assumptions_stated",
                stated,
                "Causal wording from an observational method needs a caveat naming its assumption: "
                + ", ".join(sorted(keywords))
                if not stated
                else "The identifying assumption is stated.",
            )
        )

    # 7. Model performance comes from held-out data.
    train_paths = [
        ev["path"]
        for ev, art in cited
        if art["kind"] == "model" and _parts(ev["path"])[:1] == ["train_metrics"]
    ]
    if any(art["kind"] == "model" for _ev, art in cited):
        verdict.checks.append(
            Check(
                "holdout_metrics",
                not train_paths,
                "Cite holdout or cross-validation metrics instead of training metrics: "
                + ", ".join(train_paths)
                if train_paths
                else "Model performance is reported on held-out data.",
            )
        )

    experiment_cited = [(ev, art) for ev, art in cited if art["kind"] in EXPERIMENT_KINDS]
    if not experiment_cited:
        return verdict

    # 8. Segment results must use multiplicity-adjusted p-values.
    raw_segment_p = [
        ev["path"]
        for ev, art in experiment_cited
        if art["kind"] == "segment_effects" and _parts(ev["path"])[-1:] == ["p_value"]
    ]
    verdict.checks.append(
        Check(
            "multiple_comparisons",
            not raw_segment_p,
            "Use p_value_adjusted for segment results instead of: " + ", ".join(raw_segment_p)
            if raw_segment_p
            else "Comparisons across segments are corrected for multiplicity.",
        )
    )

    # 9. A failed sample ratio check invalidates effect estimates.
    srm_failed = any(
        isinstance(art["data"], dict) and art["data"].get("srm_ok") is False for _ev, art in experiment_cited
    )
    disclosed = draft.get("confidence") == "low" and any(
        re.search(r"sample ratio|\bsrm\b|assignment", c, re.I) for c in draft.get("caveats", [])
    )
    if not srm_failed:
        srm_detail = "Assignment counts match the intended split."
    elif disclosed:
        srm_detail = "Sample ratio mismatch is disclosed."
    else:
        srm_detail = (
            "The cited test failed its sample ratio check; mark the insight low confidence and "
            "explain the broken assignment in a caveat."
        )
    verdict.checks.append(Check("sample_ratio", not srm_failed or disclosed, srm_detail))
    return verdict


EXPERIMENT_KINDS = {"ab_test", "cuped", "segment_effects", "bayesian", "sequential", "srm"}
ESTIMATE_KINDS = EXPERIMENT_KINDS | {"did", "psm", "regression", "forecast"}
# Which cited keys need an interval, and which sibling keys count as that interval.
NEEDS_INTERVAL = {
    "p_value": "ci",
    "p_value_adjusted": "ci",
    "att": "ci",
    "estimate": "ci",
    "relative_effect": "ci",
    "odds_ratio": "ci",
    "forecast": "bounds",
}


def _interval_matches(key: str, other: str, needs: str) -> bool:
    if needs == "bounds":
        return other in {"lower", "upper"}
    if key in {"relative_effect", "odds_ratio"}:
        return other.startswith(f"{key}_ci_")
    return other.startswith("ci_") or "_ci_" in other


LABEL_EXCLUDED_KEYS = {"sql", "notes", "method", "model", "algorithm", "description", "detail", "reason"}


def _labels(data: Any, key: str = "") -> list[str]:
    """String values in an artifact (category labels, dates), skipping free text such as SQL."""
    if key in LABEL_EXCLUDED_KEYS:
        return []
    if isinstance(data, str):
        return [data] if len(data) <= 60 else []
    if isinstance(data, dict):
        return [s for k, v in data.items() for s in _labels(v, str(k))]
    if isinstance(data, list):
        return [s for v in data for s in _labels(v, key)]
    return []


def _parts(path: str) -> list[str]:
    return [p for p in re.split(r"\.|\[|\]", path or "") if p]
