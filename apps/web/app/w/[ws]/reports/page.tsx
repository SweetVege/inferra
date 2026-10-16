"use client";

import { FileText, Link2, Plus } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { canEdit, useWorkspace } from "@/components/session";
import { Badge, Button, Card, EmptyState, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { REPORT_STATE as STATE } from "@/lib/report-state";
import { timeAgo } from "@/lib/format";
import type { ReportDetail, ReportSummary } from "@/lib/types";

export default function ReportsPage() {
  const { ws } = useParams<{ ws: string }>();
  const router = useRouter();
  const editable = canEdit(useWorkspace(ws));
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<ReportSummary[]>(`/workspaces/${ws}/reports`).then(setReports, (e) => setError(e.message));
  }, [ws]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const report = await api.post<ReportDetail>(`/workspaces/${ws}/reports`, { title });
      router.push(`/w/${ws}/reports/${report.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the report.");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
        <p className="text-sm text-ink-2">
          Turn reviewed insights into a report with charts and a method appendix. Publish it to freeze a version, then share it by link with people who do
          not have an Inferra account.
        </p>
      </div>
      {editable && (
        <form onSubmit={create} className="flex flex-col gap-2 sm:flex-row">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Report title, e.g. Weekly product review"
            required
            maxLength={300}
            className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
            aria-label="Report title"
          />
          <Button variant="primary" type="submit" loading={busy}>
            <Plus className="size-4" aria-hidden /> New report
          </Button>
        </form>
      )}
      {error && <ErrorBanner message={error} />}
      {!reports && !error && <Spinner />}
      {reports?.length === 0 && (
        <EmptyState icon={FileText} title="No reports yet">
          Accept or edit insights on a run, then collect them into a report here.
        </EmptyState>
      )}
      {reports && reports.length > 0 && (
        <Card className="divide-y divide-line">
          {reports.map((r) => (
            <Link key={r.id} href={`/w/${ws}/reports/${r.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-surface-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{r.title}</span>
              <span className="flex shrink-0 items-center gap-3 text-xs text-ink-3">
                <span>
                  {r.insight_count} finding{r.insight_count === 1 ? "" : "s"}
                </span>
                {r.shared && (
                  <span className="inline-flex items-center gap-1">
                    <Link2 className="size-3.5" aria-hidden /> Shared
                  </span>
                )}
                <Badge tone={STATE[r.state].tone}>{STATE[r.state].label}</Badge>
                <span>{timeAgo(r.updated_at)}</span>
              </span>
            </Link>
          ))}
        </Card>
      )}
    </div>
  );
}
