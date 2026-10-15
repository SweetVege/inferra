# Inferra

**An AI-native data science workbench.** Describe what you want to know in plain English. A team of specialist agents plans the work, runs real queries and statistics on your data, and returns insights that are checked against the computed results before you review them.

> Language models reason. Tools compute. People decide.

## How it works

1. **Ask.** "Why did signup conversion drop in late September?"
2. **Plan.** The Orchestrator maps the goal to a workflow template (explore, metric question, A/B analysis, …), fills in its parameters, and asks a clarifying question only when it must. You approve the plan, or let the workspace auto-approve.
3. **Run.** Specialist agents call typed tools against a sandboxed DuckDB: profiling and read-only SQL; A/B test design and analysis (power, SRM, CUPED, segments, Bayesian and sequential reads); regression, difference-in-differences, propensity matching, and forecasting; and model training that compares gradient-boosted trees with a linear baseline, explains predictions with SHAP, and registers the winner for batch scoring. Every tool call is logged with its inputs, SQL, and output.
4. **Verify.** The Insight Agent drafts findings that cite artifact values. A deterministic verifier checks that every number matches a cited value, blocks causal wording without a randomized or causal design, caps confidence on small samples, requires intervals with every estimate and forecast, requires observational causal claims to state their assumption, insists on held-out model metrics, and refuses effect claims from tests that failed their sample ratio check. Failed drafts go back for revision; drafts that still fail are flagged, not hidden.
5. **Review.** You accept, edit, or reject each insight.

```mermaid
flowchart LR
  G["Goal (plain English)"] --> O["Orchestrator<br/>intent + plan"]
  O -->|approved| D["Data Agent"] --> S["Analyst, Experiment,<br/>or ML Agent"] --> I["Insight Agent"]
  D & S --> T["Typed tools<br/>DuckDB · scipy · statsmodels · LightGBM"]
  I --> V["Verifier<br/>(deterministic)"] --> R["Human review"]
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and [docs/PRD.md](docs/PRD.md) for scope and roadmap.

## Status

| Phase | Scope | State |
|---|---|---|
| 0. Foundation | Auth and workspaces, file upload → Parquet, sandboxed DuckDB, profiling, job queue, agent runtime, verifier, review UI, demo workspace | Done |
| 1. MVP | A/B test design and analysis: power and duration, SRM, z/Welch tests, CUPED, segment effects with Holm correction, Bayesian and sequential reads | Done |
| 2. Modeling Studio | Regression, difference-in-differences, propensity matching, Holt-Winters forecasting, ML with model selection, SHAP, model cards, registry, and batch scoring | Done |
| 3. Analysis + Postgres | Metric layer, anomaly and change-point detection, exact mix/rate decomposition of metric changes, Postgres extracts, insight feed | Done |
| 4. Data Modeling + Warehouses | Star-schema proposals, transforms, tests; BigQuery and Snowflake | Next |

## Run it locally

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm.

```bash
# API (http://127.0.0.1:8000) — uses SQLite and local file storage by default
cd services/api
cp .env.example .env          # set INFERRA_LLM_MODEL and ANTHROPIC_API_KEY to enable agent runs
uv sync
uv run python -m inferra.seed # demo workspace with six completed runs and defined metrics
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

- **API:** Docker image from `services/api/Dockerfile` (build context: repository root). `render.yaml` is a ready Render blueprint. On start the container applies migrations, ensures the demo workspace exists, and serves. Use Postgres (e.g. Neon) and S3-compatible storage (e.g. Cloudflare R2).
- **Web:** Vercel, root directory `apps/web`, with `INFERRA_API_URL` set to the API's URL.
- **GitHub sign-in:** create an OAuth app with callback `<web origin>/api/auth/github/callback` and set `INFERRA_GITHUB_CLIENT_ID` / `INFERRA_GITHUB_CLIENT_SECRET` on the API.

## Stack

Next.js · TypeScript · Tailwind CSS · FastAPI · SQLAlchemy · Alembic · DuckDB · pandas · SciPy · statsmodels · scikit-learn · LightGBM · Postgres · Claude API
