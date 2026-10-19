"use client";

import { ArrowLeft, BellRing, CalendarClock, Check, ChevronRight, CircleDashed, Clock, Loader2, MessageCircleQuestion, MinusCircle, RotateCw, X, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ArtifactView } from "@/components/artifact-view";
import { InsightCard } from "@/components/insight-card";
import { canEdit, useWorkspace } from "@/components/session";
import { AGENTS, AgentAvatar, Badge, Button, Card, CodeBlock, cx, ErrorBanner, RunStatusBadge, SectionTitle, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCompact, timeAgo, titleCase } from "@/lib/format";
import type { Artifact, Insight, RunDetail, Schedule, Step, ToolCall } from "@/lib/types";

const ACTIVE = new Set(["planning", "running"]);

function StepIcon({ status }: { status: Step["status"] }) {
  if (status === "completed") return <Check className="size-4 text-good" aria-label="completed" />;
  if (status === "running") return <Loader2 className="size-4 animate-spin text-accent" aria-label="running" />;
  if (status === "failed") return <XCircle className="size-4 text-bad" aria-label="failed" />;
  if (status === "skipped") return <MinusCircle className="size-4 text-ink-3" aria-label="skipped" />;
  return <CircleDashed className="size-4 text-ink-3" aria-label="pending" />;
}

function ToolCallRow({ call, artifact, onShow }: { call: ToolCall; artifact?: Artifact; onShow: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-lg border border-line">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm" aria-expanded={open}>
        <ChevronRight className={cx("size-4 text-ink-3 transition-transform", open && "rotate-90")} aria-hidden />
        <code className="font-mono text-[13px]">{call.tool}</code>
        {artifact && <span className="truncate text-ink-2">{artifact.title}</span>}
        <span className="ml-auto flex shrink-0 items-center gap-2 text-xs text-ink-3">
          {call.error && <Badge tone="bad">error</Badge>}
          <span className="tabular">{call.duration_ms} ms</span>
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-line px-3 py-3">
          {call.error && <p className="text-sm text-bad">{call.error}</p>}
          <CodeBlock>{call.code ?? JSON.stringify(call.input, null, 2)}</CodeBlock>
          {artifact && (
            <Button size="sm" onClick={() => onShow(artifact.id)}>
              View result
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function Timeline({ run, onShow }: { run: RunDetail; onShow: (id: string) => void }) {
  return (
    <ol className="space-y-3">
      {run.steps.map((step) => {
        const calls = run.tool_calls.filter((c) => c.step_id === step.id);
        return (
          <li key={step.id}>
            <Card className="p-4">
              <div className="flex items-start gap-3">
                <AgentAvatar agent={step.agent} />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">{AGENTS[step.agent]?.title ?? step.agent}</span>
                    <StepIcon status={step.status} />
                  </div>
                  <p className="text-sm text-ink-2">{step.task}</p>
                  {step.summary && <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm break-words">{step.summary}</p>}
                  {step.error && <p className="text-sm text-bad">{step.error}</p>}
                  {calls.length > 0 && (
                    <ul className="space-y-1.5">
                      {calls.map((c) => (
                        <ToolCallRow key={c.id} call={c} artifact={run.artifacts.find((a) => a.id === c.artifact_id)} onShow={onShow} />
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </Card>
          </li>
        );
      })}
    </ol>
  );
}

function PlanPanel({ run, editable, onAct }: { run: RunDetail; editable: boolean; onAct: (action: "approve" | "cancel") => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null);
  const plan = run.plan!;
  const act = async (a: "approve" | "cancel") => {
    setBusy(a);
    await onAct(a);
    setBusy(null);
  };
  return (
    <Card className="border-accent/40 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Proposed plan: {plan.title}</h2>
        <Badge>{titleCase(plan.confidence)} confidence</Badge>
      </div>
      {plan.rationale && <p className="mt-1 text-sm text-ink-2">{plan.rationale}</p>}
      {Object.keys(plan.params).length > 0 && (
        <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {Object.entries(plan.params).map(([k, v]) => (
            <div key={k}>
              <dt className="inline text-ink-3">{titleCase(k)}: </dt>
              <dd className="inline">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <ol className="mt-4 space-y-2">
        {plan.steps.map((s, i) => (
          <li key={s.key} className="flex items-start gap-3 text-sm">
            <span className="tabular mt-1 w-4 text-ink-3">{i + 1}</span>
            <AgentAvatar agent={s.agent} size="sm" />
            <span>
              <span className="font-medium">{AGENTS[s.agent]?.title}</span> <span className="text-ink-2">{s.task}</span>
            </span>
          </li>
        ))}
      </ol>
      {editable && (
        <div className="mt-5 flex gap-2">
          <Button variant="primary" loading={busy === "approve"} onClick={() => void act("approve")}>
            Approve and run
          </Button>
          <Button variant="ghost" loading={busy === "cancel"} onClick={() => void act("cancel")}>
            Cancel
          </Button>
        </div>
      )}
    </Card>
  );
}

function ClarificationPanel({ run, editable, onAnswer }: { run: RunDetail; editable: boolean; onAnswer: (a: string) => Promise<void> }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const question = run.clarifications[run.clarifications.length - 1]?.question;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    await onAnswer(answer);
    setBusy(false);
    setAnswer("");
  }
  return (
    <Card className="p-5">
      <div className="flex items-start gap-3">
        <MessageCircleQuestion className="mt-0.5 size-5 shrink-0 text-warn" aria-hidden />
        <div className="flex-1 space-y-3">
          <p className="font-medium">{question}</p>
          {editable && (
            <form onSubmit={submit} className="flex gap-2">
              <input
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                required
                className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
                placeholder="Your answer"
                aria-label="Answer"
              />
              <Button variant="primary" type="submit" loading={busy}>
                Reply
              </Button>
            </form>
          )}
        </div>
      </div>
    </Card>
  );
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function SchedulePanel({ run, ws, editable, onScheduled }: { run: RunDetail; ws: string; editable: boolean; onScheduled: (s: Schedule) => void }) {
  const [cadence, setCadence] = useState<"daily" | "weekly">("weekly");
  const [weekday, setWeekday] = useState(0);
  const [hour, setHour] = useState(8);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const schedule = run.schedules[0];
  const select = "h-8 rounded-lg border border-line bg-surface px-2 text-sm";

  if (schedule) {
    return (
      <Card className="flex flex-wrap items-center gap-3 p-4 text-sm">
        <CalendarClock className="size-5 shrink-0 text-accent" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="font-medium">{schedule.enabled ? `Re-runs ${schedule.description.replace(/^Every/, "every")}` : "Schedule paused"}</span>
          <span className="text-ink-2"> · the same plan, with fresh data and new insights each time</span>
        </span>
        <Link href={`/w/${ws}/monitors`} className="text-accent hover:underline">
          Manage
        </Link>
      </Card>
    );
  }
  if (!editable) return null;
  async function submit() {
    setBusy(true);
    setError(null);
    try {
      onScheduled(await api.post<Schedule>(`/workspaces/${ws}/schedules`, { run_id: run.id, cadence, weekday, hour }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not schedule the run.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card className="space-y-2 p-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <CalendarClock className="size-5 shrink-0 text-ink-3" aria-hidden />
        <span className="font-medium">Re-run this plan</span>
        <select className={select} value={cadence} onChange={(e) => setCadence(e.target.value as "daily" | "weekly")} aria-label="Cadence">
          <option value="daily">every day</option>
          <option value="weekly">every week</option>
        </select>
        {cadence === "weekly" && (
          <select className={select} value={weekday} onChange={(e) => setWeekday(Number(e.target.value))} aria-label="Weekday">
            {WEEKDAYS.map((d, i) => (
              <option key={d} value={i}>
                on {d}
              </option>
            ))}
          </select>
        )}
        <select className={select} value={hour} onChange={(e) => setHour(Number(e.target.value))} aria-label="Hour (UTC)">
          {Array.from({ length: 24 }, (_, h) => (
            <option key={h} value={h}>
              at {String(h).padStart(2, "0")}:00 UTC
            </option>
          ))}
        </select>
        <Button size="sm" variant="primary" loading={busy} onClick={() => void submit()}>
          Schedule
        </Button>
      </div>
      <p className="text-xs text-ink-3">Scheduled runs reuse this approved plan, refresh extracts first, and add new insights for review.</p>
      {error && <p className="text-sm text-bad">{error}</p>}
    </Card>
  );
}

const FEATURED = new Set(["data_model", "keys", "cuped", "ab_test", "power", "did", "psm", "forecast", "model", "regression", "anomalies", "decomposition", "metric", "metric_check"]);

function ResultCard({ artifact, defaultOpen }: { artifact: Artifact; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-3 text-left" aria-expanded={open}>
        <ChevronRight className={cx("size-4 shrink-0 text-ink-3 transition-transform", open && "rotate-90")} aria-hidden />
        <span className="truncate text-sm font-medium">{artifact.title}</span>
        <span className="ml-auto shrink-0">
          <Badge>{titleCase(artifact.kind)}</Badge>
        </span>
      </button>
      {open && (
        <div className="border-t border-line px-4 py-4">
          <ArtifactView artifact={artifact} />
        </div>
      )}
    </Card>
  );
}

function ArtifactModal({ artifact, onClose }: { artifact: Artifact; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 md:p-10" onClick={onClose} role="dialog" aria-modal aria-label={artifact.title}>
      <Card className="w-full max-w-4xl p-5">
        <div onClick={(e) => e.stopPropagation()}>
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <p className="text-xs text-ink-3">{titleCase(artifact.kind)} · {artifact.id}</p>
              <h2 className="font-semibold">{artifact.title}</h2>
            </div>
            <button onClick={onClose} className="rounded-lg p-1.5 text-ink-3 hover:bg-surface-2" aria-label="Close">
              <X className="size-4" />
            </button>
          </div>
          <ArtifactView artifact={artifact} />
        </div>
      </Card>
    </div>
  );
}

export default function RunPage() {
  const { ws, id } = useParams<{ ws: string; id: string }>();
  const workspace = useWorkspace(ws);
  const editable = canEdit(workspace);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const router = useRouter();

  const load = useCallback(async () => {
    try {
      setRun(await api.get<RunDetail>(`/workspaces/${ws}/runs/${id}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the run.");
    }
  }, [ws, id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!run || !ACTIVE.has(run.status)) return;
    const timer = setInterval(() => void load(), 1500);
    return () => clearInterval(timer);
  }, [run, load]);

  async function act(path: string, body?: unknown) {
    try {
      setRun(await api.post<RunDetail>(`/workspaces/${ws}/runs/${id}/${path}`, body));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed.");
    }
  }

  if (!run) return error ? <ErrorBanner message={error} /> : <Spinner />;
  const updateInsight = (next: Insight) => setRun((r) => (r ? { ...r, insights: r.insights.map((i) => (i.id === next.id ? next : i)) } : r));
  const shownArtifact = run.artifacts.find((a) => a.id === shown);
  const verified = run.insights.filter((i) => i.verified).length;
  const tokens = (run.usage.input_tokens ?? 0) + (run.usage.output_tokens ?? 0);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div className="space-y-3">
        <Link href={`/w/${ws}/runs`} className="inline-flex items-center gap-1 text-sm text-ink-3 hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Runs
        </Link>
        <h1 className="text-xl font-semibold leading-snug tracking-tight md:text-2xl">{run.goal}</h1>
        <div className="flex flex-wrap items-center gap-3 text-sm text-ink-3">
          <RunStatusBadge status={run.status} />
          {run.plan && <span>{run.plan.title}</span>}
          <span className="inline-flex items-center gap-1">
            <Clock className="size-3.5" aria-hidden /> {timeAgo(run.created_at)}
          </span>
          {run.schedule_id && (
            <Link href={`/w/${ws}/monitors`} className="inline-flex items-center gap-1 hover:text-ink">
              <CalendarClock className="size-3.5" aria-hidden /> Scheduled re-run
            </Link>
          )}
          {tokens > 0 && <span className="tabular">{formatCompact(tokens)} tokens · {run.usage.requests} model calls</span>}
          {editable && ACTIVE.has(run.status) && (
            <Button size="sm" variant="ghost" onClick={() => void act("cancel")}>
              Cancel run
            </Button>
          )}
        </div>
      </div>

      {error && <ErrorBanner message={error} />}
      {run.alerts.map((a) => (
        <Card key={a.id} className="flex items-start gap-3 border-bad/40 p-4 text-sm">
          <BellRing className="mt-0.5 size-5 shrink-0 text-bad" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="font-medium">{a.title}.</span> <span className="text-ink-2">{a.message}</span>
          </span>
          <Link href={`/w/${ws}/monitors`} className="shrink-0 text-accent hover:underline">
            {a.status === "open" ? "Review" : "Acknowledged"}
          </Link>
        </Card>
      ))}
      {run.status === "needs_input" && <ClarificationPanel run={run} editable={editable} onAnswer={(answer) => act("answer", { answer })} />}
      {run.status === "awaiting_approval" && run.plan && <PlanPanel run={run} editable={editable} onAct={(a) => act(a)} />}
      {run.status === "planning" && (
        <Card className="flex items-center gap-3 p-5">
          <AgentAvatar agent="orchestrator" />
          <Spinner label="The Orchestrator is reading your goal and choosing a workflow…" />
        </Card>
      )}
      {(run.status === "failed" || run.status === "unsupported") && run.error && (
        <Card className="p-5 text-sm">
          <p className="font-medium">{run.status === "unsupported" ? "Not available yet" : "The run stopped"}</p>
          <p className="mt-1 text-ink-2">{run.error}</p>
          {run.status === "failed" && editable && (
            <Button
              className="mt-3"
              size="sm"
              loading={retrying}
              onClick={async () => {
                setRetrying(true);
                setError(null);
                try {
                  const next = await api.post<RunDetail>(`/workspaces/${ws}/runs/${id}/retry`);
                  router.push(`/w/${ws}/runs/${next.id}`);
                } catch (err) {
                  setError(err instanceof Error ? err.message : "Could not retry the run.");
                  setRetrying(false);
                }
              }}
            >
              <RotateCw className="size-4" aria-hidden /> Retry{run.plan ? " with the same plan" : ""}
            </Button>
          )}
        </Card>
      )}

      {run.insights.length > 0 && (
        <section>
          <SectionTitle>
            Insights · {verified} of {run.insights.length} verified
          </SectionTitle>
          <div className="space-y-3">
            {run.insights.map((i) => (
              <InsightCard key={i.id} insight={i} workspaceId={ws} artifacts={run.artifacts} editable={editable} onChange={updateInsight} onShowArtifact={setShown} />
            ))}
          </div>
        </section>
      )}

      {run.status === "completed" && !run.schedule_id && (
        <SchedulePanel run={run} ws={ws} editable={editable} onScheduled={(sch) => setRun((r) => (r ? { ...r, schedules: [sch] } : r))} />
      )}

      {run.artifacts.length > 0 && (
        <section>
          <SectionTitle>Results</SectionTitle>
          <div className="space-y-2">
            {run.artifacts.map((a, i) => (
              <ResultCard key={a.id} artifact={a} defaultOpen={i < 3 && FEATURED.has(a.kind)} />
            ))}
          </div>
        </section>
      )}

      {run.steps.length > 0 && (
        <section>
          <SectionTitle>Agent workflow</SectionTitle>
          <Timeline run={run} onShow={setShown} />
        </section>
      )}

      {run.clarifications.some((c) => c.answer) && (
        <section>
          <SectionTitle>Clarifications</SectionTitle>
          <ul className="space-y-1 text-sm">
            {run.clarifications.filter((c) => c.answer).map((c, i) => (
              <li key={i}>
                <span className="text-ink-2">{c.question}</span> → {c.answer}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <button onClick={() => setShowLog(!showLog)} className="inline-flex items-center gap-1 text-sm text-ink-3 hover:text-ink" aria-expanded={showLog}>
          <ChevronRight className={cx("size-4 transition-transform", showLog && "rotate-90")} aria-hidden />
          Activity log ({run.events.length})
        </button>
        {showLog && (
          <ol className="mt-2 space-y-1 border-l border-line pl-4 text-sm">
            {run.events.map((e) => (
              <li key={e.id} className="flex gap-3">
                <span className="tabular shrink-0 text-xs leading-5 text-ink-3">{new Date(e.created_at).toLocaleTimeString()}</span>
                <span className="text-ink-2">{e.message}</span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {shownArtifact && <ArtifactModal artifact={shownArtifact} onClose={() => setShown(null)} />}
    </div>
  );
}
