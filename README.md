# Inferra

**An AI-native data science workbench.** Describe what you want to know in plain English. A team of specialist agents plans the work, runs real queries and statistics on your data, and returns insights that are checked against the computed results before you review them.

> Language models reason. Tools compute. People decide.

**[Live demo](https://inferra-demo.vercel.app)** · **[Sample report](https://inferra-demo.vercel.app/r/I3ok7ujo53s8UK0GWsOAsMhEXzse2qwD)** · no sign-up needed. The demo is read-only and shows completed runs, a shared report, and a metric monitor. If it has been idle, the first visit can take up to a minute while the API wakes up.

![A completed A/B test run: a verified insight with its evidence, method, caveats, and the verifier's checks](docs/images/run.png)

*A completed run in the demo workspace. Each insight cites the tool results its numbers come from, states its method and caveats, and lists the checks the verifier ran.*

## How it works

1. **Ask.** "Why did signup conversion drop in late September?"
2. **Plan.** The Orchestrator maps the goal to a workflow template (explore, metric question, A/B analysis, data modeling, …), fills in its parameters, and asks a clarifying question only when it must. You approve the plan, or let the workspace auto-approve.
3. **Run.** Specialist agents call typed tools against a sandboxed DuckDB: profiling and read-only SQL; A/B test design and analysis (power, SRM, CUPED, segments, Bayesian and sequential reads); regression, difference-in-differences, propensity matching, and forecasting; model training that compares gradient-boosted trees with a linear baseline, explains predictions with SHAP, and registers the winner for batch scoring; and dimensional modeling that finds a table's grain and entities, builds a tested star schema, and exports it as a dbt project. Every tool call is logged with its inputs, SQL, and output.
4. **Verify.** The Insight Agent drafts findings that cite artifact values. A deterministic verifier checks that every number matches a cited value, blocks causal wording without a randomized or causal design, caps confidence on small samples, requires intervals with every estimate and forecast, requires observational causal claims to state their assumption, insists on held-out model metrics, refuses effect claims from tests that failed their sample ratio check, and makes failing data-model tests visible. Failed drafts go back for revision; drafts that still fail are flagged, not hidden.
5. **Review.** You accept, edit, or reject each insight. Edits keep the agent's original text and go through the verifier again.
6. **Share and monitor.** Reviewed insights become a report with charts and a method appendix, published as a frozen version and shareable by a revocable link that needs no account. Any completed run can re-run daily or weekly on the plan you approved, and a monitor ("Alert me when conversion drops") raises an in-app alert when its metric check crosses the threshold.

```mermaid
flowchart LR
  G["Goal (plain English)"] --> O["Orchestrator<br/>intent + plan"]
  O -->|approved| D["Data Agent"] --> S["Analyst, Experiment,<br/>ML Agent, or Data Modeler"] --> I["Insight Agent"]
  D & S --> T["Typed tools<br/>DuckDB · scipy · statsmodels · LightGBM"]
  I --> V["Verifier<br/>(deterministic)"] --> R["Human review"] --> P["Report<br/>(shareable link)"]
  R -. schedule .-> O
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and [docs/PRD.md](docs/PRD.md) for scope and roadmap.

## What it looks like

All screenshots are from the [live demo](https://inferra-demo.vercel.app).

<table>
<tr>
<td width="50%" valign="top">
<img src="docs/images/workflow.png" alt="The run's workflow: each agent's steps and tool calls, with the SQL behind an A/B test">
<p><b>Every step is on the record.</b> The run page shows each agent's tool calls with their inputs, SQL, and outputs, so any number in an insight can be traced back to the query that produced it.</p>
</td>
<td width="50%" valign="top">
<img src="docs/images/model.png" alt="A churn model card: holdout AUC, the baseline it beat, SHAP feature importance, and calibration">
<p><b>Models come with a model card.</b> Gradient-boosted trees and a linear baseline are compared on held-out data (here the simpler model won); the winner is registered with its SHAP explanations and calibration, ready for batch scoring.</p>
</td>
</tr>
<tr>
<td width="50%" valign="top">
<img src="docs/images/data-model.png" alt="A star schema built by the Data Modeler: lineage graph and data tests">
<p><b>Raw tables become a tested star schema.</b> The Data Modeler finds each table's grain and keys, builds facts and dimensions with data tests and lineage, keeps failing tests in view, and exports the result as a dbt project.</p>
</td>
<td width="50%" valign="top">
<img src="docs/images/report.png" alt="A shared report: summary, verified findings, and charts, opened from a public link">
<p><b>Reviewed insights become a report.</b> Accepted findings are published as a frozen version with charts and a method appendix, shared by a revocable link that needs no account. <a href="https://inferra-demo.vercel.app/r/I3ok7ujo53s8UK0GWsOAsMhEXzse2qwD">Open it.</a></p>
</td>
</tr>
</table>

## On real data

Public Kaggle datasets, run on the live deployment with a real model and reviewed by a person before publishing. [docs/EVALUATION.md](docs/EVALUATION.md) has the method, every question's result against published analyses, the fixes the evaluation drove, and what is still unsolved.

| Case | Question | Result |
|---|---|---|
| [Cookie Cats](https://inferra-demo.vercel.app/r/pHet7uCTv6eOYl5ZHNrBFnm0bllHxzYJ) (mobile game A/B test, 90,189 players) | Should the first gate move from level 30 to level 40? | No: 7-day retention fell by 0.82 percentage points (p=0.0016), with no offsetting gain in 1-day retention or engagement. Matches the published analyses of this dataset. |
| [Telco Customer Churn](https://inferra-demo.vercel.app/r/NNSOi5qdxdWFu_4rLa3LtNtN3PKilNKj) (7,043 customers) | Who churns, and do longer contracts help? | A churn model ranks customers well on held-out data (AUC 0.848; contract type, tenure, and internet and security services drive it). Two-year customers churn far less than month-to-month ones even after adjusting for tenure and charges (odds ratio 0.158), reported as an association, with the adjustment's caveats, not as an effect of the contract. |
| [Walmart weekly sales](https://inferra-demo.vercel.app/r/6OAEIAGfOei-Vvu6k8XigR_Rjfh4tG4L) (45 stores, 143 weeks) | What do holidays and the economy explain? | Holiday weeks sell about 7.8% more, almost all of it Thanksgiving, from only 10 independent holiday weeks. Unemployment, fuel price, CPI and temperature look predictive on a random split (R² 0.30), but the agent held out whole stores on its own and the skill vanished (R² −0.25, worse than predicting the mean): the model was recognizing stores, not learning from the economy. |
| [Olist e-commerce](https://inferra-demo.vercel.app/r/kgrgvDVPOHKJanWdx6V1IDFLPMFuDxsx) (four related tables, 99,441 orders) | Do late deliveries hurt reviews, what drives revenue, and can bad reviews be predicted? | Late orders average 2.57 stars against 4.29, a 1.72-point gap (95% CI 1.68 to 1.76) that barely moves after controlling for price, freight, category, weight, photos, description length and purchase month; reviews written after delivery show a smaller 0.38-point gap. A model ranks likely 1–2 star orders (AUC 0.756 on delivered orders), and the agent flagged that its delivery features are only known after delivery. Running this case drove four fixes: views over joined tables, a DuckDB memory cap, nullable-column handling, and faster training. |
| [Online Retail](https://inferra-demo.vercel.app/r/Aa-r6OFLB84QU-yKULLUGrx6_6frZHOa) (UK gift retailer, 541,909 rows) | What needs cleaning, which products drive revenue, and how well are customers kept? | Found the 9,288 cancellations, 135,080 rows without a customer ID (24.9%) and the non-product codes, matching published analyses exactly. A cancelled 80,995-unit order looks like a £168,470 best-seller until reversals are netted out (net £0); the genuine leader is the Regency cakestand at £164,762 net. About a quarter of customers order again the next month, and the first cohort's higher retention is explained by customers who were buying before the data starts. Running this case drove three fixes: non-UTF-8 uploads, text dates, and netting reversals. |

## Status

| Phase | Scope | State |
|---|---|---|
| 0. Foundation | Auth and workspaces, file upload → Parquet, sandboxed DuckDB, profiling, job queue, agent runtime, verifier, review UI, demo workspace | Done |
| 1. MVP | A/B test design and analysis: power and duration, SRM, z/Welch tests, CUPED, segment effects with Holm correction, Bayesian and sequential reads | Done |
| 2. Modeling Studio | Regression, difference-in-differences, propensity matching, Holt-Winters forecasting, ML with model selection, SHAP, model cards, registry, and batch scoring | Done |
| 3. Analysis + Postgres | Metric layer, anomaly and change-point detection, exact mix/rate decomposition of metric changes, Postgres extracts, insight feed | Done |
| 4. Data Modeling + Warehouses | Key and entity discovery, star-schema design, SQL transforms with tests and lineage, publishing as datasets, dbt export; BigQuery and Snowflake extracts | Done |
| 5. Reports & polish | Shareable reports (publish snapshots, public links), scheduled re-runs, metric monitors with alerts, getting-started checklist and demo tour | Done |

## Run it locally

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm.

```bash
# API (http://127.0.0.1:8000) — uses SQLite and local file storage by default
cd services/api
cp .env.example .env          # set INFERRA_LLM_MODEL and ANTHROPIC_API_KEY to enable agent runs
uv sync
uv run python -m inferra.seed # demo workspace with eight completed runs, a shared report, and a monitor
uv run uvicorn inferra.main:app --reload

# Web (http://localhost:3000)
cd apps/web
pnpm install
pnpm dev
```

Without a model configured you can still sign in, upload and profile datasets, and browse the demo workspace. To use Postgres locally, run `docker compose up -d` and set `INFERRA_DATABASE_URL=postgresql://inferra:inferra@localhost:5432/inferra`, then `uv run alembic upgrade head`.

## Tests

```bash
cd services/api
uv run pytest                    # SQLite
INFERRA_TEST_DATABASE_URL=postgresql://inferra:inferra@localhost:5432/inferra_test uv run pytest
uv run ruff check . && uv run ruff format --check .

cd apps/web && pnpm lint && pnpm typecheck && pnpm build
```

The end-to-end tests drive the real API, queue, agents, tools, and verifier with a scripted model, so they run offline and deterministically.

## Deploy

Vercel (web) + Render (API, Docker) + Neon (Postgres) + Cloudflare R2 (storage), all on free tiers.
`render.yaml` is a ready Render blueprint; on start the API container applies migrations, keeps the
demo workspace current, and serves. Step-by-step instructions, in order: [docs/DEPLOY.md](docs/DEPLOY.md).

## Stack

Next.js · TypeScript · Tailwind CSS · FastAPI · SQLAlchemy · Alembic · DuckDB · pandas · SciPy · statsmodels · scikit-learn · LightGBM · dbt export · Postgres · BigQuery · Snowflake · Claude API
