"use client";

import { Lightbulb, ShieldAlert, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge, Card, cx, EmptyState, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo, titleCase } from "@/lib/format";
import type { InsightFeedItem } from "@/lib/types";

const FILTERS = [
  { key: "all", label: "All", query: "" },
  { key: "draft", label: "Awaiting review", query: "?status=draft" },
  { key: "accepted", label: "Accepted", query: "?status=accepted" },
  { key: "unverified", label: "Unverified", query: "?verified=false" },
];
const STATUS_TONE = { draft: "neutral", accepted: "good", edited: "accent", rejected: "bad" } as const;

export default function InsightsPage() {
  const { ws } = useParams<{ ws: string }>();
  const [filter, setFilter] = useState("all");
  const [items, setItems] = useState<InsightFeedItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(null);
    const query = FILTERS.find((f) => f.key === filter)?.query ?? "";
    api.get<InsightFeedItem[]>(`/workspaces/${ws}/insights${query}`).then(setItems, (e) => setError(e.message));
  }, [ws, filter]);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
        <p className="text-sm text-ink-2">Every finding from every run, with its verification and review status.</p>
      </div>
      <div className="flex flex-wrap gap-1" role="tablist">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            role="tab"
            aria-selected={filter === f.key}
            onClick={() => setFilter(f.key)}
            className={cx("rounded-lg px-3 py-1.5 text-sm", filter === f.key ? "bg-accent-soft text-accent" : "text-ink-2 hover:bg-surface-2")}
          >
            {f.label}
          </button>
        ))}
      </div>
      {error && <ErrorBanner message={error} />}
      {!items && !error && <Spinner />}
      {items?.length === 0 && <EmptyState icon={Lightbulb} title="Nothing here yet">Insights appear when runs complete.</EmptyState>}
      <div className="space-y-3">
        {items?.map((i) => (
          <Card key={i.id} className={cx("p-4", i.status === "rejected" && "opacity-60")}>
            <div className="flex flex-wrap items-center gap-2">
              {i.verified ? (
                <Badge tone="good" icon={ShieldCheck}>
                  Verified
                </Badge>
              ) : (
                <Badge tone="warn" icon={ShieldAlert}>
                  Unverified
                </Badge>
              )}
              <Badge tone={STATUS_TONE[i.status]}>{titleCase(i.status)}</Badge>
              <Badge>{titleCase(i.confidence)} confidence</Badge>
              <span className="ml-auto text-xs text-ink-3">{timeAgo(i.created_at)}</span>
            </div>
            <h2 className="mt-2 font-semibold">{i.title}</h2>
            <p className="mt-1 text-sm leading-relaxed">{i.claim}</p>
            <Link href={`/w/${ws}/runs/${i.run_id}`} className="mt-2 inline-block text-xs text-accent hover:underline">
              From: {i.run_goal}
            </Link>
          </Card>
        ))}
      </div>
    </div>
  );
}
