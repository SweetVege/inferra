"""Predictive modeling: train, evaluate, explain, and register a model.

The trained model is serialized with its feature encoding, so batch scoring later reproduces the
exact preprocessing used in training."""

import json
import math
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from inferra.datasources.base import DataSource
from inferra.tools.base import (
    ArtifactDraft,
    ModelDraft,
    ToolContext,
    ToolError,
    ToolOutput,
    round_floats,
    tool,
)
from inferra.tools.experiment import _column
from inferra.tools.modeling import fetch, to_numeric
from inferra.tools.profiling import _table, column_kind

MAX_TRAIN_ROWS = 100_000
MAX_CATEGORY_LEVELS = 50
SEED = 42
GBM_PARAMS = {
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 40,
    "min_sum_hessian_in_leaf": 1.0,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "seed": SEED,
    "num_threads": 2,  # lowered to the CPUs this container may use; see _cpu_threads
}


def _cpu_threads(most: int = 2, cpu_max: str = "/sys/fs/cgroup/cpu.max") -> int:
    """Threads LightGBM may use without oversubscribing a CPU-limited container (a 0.1-CPU free
    instance runs far slower with two spinning OpenMP threads than with one)."""
    import os

    cpus = float(len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1)
    try:
        with open(cpu_max) as fh:
            quota, period = fh.read().split()[:2]
        if quota != "max":
            cpus = min(cpus, int(quota) / int(period))
    except (OSError, ValueError):
        pass
    return max(1, min(most, int(cpus)))


GBM_PARAMS["num_threads"] = _cpu_threads()
MAX_ROUNDS = 2000
EARLY_STOP = 100


# --------------------------------------------------------------------------------------------
# Feature encoding shared by training and scoring
# --------------------------------------------------------------------------------------------


def encode(frame: pd.DataFrame, spec: list[dict[str, Any]]) -> np.ndarray:
    """Turn raw columns into the model's numeric matrix. Categoricals become integer codes over the
    training levels (unknown levels become missing), which LightGBM treats as categorical."""
    cols = []
    for f in spec:
        s = frame[f["name"]]
        if f["kind"] == "categorical":
            lookup = {v: i for i, v in enumerate(f["levels"])}
            cols.append(s.astype("string").map(lookup).astype(float).to_numpy())
        else:
            cols.append(to_numeric(s).astype(float).to_numpy())
    return np.column_stack(cols) if cols else np.empty((len(frame), 0))


def linear_design(X: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    """Median-imputed, standardized numerics plus one-hot categoricals (unknown levels -> all zeros)."""
    cols = []
    for col in params["columns"]:
        x = X[:, col["index"]]
        if col["kind"] == "numeric":
            cols.append((np.where(np.isnan(x), col["median"], x) - col["mean"]) / col["scale"])
        else:
            cols.append((x == col["level"]).astype(float))
    return np.column_stack(cols)


def fit_linear(
    X: np.ndarray, y: np.ndarray, spec: list[dict[str, Any]], classification: bool
) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression, Ridge

    columns: list[dict[str, Any]] = []
    for i, f in enumerate(spec):
        if f["kind"] == "numeric":
            x = X[:, i]
            median = float(np.nanmedian(x)) if np.isfinite(x).any() else 0.0
            filled = np.where(np.isnan(x), median, x)
            columns.append(
                {
                    "index": i,
                    "kind": "numeric",
                    "median": median,
                    "mean": float(filled.mean()),
                    "scale": float(filled.std()) or 1.0,
                }
            )
        else:
            columns += [{"index": i, "kind": "level", "level": float(k)} for k in range(len(f["levels"]))]
    params: dict[str, Any] = {"columns": columns}
    Z = linear_design(X, params)
    model = LogisticRegression(max_iter=2000) if classification else Ridge(alpha=1.0)
    model.fit(Z, y)
    params.update(
        coef=np.ravel(model.coef_).tolist(),
        intercept=float(np.ravel(model.intercept_)[0]),
        logistic=classification,
        z_mean=Z.mean(axis=0).tolist(),
    )
    return params


def linear_predict(X: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    eta = linear_design(X, params) @ np.array(params["coef"]) + params["intercept"]
    return 1 / (1 + np.exp(-eta)) if params["logistic"] else eta


def predict(payload: str, frame: pd.DataFrame) -> np.ndarray:
    """Score raw rows with a registered model, reproducing its training-time encoding."""
    data = json.loads(payload)
    X = encode(frame, data["spec"])
    if data["type"] == "linear":
        return linear_predict(X, data["linear"])
    import lightgbm as lgb

    return lgb.Booster(model_str=data["booster"]).predict(X)


# --------------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------------


def classification_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import (
        average_precision_score,
        brier_score_loss,
        log_loss,
        precision_recall_curve,
        roc_auc_score,
    )

    precision, recall, thresholds = precision_recall_curve(y, p)
    f1 = 2 * precision * recall / np.clip(precision + recall, 1e-12, None)
    best = int(np.argmax(f1[:-1])) if len(thresholds) else 0
    threshold = float(thresholds[best]) if len(thresholds) else 0.5
    pred = (p >= threshold).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    top = np.argsort(-p)[: max(1, len(p) // 10)]
    capture = float(y[top].sum() / max(1, y.sum()))
    return {
        "auc": float(roc_auc_score(y, p)),
        "average_precision": float(average_precision_score(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))),
        "brier": float(brier_score_loss(y, p)),
        "positive_rate": float(y.mean()),
        "threshold": threshold,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "f1": float(f1[best]) if len(thresholds) else 0.0,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "top_decile_capture": capture,
        "top_decile_lift": capture / 0.1,
    }


def regression_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import mean_absolute_error, r2_score

    return {
        "rmse": float(math.sqrt(np.mean((y - p) ** 2))),
        "mae": float(mean_absolute_error(y, p)),
        "r2": float(r2_score(y, p)),
    }


# --------------------------------------------------------------------------------------------
# Tool
# --------------------------------------------------------------------------------------------


class TrainInput(BaseModel):
    table: str
    target: str
    features: list[str] | None = Field(
        default=None,
        description="Columns to use. Omit to use every column except the target, IDs, and dates.",
    )
    task: Literal["auto", "classification", "regression"] = "auto"
    model_name: str | None = Field(default=None, description="Name for the model registry.")
    test_size: float = Field(default=0.2, ge=0.1, le=0.4)
    where: str | None = None


def _screen(
    source: DataSource,
    table: str,
    frame: pd.DataFrame,
    y: pd.Series,
    candidates: list[str],
    classification: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Choose features and record why others were excluded (dates, identifiers, leakage)."""
    from sklearn.metrics import roc_auc_score

    spec, excluded = [], []
    for name in candidates:
        kind = column_kind(_column(source, table, name)["type"])
        s = frame[name]
        if kind == "temporal":
            excluded.append({"feature": name, "reason": "date column; derive durations instead"})
            continue
        numeric = to_numeric(s)
        is_cat = kind in {"categorical", "other"} and numeric.notna().sum() < s.notna().sum() * 0.99
        if is_cat:
            distinct = s.nunique(dropna=True)
            if distinct > 0.9 * s.notna().sum():
                excluded.append({"feature": name, "reason": "identifier-like (almost every value is unique)"})
                continue
            if distinct > MAX_CATEGORY_LEVELS:
                excluded.append({"feature": name, "reason": f"more than {MAX_CATEGORY_LEVELS} categories"})
                continue
            levels = list(s.astype("string").dropna().value_counts().index)
            signal = s.astype("string").map(y.groupby(s.astype("string")).mean()).astype(float)
        else:
            if numeric.notna().sum() == 0 or numeric.nunique() <= 1:
                excluded.append({"feature": name, "reason": "constant or empty"})
                continue
            levels, signal = [], numeric
        mask = signal.notna() & y.notna()
        if mask.sum() > 50:
            if classification and y[mask].nunique() == 2:
                auc = roc_auc_score(y[mask], signal[mask])
                strength = max(auc, 1 - auc)
            else:
                strength = abs(np.corrcoef(signal[mask], y[mask])[0, 1])
            if strength > 0.98:
                excluded.append(
                    {"feature": name, "reason": f"possible target leakage (univariate {strength:.3f})"}
                )
                continue
        spec.append({"name": name, "kind": "categorical" if is_cat else "numeric", "levels": levels})
    return spec, excluded


@tool(
    "train_model",
    "Train and evaluate a predictive model for a binary or numeric target. Screens features (drops "
    "dates, identifiers, and likely target leakage), trains a gradient-boosted tree model and a linear "
    "baseline, reports 5-fold cross-validation and held-out test metrics against the baseline and a naive "
    "predictor, explains predictions with SHAP values, writes a model card, and registers the model for "
    "batch scoring. Creates a 'model' artifact.",
    TrainInput,
)
def train_model(ctx: ToolContext, args: TrainInput) -> ToolOutput:
    import lightgbm as lgb
    from sklearn.model_selection import KFold, StratifiedKFold, train_test_split

    src = ctx.source
    info = _table(src, args.table)
    _column(src, args.table, args.target)
    candidates = args.features or [c["name"] for c in info.columns if c["name"] != args.target]
    candidates = [c for c in candidates if c != args.target]
    frame, sql, sampled = fetch(src, args.table, [args.target, *candidates], args.where, MAX_TRAIN_ROWS)
    y_all = to_numeric(frame[args.target])
    keep = y_all.notna()
    frame, y_all = frame[keep].reset_index(drop=True), y_all[keep].reset_index(drop=True)
    binary = set(y_all.unique()) <= {0.0, 1.0} and y_all.nunique() == 2
    task = args.task if args.task != "auto" else ("classification" if binary else "regression")
    if task == "classification" and not binary:
        raise ToolError("Classification needs a target with exactly two values (0/1 or true/false).")
    if len(frame) < 200:
        raise ToolError("At least 200 labeled rows are needed to train and evaluate a model.")
    classification = task == "classification"
    if classification and min(y_all.sum(), len(y_all) - y_all.sum()) < 30:
        raise ToolError("The rarer class has fewer than 30 examples; there is not enough signal to model.")

    spec, excluded = _screen(src, args.table, frame, y_all, candidates, classification)
    if not spec:
        raise ToolError("No usable features remain after screening: " + json.dumps(excluded))
    X = encode(frame, spec)
    y = y_all.to_numpy()
    cat_idx = [i for i, f in enumerate(spec) if f["kind"] == "categorical"]
    names = [f["name"] for f in spec]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=SEED, stratify=y if classification else None
    )
    params = {**GBM_PARAMS, "objective": "binary" if classification else "regression"}

    def fit(Xf: np.ndarray, yf: np.ndarray) -> Any:
        """Pick the number of rounds by early stopping on an inner validation split, then refit."""
        X_fit, X_val, y_fit, y_val = train_test_split(
            Xf, yf, test_size=0.2, random_state=SEED, stratify=yf if classification else None
        )
        ds = lgb.Dataset(X_fit, y_fit, feature_name=names, categorical_feature=cat_idx, free_raw_data=False)
        val = lgb.Dataset(X_val, y_val, reference=ds)
        probe = lgb.train(
            params,
            ds,
            num_boost_round=MAX_ROUNDS,
            valid_sets=[val],
            callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)],
        )
        rounds = max(20, probe.best_iteration or MAX_ROUNDS)
        full = lgb.Dataset(Xf, yf, feature_name=names, categorical_feature=cat_idx, free_raw_data=False)
        return lgb.train(params, full, num_boost_round=rounds)

    score = classification_metrics if classification else regression_metrics
    headline = "auc" if classification else "rmse"
    folds = (StratifiedKFold if classification else KFold)(n_splits=5, shuffle=True, random_state=SEED)
    tree_cv, linear_cv = [], []
    for tr, va in folds.split(X_train, y_train):
        tree_cv.append(score(y_train[va], fit(X_train[tr], y_train[tr]).predict(X_train[va]))[headline])
        fold_linear = fit_linear(X_train[tr], y_train[tr], spec, classification)
        linear_cv.append(score(y_train[va], linear_predict(X_train[va], fold_linear))[headline])
    booster = fit(X_train, y_train)
    p_test = booster.predict(X_test)
    holdout = tree_holdout = score(y_test, p_test)
    train_metrics = score(y_train, booster.predict(X_train))
    linear = fit_linear(X_train, y_train, spec, classification)
    p_linear = linear_predict(X_test, linear)
    linear_metrics = score(y_test, p_linear)
    baseline = {
        "model": "logistic regression" if classification else "ridge regression",
        **{
            k: linear_metrics[k]
            for k in (("auc", "average_precision", "brier") if classification else ("rmse", "r2"))
        },
    }
    naive = (
        {"auc": 0.5, "brier": float(np.mean((y_test - y_train.mean()) ** 2))}
        if classification
        else {"rmse": float(math.sqrt(np.mean((y_test - y_train.mean()) ** 2)))}
    )
    # Prefer the simpler model unless the tree model is better on the holdout.
    tree_wins = holdout["auc"] > baseline["auc"] if classification else holdout["rmse"] < baseline["rmse"]
    if not tree_wins:
        p_test, holdout = p_linear, linear_metrics
        train_metrics = score(y_train, linear_predict(X_train, linear))
    selected = "gradient-boosted trees" if tree_wins else baseline["model"]
    cv_scores = tree_cv if tree_wins else linear_cv
    drivers = _drivers(booster, X_test, spec) if tree_wins else _linear_drivers(linear, X_test, spec)
    calibration = []
    if classification:
        bins = pd.qcut(p_test, q=10, duplicates="drop")
        cal = pd.DataFrame({"p": p_test, "y": y_test, "bin": bins}).groupby("bin", observed=True)
        calibration = [
            {"predicted": float(g.p.mean()), "observed": float(g.y.mean()), "n": len(g)} for _, g in cal
        ]

    name = args.model_name or f"{args.target} {'classifier' if classification else 'regressor'}"
    limitations = [
        "Feature effects (SHAP) describe how the model uses each input, not causal effects.",
        "Performance is measured on a random holdout from the same period; monitor it on new data.",
    ]
    if excluded:
        limitations.append(
            "Excluded inputs: " + "; ".join(f"{e['feature']} ({e['reason']})" for e in excluded)
        )
    if sampled:
        limitations.append(f"Trained on a reproducible sample of {MAX_TRAIN_ROWS:,} rows.")
    if classification and y.mean() < 0.1:
        limitations.append(
            "The positive class is rare; judge the model by average precision and lift, not accuracy."
        )
    algorithm = (
        f"LightGBM gradient-boosted trees ({booster.current_iteration()} rounds, early-stopped)"
        if tree_wins
        else f"{baseline['model']} (chosen over gradient-boosted trees, which did not beat it on the holdout)"
    )
    card = {
        "name": name,
        "task": task,
        "target": args.target,
        "algorithm": algorithm,
        "training_data": {
            "table": args.table,
            "rows": len(frame),
            "train_rows": len(y_train),
            "test_rows": len(y_test),
            "sampled": sampled,
        },
        "features": names,
        "excluded_features": excluded,
        "top_drivers": [d["feature"] for d in drivers[:5]],
        "intended_use": f"Rank or estimate {args.target} for records shaped like {args.table}.",
        "limitations": limitations,
    }
    result = round_floats(
        {
            "design": "predictive",
            "task": task,
            "table": args.table,
            "target": args.target,
            "model_name": name,
            "algorithm": card["algorithm"],
            "n_train": len(y_train),
            "n_test": len(y_test),
            "features": [{"name": f["name"], "kind": f["kind"], "levels": len(f["levels"])} for f in spec],
            "excluded_features": excluded,
            "cv": {
                "metric": headline,
                "mean": float(np.mean(cv_scores)),
                "std": float(np.std(cv_scores)),
                "folds": [float(v) for v in cv_scores],
            },
            "holdout": holdout,
            "baseline": baseline,
            "naive": naive,
            "train_metrics": {headline: train_metrics[headline]},
            "selected_model": selected,
            "tree_model_cv_mean": float(np.mean(tree_cv)),
            "tree_model_holdout": tree_holdout,
            "drivers": drivers,
            "calibration": calibration,
            "card": card,
        },
        6,
    )
    payload = json.dumps(
        {"type": "gbm", "spec": spec, "task": task, "booster": booster.model_to_string()}
        if tree_wins
        else {"type": "linear", "spec": spec, "task": task, "linear": linear}
    )
    metrics = {"cv": result["cv"], "holdout": result["holdout"], "baseline": result["baseline"]}
    return ToolOutput(
        result=result,  # all ten calibration bins: the high-score bins are where a churn model is used
        artifact=ArtifactDraft("model", f"Model: {name}", result),
        code=sql,
        model=ModelDraft(
            name=name,
            task=task,
            target=args.target,
            table=args.table,
            features=names,
            metrics=metrics,
            card=card,
            payload=payload,
        ),
    )


def _drivers(booster: Any, X_test: np.ndarray, spec: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Global importance and direction from TreeSHAP contributions on the holdout set."""
    contrib = booster.predict(X_test, pred_contrib=True)[:, :-1]
    drivers = []
    for i, f in enumerate(spec):
        values = X_test[:, i]
        mask = ~np.isnan(values)
        entry: dict[str, Any] = {"feature": f["name"], "mean_abs_shap": float(np.mean(np.abs(contrib[:, i])))}
        if f["kind"] == "numeric" and mask.sum() > 2 and np.std(values[mask]) > 0:
            rho = pd.Series(values[mask]).corr(pd.Series(contrib[mask, i]), method="spearman")
            entry["direction"] = (
                "higher raises prediction"
                if rho > 0.1
                else "higher lowers prediction"
                if rho < -0.1
                else "mixed"
            )
        elif f["kind"] == "categorical" and mask.any():
            by_level = pd.Series(contrib[mask, i]).groupby(values[mask].astype(int)).mean()
            entry["raises_most"] = f["levels"][int(by_level.idxmax())]
            entry["lowers_most"] = f["levels"][int(by_level.idxmin())]
        drivers.append(entry)
    total = sum(d["mean_abs_shap"] for d in drivers) or 1.0
    for d in drivers:
        d["share_of_importance"] = d["mean_abs_shap"] / total
    return sorted(drivers, key=lambda d: -d["mean_abs_shap"])


def _linear_drivers(
    params: dict[str, Any], X_test: np.ndarray, spec: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Exact SHAP values for a linear model: coefficient times the centered (standardized) input."""
    Z = linear_design(X_test, params)
    contrib_cols = (Z - np.array(params["z_mean"])) * np.array(params["coef"])
    drivers = []
    for i, f in enumerate(spec):
        idx = [j for j, c in enumerate(params["columns"]) if c["index"] == i]
        contrib = contrib_cols[:, idx].sum(axis=1)
        entry: dict[str, Any] = {"feature": f["name"], "mean_abs_shap": float(np.mean(np.abs(contrib)))}
        if f["kind"] == "numeric":
            coef = params["coef"][idx[0]]
            entry["direction"] = "higher raises prediction" if coef > 0 else "higher lowers prediction"
        else:
            levels = {int(params["columns"][j]["level"]): params["coef"][j] for j in idx}
            entry["raises_most"] = f["levels"][max(levels, key=levels.get)]
            entry["lowers_most"] = f["levels"][min(levels, key=levels.get)]
        drivers.append(entry)
    total = sum(d["mean_abs_shap"] for d in drivers) or 1.0
    for d in drivers:
        d["share_of_importance"] = d["mean_abs_shap"] / total
    return sorted(drivers, key=lambda d: -d["mean_abs_shap"])
