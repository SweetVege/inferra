# Inferra — Product Requirements

## 1. Summary

Inferra is an AI-native data science workbench. A user states a goal in plain English, such as *"Did the new checkout page improve conversion?"* or *"Which customers are likely to churn next month?"*. A team of specialist agents then plans and runs the work an analyst or data scientist would do. Every result comes back as an **insight with evidence** that a human reviews before it is shared.

**Positioning:** a portfolio-grade product built to be genuinely usable. It has real authentication, real data handling, and reproducible results, plus a demo workspace that shows the whole flow in one click.

## 2. Problem

Data work is split across many tools: SQL clients, notebooks, BI dashboards, experimentation platforms, and ML tooling. Stakeholders wait days for answers to questions that follow well-known patterns. Generic AI chat assistants answer quickly, but they invent numbers, skip statistical checks, and leave no audit trail.

## 3. Target users

| Persona | Needs |
|---|---|
| **Product / business stakeholder** | Ask a question in plain English and get a trustworthy answer with clear caveats |
| **Data analyst** | Hand off routine analysis, review AI output quickly, and keep control of the method |
| **Data scientist** | Rigorous experiment analysis, statistical and ML modeling with explainability, and reproducible work |
| **Analytics engineer** | Turn raw tables into a clean analytics model with tests and lineage |

## 4. Product principles

1. **AI does the work; humans approve it.** The default flow is autonomous, with three checkpoints: clarification, plan approval, and insight review.
2. **Glass box, not black box.** Every number links to the SQL/code and data that produced it.
3. **Rigor by default.** Statistical checks are built into workflows, not left to the user to remember.
4. **One workflow per question type.** Predictable, well-tested workflows beat open-ended improvisation.

## 5. Core experience

1. **Ask.** The user types a goal and optionally picks datasets. Inferra infers the intent, resolves the relevant tables and columns, and asks a clarifying question only when it has to.
2. **Plan.** Inferra shows a short, readable plan: steps, owning agent, and key parameters such as primary metric or model target. The user approves or edits it. Auto-approve is an option.
3. **Run.** A live timeline shows each agent's steps, tool calls, intermediate tables, and charts.
4. **Review.** Inferra presents a set of insights. Each one has a claim, evidence, method, confidence, caveats, and a recommended action, plus a verification badge. The user accepts, edits, or rejects each insight.
5. **Share.** Accepted insights become a report with charts and an appendix of the method, shareable by link.

## 6. Functional scope

### 6.1 Natural-language goals and agent workflows
- Intent classification across: explore, metric query, diagnose, experiment design, experiment analysis, effect estimation, predict, forecast, data modeling.
- Dataset and column resolution with confidence; clarifying questions when confidence is low.
- Editable plan before execution; re-run from any step.
- Run history with full tool-call logs.

### 6.2 Data Hub
- Upload CSV, Parquet, and XLSX (MVP); connect Postgres (Phase 3), then BigQuery and Snowflake (Phase 4).
- Automatic profiling: schema, types, null rates, distributions, cardinality, duplicates, outliers.
- AI data-quality findings, for example *"`order_date` has 3% future dates"*.

### 6.3 Analysis
- Metric computation, segmentation, funnels, retention and cohorts.
- Metric-change decomposition ("why did X move?") across dimensions.
- Metric definitions (semantic layer) reused across runs.

### 6.4 Experimentation
- **Design:** baseline estimation, power analysis, sample size, MDE, and estimated duration.
- **Analysis:** SRM check, frequentist tests (proportions and means, Welch), CUPED, sequential testing, Bayesian analysis, guardrail metrics, segment analysis with multiple-comparison correction.
- Automatically generated experiment readouts, plus an experiment registry.

### 6.5 Modeling Studio
- **Statistical modeling:** linear and logistic regression with diagnostics, difference-in-differences, propensity score matching, time-series forecasting with prediction intervals.
- **Machine learning:** feature engineering suggestions, baseline and gradient-boosting models, cross-validation, evaluation (AUC, PR, calibration, confusion matrix, regression metrics), SHAP explanations, model cards, batch scoring, and a model registry.

### 6.6 Data Modeling (dimensional)
- Key and relationship discovery across tables.
- AI-proposed star schema (facts and dimensions) with SQL transformations.
- Data tests (uniqueness, not-null, referential integrity, accepted values) and lineage graph.
- Export as dbt-compatible SQL models.

### 6.7 Insights and reports
- Structured insights with deterministic verification: number grounding, statistical hygiene, causal-language rules.
- Review workflow: Draft → Accepted / Edited / Rejected, with reviewer audit trail.
- Insight feed across runs; shareable reports.

### 6.8 Platform
- GitHub sign-in, workspaces, roles (Owner, Editor, Viewer).
- Public read-only demo workspace with seeded datasets and completed runs.
- Usage limits and per-workspace LLM budget.

## 7. Roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **0. Foundation** (done) | Monorepo, FastAPI + Next.js, auth, workspaces, file upload → Parquet + DuckDB, `DataSource` abstraction, job queue, LLM gateway, agent runtime skeleton (Orchestrator + DAG executor), Insight model + Verifier skeleton, CI, deploy | A trivial goal runs end to end and produces a verified insight in the UI |
| **1. MVP** | NL goals for `explore`, `experiment_design`, and `experiment_analysis`; Data Hub profiling; Experimentation tools; Insight review; demo workspace | A user uploads an A/B dataset, asks "Did variant B win?", and gets a reviewed readout |
| **2. Modeling Studio** | `estimate_effect`, `predict`, `forecast`; statistical and ML tools; model cards; model registry | A churn prediction and a DiD analysis run end to end on demo data |
| **3. Analysis + Postgres** | `metric_query`, `diagnose`; metric layer; Postgres connector; insight feed | "Why did conversion drop?" works against a connected Postgres database |
| **4. Data Modeling + Warehouses** | `model_data`; star-schema proposal, transforms, tests, lineage, dbt export; BigQuery and Snowflake connectors | Raw tables become a tested analytics model |
| **5. Reports & polish** | Shareable reports, scheduled re-runs, onboarding | Portfolio launch |

## 8. Success metrics (for the portfolio and real use)

- **Time to first insight** on the demo workspace: under 2 minutes.
- **Verification pass rate** of drafted insights: at least 90% on golden test runs.
- **Statistical correctness:** every stats tool matches reference implementations in tests.
- **Insight acceptance rate** (accepted or edited, out of all reviewed).

## 9. Non-goals (for now)

- Real-time and streaming data.
- Writing back to customer warehouses.
- Arbitrary user-supplied code execution.
- Replacing a BI tool's dashboarding.
