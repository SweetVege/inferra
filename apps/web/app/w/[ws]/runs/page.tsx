"use client";

import { History } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Card, EmptyState, ErrorBanner, RunStatusBadge, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo, titleCase } from "@/lib/format";
import type { RunSummary } from "@/lib/types";

export default function RunsPage() {
  const { ws } = useParams<{ ws: string }>();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<RunSummary[]>(`/workspaces/${ws}/runs`).then(setRuns, (e) => setError(e.message));
  }, [ws]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
      {error && <ErrorBanner message={error} />}
      {!runs && !error && <Spinner />}
      {runs?.length === 0 && (
        <EmptyState icon={History} title="No runs yet">
          Ask a question on the <Link href={`/w/${ws}`} className="text-accent hover:underline">Ask</Link> page to start one.
        </EmptyState>
      )}
      {runs && runs.length > 0 && (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-line text-left text-xs text-ink-3">
              <tr>
                <th className="px-4 py-2.5 font-medium">Goal</th>
                <th className="px-4 py-2.5 font-medium">Workflow</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 text-right font-medium">Insights</th>
                <th className="px-4 py-2.5 text-right font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {runs.map((r) => (
                <tr key={r.id} className="hover:bg-surface-2">
                  <td className="max-w-md px-4 py-3">
                    <Link href={`/w/${ws}/runs/${r.id}`} className="line-clamp-2 hover:underline">
                      {r.goal}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-ink-2">
                    {r.intent ? titleCase(r.intent) : "—"}
                    {r.schedule_id && <span className="block text-xs text-ink-3">Scheduled re-run</span>}
                  </td>
                  <td className="px-4 py-3">
                    <RunStatusBadge status={r.status} />
                  </td>
                  <td className="tabular px-4 py-3 text-right">{r.insight_count || "—"}</td>
                  <td className="px-4 py-3 text-right text-ink-3">{timeAgo(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
