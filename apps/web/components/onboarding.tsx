"use client";

import { Check, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Card, cx } from "@/components/ui";
import { api } from "@/lib/api";
import type { Overview, Workspace } from "@/lib/types";

interface Item {
  title: string;
  text: string;
  href: string;
  done: boolean;
}

function items(ws: Workspace, o: Overview): Item[] {
  const base = `/w/${ws.id}`;
  if (ws.is_demo) {
    return [
      { title: "Follow a run", text: "See the plan, every agent step, the tool calls, and the verifier's checks.", href: `${base}/runs`, done: false },
      { title: "Read the shared report", text: "Reviewed findings with their charts and a method appendix, readable by link.", href: `${base}/reports`, done: false },
      { title: "Check the monitor", text: "A weekly conversion check that raised an alert.", href: `${base}/monitors`, done: false },
    ];
  }
  return [
    { title: "Add data", text: "Upload a CSV, Parquet, or Excel file, or connect Postgres, BigQuery, or Snowflake.", href: `${base}/datasets`, done: o.datasets > 0 },
    { title: "Ask a question", text: "Describe a goal below, then approve the plan the Orchestrator proposes.", href: base, done: o.runs > 0 },
    { title: "Review an insight", text: "Accept, edit, or reject each finding after checking its evidence.", href: `${base}/insights`, done: o.reviewed_insights > 0 },
    { title: "Publish a report", text: "Collect reviewed insights into a report and share it by link.", href: `${base}/reports`, done: o.published_reports > 0 },
    { title: "Monitor a metric", text: "Ask “Alert me when conversion drops” or schedule any completed run.", href: `${base}/monitors`, done: o.schedules > 0 },
  ];
}

/** Getting started: a short checklist driven by what the workspace already has. Dismissible. */
export function Onboarding({ workspace }: { workspace: Workspace }) {
  const key = `inferra:onboarding:${workspace.id}`;
  const [overview, setOverview] = useState<Overview | null>(null);
  const [hidden, setHidden] = useState(true);

  useEffect(() => {
    let dismissed = false;
    try {
      dismissed = localStorage.getItem(key) === "dismissed";
    } catch {}
    setHidden(dismissed);
    if (!dismissed) api.get<Overview>(`/workspaces/${workspace.id}/overview`).then(setOverview, () => {});
  }, [key, workspace.id]);

  if (hidden || !overview) return null;
  const list = items(workspace, overview);
  const done = list.filter((i) => i.done).length;
  if (!workspace.is_demo && done === list.length) return null;
  const next = list.findIndex((i) => !i.done);

  function dismiss() {
    try {
      localStorage.setItem(key, "dismissed");
    } catch {}
    setHidden(true);
  }

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">{workspace.is_demo ? "Take a tour of the demo" : "Get started"}</h2>
          <p className="text-sm text-ink-2">
            {workspace.is_demo
              ? "This read-only workspace shows completed work on sample data. Sign in with GitHub to analyze your own."
              : `${done} of ${list.length} done. Each step takes a minute or two.`}
          </p>
        </div>
        <button onClick={dismiss} className="rounded-lg p-1.5 text-ink-3 hover:bg-surface-2 hover:text-ink" aria-label="Dismiss getting started">
          <X className="size-4" />
        </button>
      </div>
      {!workspace.is_demo && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${(done / list.length) * 100}%` }} />
        </div>
      )}
      <ol className="mt-4 grid gap-2 sm:grid-cols-2">
        {list.map((item, i) => (
          <li key={item.title}>
            <Link
              href={item.href}
              className={cx(
                "flex h-full items-start gap-3 rounded-lg border px-3 py-2.5 hover:bg-surface-2",
                i === next && !workspace.is_demo ? "border-accent" : "border-line",
              )}
            >
              <span
                className={cx(
                  "tabular mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                  item.done ? "bg-good text-bg" : "bg-surface-2 text-ink-2",
                )}
              >
                {item.done ? <Check className="size-3.5" aria-label="done" /> : i + 1}
              </span>
              <span className="min-w-0">
                <span className={cx("block text-sm font-medium", item.done && "text-ink-3 line-through")}>{item.title}</span>
                <span className="block text-xs text-ink-2">{item.text}</span>
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </Card>
  );
}
