"use client";

import { BellRing, CalendarClock, Check, Pause, Play, RotateCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { canEdit, useWorkspace } from "@/components/session";
import { Badge, Button, Card, cx, EmptyState, ErrorBanner, RunStatusBadge, SectionTitle, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatMetric, formatPercent, timeAgo } from "@/lib/format";
import type { Alert, Schedule } from "@/lib/types";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const select = "h-8 rounded-lg border border-line bg-surface px-2 text-sm";

function AlertCard({ alert, ws, editable, onChange }: { alert: Alert; ws: string; editable: boolean; onChange: (a: Alert) => void }) {
  const [busy, setBusy] = useState(false);
  const d = alert.data;
  const format = d.metric?.format ?? "number";
  return (
    <Card className={cx("p-4", alert.status === "open" ? "border-bad/40" : "opacity-70")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={alert.status === "open" ? "bad" : "neutral"} icon={BellRing}>
              {alert.status === "open" ? "Open" : "Acknowledged"}
            </Badge>
            <span className="text-xs text-ink-3">{timeAgo(alert.created_at)}</span>
          </div>
          <p className="font-medium">{alert.title}</p>
          <p className="text-sm text-ink-2">{alert.message}</p>
          {d.value != null && (
            <p className="tabular text-sm">
              {d.grain} of {d.period}: <span className="font-semibold">{formatMetric(d.value, format)}</span> vs median {formatMetric(d.baseline, format)} (
              {formatPercent(d.relative_change)})
            </p>
          )}
          <Link href={`/w/${ws}/runs/${alert.run_id}`} className="inline-block text-sm text-accent hover:underline">
            Open the run
          </Link>
        </div>
        {editable && alert.status === "open" && (
          <Button
            size="sm"
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                onChange(await api.post<Alert>(`/workspaces/${ws}/alerts/${alert.id}/acknowledge`));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Check className="size-4" aria-hidden /> Acknowledge
          </Button>
        )}
      </div>
    </Card>
  );
}

function ScheduleCard({
  schedule,
  ws,
  editable,
  demo,
  onChange,
  onDelete,
}: {
  schedule: Schedule;
  ws: string;
  editable: boolean;
  demo: boolean;
  onChange: (s: Schedule) => void;
  onDelete: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const base = `/workspaces/${ws}/schedules/${schedule.id}`;
  async function act(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }
  const patch = (body: Partial<Schedule>) => act("patch", async () => onChange(await api.patch<Schedule>(base, body)));

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={schedule.enabled ? "good" : "neutral"} icon={CalendarClock}>
              {schedule.enabled ? schedule.description : "Paused"}
            </Badge>
            {schedule.plan_title && <span className="text-xs text-ink-3">{schedule.plan_title}</span>}
            {schedule.open_alerts > 0 && <Badge tone="bad">{schedule.open_alerts} open alert{schedule.open_alerts === 1 ? "" : "s"}</Badge>}
          </div>
          <p className="font-medium">{schedule.goal}</p>
          <p className="text-xs text-ink-3">
            {schedule.enabled ? `Next run ${new Date(schedule.next_run_at).toLocaleString()}` : demo ? "The demo workspace's data is static, so its schedule stays paused." : "Resume to start running again."}
            {schedule.last_run_at && ` · last ran ${timeAgo(schedule.last_run_at)}`}
            {schedule.source_run_id && (
              <>
                {" · "}
                <Link href={`/w/${ws}/runs/${schedule.source_run_id}`} className="text-accent hover:underline">
                  approved plan
                </Link>
              </>
            )}
          </p>
        </div>
        {editable && (
          <div className="flex shrink-0 flex-wrap gap-1.5">
            <Button size="sm" loading={busy === "run"} onClick={() => void act("run", async () => onChange(await api.post<Schedule>(`${base}/run`)))}>
              <RotateCw className="size-4" aria-hidden /> Run now
            </Button>
            <Button size="sm" variant="ghost" loading={busy === "patch"} onClick={() => void patch({ enabled: !schedule.enabled })}>
              {schedule.enabled ? <Pause className="size-4" aria-hidden /> : <Play className="size-4" aria-hidden />}
              {schedule.enabled ? "Pause" : "Resume"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              loading={busy === "delete"}
              aria-label="Delete schedule"
              onClick={() =>
                void act("delete", async () => {
                  if (!window.confirm("Delete this schedule? Past runs and alerts stay.")) return;
                  await api.delete(base);
                  onDelete();
                })
              }
            >
              <Trash2 className="size-4" aria-hidden />
            </Button>
          </div>
        )}
      </div>
      {editable && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-ink-3">Runs</span>
          <select className={select} value={schedule.cadence} onChange={(e) => void patch({ cadence: e.target.value as Schedule["cadence"] })} aria-label="Cadence">
            <option value="daily">every day</option>
            <option value="weekly">every week</option>
          </select>
          {schedule.cadence === "weekly" && (
            <select className={select} value={schedule.weekday} onChange={(e) => void patch({ weekday: Number(e.target.value) })} aria-label="Weekday">
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={i}>
                  on {d}
                </option>
              ))}
            </select>
          )}
          <select className={select} value={schedule.hour} onChange={(e) => void patch({ hour: Number(e.target.value) })} aria-label="Hour (UTC)">
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={h}>
                at {String(h).padStart(2, "0")}:00 UTC
              </option>
            ))}
          </select>
        </div>
      )}
      {schedule.recent_runs.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3 text-xs text-ink-3">
          <span>Recent runs</span>
          {schedule.recent_runs.map((r) => (
            <Link key={r.id} href={`/w/${ws}/runs/${r.id}`} className="inline-flex items-center gap-1.5 rounded-lg px-1.5 py-0.5 hover:bg-surface-2">
              <RunStatusBadge status={r.status} />
              {timeAgo(r.created_at)}
            </Link>
          ))}
        </div>
      )}
      {error && <p className="text-sm text-bad">{error}</p>}
    </Card>
  );
}

export default function MonitorsPage() {
  const { ws } = useParams<{ ws: string }>();
  const workspace = useWorkspace(ws);
  const editable = canEdit(workspace);
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<Schedule[]>(`/workspaces/${ws}/schedules`).then(setSchedules, (e) => setError(e.message));
    api.get<Alert[]>(`/workspaces/${ws}/alerts`).then(setAlerts, () => {});
  }, [ws]);
  useEffect(load, [load]);

  const visible = showAll ? alerts : alerts.filter((a) => a.status === "open");

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Monitors</h1>
        <p className="text-sm text-ink-2">
          Scheduled re-runs of plans you approved. Each run refreshes its extracts and reuses the same plan, so results stay comparable. A metric check that
          crosses its threshold raises an alert here.
        </p>
      </div>
      {error && <ErrorBanner message={error} />}

      <section>
        <SectionTitle
          action={
            alerts.some((a) => a.status !== "open") && (
              <button onClick={() => setShowAll(!showAll)} className="text-sm text-accent hover:underline">
                {showAll ? "Open only" : "Show acknowledged"}
              </button>
            )
          }
        >
          Alerts
        </SectionTitle>
        {visible.length === 0 ? (
          <p className="text-sm text-ink-3">No open alerts.</p>
        ) : (
          <div className="space-y-3">
            {visible.map((a) => (
              <AlertCard key={a.id} alert={a} ws={ws} editable={editable} onChange={(next) => setAlerts((list) => list.map((x) => (x.id === next.id ? next : x)))} />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionTitle>Schedules</SectionTitle>
        {!schedules && !error && <Spinner />}
        {schedules?.length === 0 && (
          <EmptyState icon={CalendarClock} title="Nothing scheduled yet">
            Ask something like <span className="font-medium">&ldquo;Alert me when conversion drops&rdquo;</span> to set up a monitor, or open any completed run and
            schedule it to re-run daily or weekly.
          </EmptyState>
        )}
        <div className="space-y-3">
          {schedules?.map((s) => (
            <ScheduleCard
              key={s.id}
              schedule={s}
              ws={ws}
              editable={editable}
              demo={workspace.is_demo}
              onChange={(next) => {
                setSchedules((list) => list?.map((x) => (x.id === next.id ? next : x)) ?? null);
                setTimeout(load, 1500); // a manual run shows up once the worker picks it up
              }}
              onDelete={() => setSchedules((list) => list?.filter((x) => x.id !== s.id) ?? null)}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
