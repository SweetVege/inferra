"use client";

import { ArrowUp, Check, Database, Upload } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { canEdit, useSession, useWorkspace } from "@/components/session";
import { AGENTS, AgentAvatar, Badge, Card, cx, EmptyState, ErrorBanner, RunStatusBadge, SectionTitle, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber, timeAgo } from "@/lib/format";
import type { Dataset, RunDetail, RunSummary } from "@/lib/types";

const SUGGESTIONS = [
  "Give me an overview of this dataset. What stands out, and can I trust the data?",
  "What is the average order value by region and channel?",
  "Which columns have data-quality problems I should fix first?",
];

export default function AskPage() {
  const { ws } = useParams<{ ws: string }>();
  const router = useRouter();
  const workspace = useWorkspace(ws);
  const { meta } = useSession();
  const editable = canEdit(workspace);
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<Dataset[]>(`/workspaces/${ws}/datasets`).then(setDatasets, (e) => setError(e.message));
    api.get<RunSummary[]>(`/workspaces/${ws}/runs`).then(setRuns, () => {});
  }, [ws]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await api.post<RunDetail>(`/workspaces/${ws}/runs`, { goal, dataset_ids: selected });
      router.push(`/w/${ws}/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the run.");
      setBusy(false);
    }
  }

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const disabledReason = !editable
    ? "This workspace is read-only. Open a completed run below, or sign in to use your own workspace."
    : !meta.llm_available
      ? "Agent runs need a configured model on the API (INFERRA_LLM_MODEL and ANTHROPIC_API_KEY)."
      : datasets && datasets.length === 0
        ? "Upload a dataset first."
        : null;

  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <header className="space-y-2 pt-4 md:pt-10">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">What do you want to know?</h1>
        <p className="text-ink-2">
          Describe your goal in plain English. Inferra plans the work, runs it with a team of agents, and brings back insights you can verify.
        </p>
      </header>

      <form onSubmit={submit} className="space-y-3">
        <Card className="p-2 focus-within:border-accent">
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && goal.trim().length > 2) e.currentTarget.form?.requestSubmit();
            }}
            rows={3}
            placeholder="e.g. Which regions have the highest order value, and is the data clean enough to report on?"
            className="w-full resize-none bg-transparent px-3 py-2 text-[15px] outline-none placeholder:text-ink-3"
            disabled={!!disabledReason}
            aria-label="Goal"
          />
          <div className="flex flex-wrap items-center justify-between gap-2 px-2 pb-1">
            <div className="flex flex-wrap gap-1.5">
              {datasets?.map((d) => (
                <button
                  type="button"
                  key={d.id}
                  onClick={() => toggle(d.id)}
                  className={cx(
                    "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs",
                    selected.includes(d.id) ? "border-accent bg-accent-soft text-accent" : "border-line text-ink-2 hover:bg-surface-2",
                  )}
                >
                  {selected.includes(d.id) ? <Check className="size-3" /> : <Database className="size-3" />}
                  {d.display_name}
                </button>
              ))}
            </div>
            <button
              type="submit"
              disabled={busy || !!disabledReason || goal.trim().length < 3}
              className="inline-flex size-9 items-center justify-center rounded-lg bg-accent text-accent-ink disabled:opacity-40"
              aria-label="Start analysis"
            >
              {busy ? <Spinner label="" /> : <ArrowUp className="size-4" />}
            </button>
          </div>
        </Card>
        {disabledReason && <p className="text-sm text-ink-3">{disabledReason}</p>}
        {error && <ErrorBanner message={error} />}
        {!disabledReason && (
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                type="button"
                key={s}
                onClick={() => setGoal(s)}
                className="rounded-lg border border-line bg-surface px-3 py-1.5 text-left text-[13px] text-ink-2 hover:text-ink"
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </form>

      {datasets && datasets.length === 0 && (
        <EmptyState icon={Upload} title="No datasets yet">
          <Link href={`/w/${ws}/datasets`} className="text-accent hover:underline">
            Upload a CSV, Parquet, or Excel file
          </Link>{" "}
          to start asking questions.
        </EmptyState>
      )}

      <section>
        <SectionTitle
          action={
            runs.length > 0 && (
              <Link href={`/w/${ws}/runs`} className="text-sm text-accent hover:underline">
                View all
              </Link>
            )
          }
        >
          Recent runs
        </SectionTitle>
        {runs.length === 0 ? (
          <p className="text-sm text-ink-3">Runs you start appear here.</p>
        ) : (
          <Card className="divide-y divide-line">
            {runs.slice(0, 5).map((r) => (
              <Link key={r.id} href={`/w/${ws}/runs/${r.id}`} className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-surface-2">
                <span className="min-w-0 truncate text-sm">{r.goal}</span>
                <span className="flex shrink-0 items-center gap-3 text-xs text-ink-3">
                  {r.insight_count > 0 && <span>{formatNumber(r.insight_count)} insights</span>}
                  <RunStatusBadge status={r.status} />
                  <span className="hidden sm:inline">{timeAgo(r.created_at)}</span>
                </span>
              </Link>
            ))}
          </Card>
        )}
      </section>

      <section>
        <SectionTitle>How Inferra works</SectionTitle>
        <div className="grid gap-3 sm:grid-cols-2">
          {(["orchestrator", "data", "analyst", "insight"] as const).map((key) => (
            <Card key={key} className="flex items-start gap-3 p-4">
              <AgentAvatar agent={key} />
              <div>
                <p className="text-sm font-medium">{AGENTS[key].title}</p>
                <p className="text-sm text-ink-2">{AGENTS[key].role}</p>
              </div>
            </Card>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {meta.intents.map((i) => (
            <span key={i.intent} title={i.description}>
              <Badge tone={i.available ? "accent" : "neutral"}>
                {i.title}
                {!i.available && ` · ${i.phase}`}
              </Badge>
            </span>
          ))}
        </div>
      </section>
    </div>
  );
}
