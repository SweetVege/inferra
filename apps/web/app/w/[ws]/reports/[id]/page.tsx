"use client";

import { ArrowDown, ArrowLeft, ArrowUp, Check, Copy, ExternalLink, Link2, Link2Off, Plus, Send, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ReportView } from "@/components/report-view";
import { canEdit, useWorkspace } from "@/components/session";
import { Badge, Button, Card, cx, ErrorBanner, SectionTitle, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { REPORT_STATE } from "@/lib/report-state";
import { timeAgo } from "@/lib/format";
import type { InsightFeedItem, ReportDetail } from "@/lib/types";

function ShareLink({ token }: { token: string }) {
  const [copied, setCopied] = useState(false);
  const url = typeof window === "undefined" ? `/r/${token}` : `${window.location.origin}/r/${token}`;
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  }
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <input readOnly value={url} onFocus={(e) => e.currentTarget.select()} className="h-8 min-w-0 flex-1 rounded-lg border border-line bg-surface-2 px-2 font-mono text-xs" aria-label="Share link" />
      <Button size="sm" onClick={() => void copy()} aria-label="Copy link">
        {copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}
      </Button>
      <a href={`/r/${token}`} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center rounded-lg px-2 text-ink-3 hover:bg-surface-2 hover:text-ink" aria-label="Open shared report">
        <ExternalLink className="size-4" />
      </a>
    </div>
  );
}

export default function ReportPage() {
  const { ws, id } = useParams<{ ws: string; id: string }>();
  const router = useRouter();
  const editable = canEdit(useWorkspace(ws));
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [reviewed, setReviewed] = useState<InsightFeedItem[]>([]);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"draft" | "published">("draft");

  const apply = useCallback((r: ReportDetail) => {
    setReport(r);
    setTitle(r.title);
    setSummary(r.summary);
  }, []);

  useEffect(() => {
    api.get<ReportDetail>(`/workspaces/${ws}/reports/${id}`).then(
      (r) => {
        apply(r);
        if (r.published && r.state === "published") setView("published");
      },
      (e) => setError(e.message),
    );
    api.get<InsightFeedItem[]>(`/workspaces/${ws}/insights?status=accepted,edited`).then(setReviewed, () => {});
  }, [ws, id, apply]);

  async function act(key: string, fn: () => Promise<ReportDetail | void>) {
    setBusy(key);
    setError(null);
    try {
      const next = await fn();
      if (next) apply(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }

  if (!report) return error ? <ErrorBanner message={error} /> : <Spinner />;
  const base = `/workspaces/${ws}/reports/${id}`;
  const dirty = title !== report.title || summary !== report.summary;
  const included = report.insight_ids;
  const byId = new Map(reviewed.map((i) => [i.id, i]));
  const available = reviewed.filter((i) => !included.includes(i.id));
  const setInsights = (ids: string[]) => act("insights", () => api.patch<ReportDetail>(base, { insight_ids: ids }));
  const move = (index: number, delta: number) => {
    const ids = [...included];
    const [item] = ids.splice(index, 1);
    ids.splice(index + delta, 0, item);
    void setInsights(ids);
  };
  const state = REPORT_STATE[report.state];
  const shown = view === "published" && report.published ? report.published : report.content;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div className="space-y-3">
        <Link href={`/w/${ws}/reports`} className="inline-flex items-center gap-1 text-sm text-ink-3 hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Reports
        </Link>
        <div className="flex flex-wrap items-center gap-3 text-sm text-ink-3">
          <Badge tone={state.tone}>{state.label}</Badge>
          {report.published_at && <span>Published {timeAgo(report.published_at)}</span>}
          <span>Edited {timeAgo(report.updated_at)}</span>
        </div>
      </div>

      {error && <ErrorBanner message={error} />}

      {editable && (
        <Card className="space-y-5 p-5">
          <div className="space-y-3">
            <label className="block space-y-1">
              <span className="text-sm font-medium">Title</span>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={300}
                className="h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-sm font-medium">Summary</span>
              <textarea
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                rows={4}
                placeholder="What should a reader take away? Write it in your own words; separate paragraphs with a blank line."
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm leading-relaxed outline-none focus:border-accent"
              />
            </label>
            {dirty && (
              <Button size="sm" variant="primary" loading={busy === "save"} onClick={() => void act("save", () => api.patch<ReportDetail>(base, { title, summary }))}>
                Save changes
              </Button>
            )}
          </div>

          <div>
            <SectionTitle>Findings in this report</SectionTitle>
            {included.length === 0 ? (
              <p className="text-sm text-ink-3">Add reviewed insights below. Only accepted or edited insights can be published.</p>
            ) : (
              <ol className="space-y-1.5">
                {report.content.insights.map((insight, i) => (
                  <li key={insight.id} className="flex items-center gap-2 rounded-lg border border-line px-3 py-2">
                    <span className="tabular w-5 shrink-0 text-sm text-ink-3">{i + 1}</span>
                    <span className="min-w-0 flex-1 truncate text-sm">{insight.title}</span>
                    {!["accepted", "edited"].includes(insight.status) && <Badge tone="bad">Needs review</Badge>}
                    <span className="flex shrink-0 gap-0.5">
                      <button disabled={i === 0 || !!busy} onClick={() => move(i, -1)} className="rounded p-1 text-ink-3 hover:bg-surface-2 disabled:opacity-30" aria-label="Move up">
                        <ArrowUp className="size-4" />
                      </button>
                      <button disabled={i === included.length - 1 || !!busy} onClick={() => move(i, 1)} className="rounded p-1 text-ink-3 hover:bg-surface-2 disabled:opacity-30" aria-label="Move down">
                        <ArrowDown className="size-4" />
                      </button>
                      <button disabled={!!busy} onClick={() => void setInsights(included.filter((x) => x !== insight.id))} className="rounded p-1 text-ink-3 hover:bg-surface-2 hover:text-bad" aria-label="Remove">
                        <X className="size-4" />
                      </button>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>

          {available.length > 0 && (
            <details className="group">
              <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-sm font-medium text-accent hover:underline">
                <Plus className="size-4" aria-hidden /> Add reviewed insights ({available.length})
              </summary>
              <ul className="mt-2 max-h-80 space-y-1.5 overflow-y-auto">
                {available.map((i) => (
                  <li key={i.id}>
                    <button
                      disabled={!!busy}
                      onClick={() => void setInsights([...included, i.id])}
                      className="flex w-full items-start gap-2 rounded-lg border border-line px-3 py-2 text-left hover:bg-surface-2 disabled:opacity-50"
                    >
                      <Plus className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
                      <span className="min-w-0">
                        <span className="block text-sm">{i.title}</span>
                        <span className="block truncate text-xs text-ink-3">From: {byId.get(i.id)?.run_goal}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <Button
              variant="primary"
              disabled={included.length === 0 || dirty || report.state === "published"}
              loading={busy === "publish"}
              onClick={() =>
                void act("publish", async () => {
                  const next = await api.post<ReportDetail>(`${base}/publish`);
                  setView("published");
                  return next;
                })
              }
            >
              <Send className="size-4" aria-hidden /> {report.state === "draft" ? "Publish" : report.state === "changed" ? "Publish changes" : "Published"}
            </Button>
            {report.published && !report.share_token && (
              <Button loading={busy === "share"} onClick={() => void act("share", () => api.post<ReportDetail>(`${base}/share`))}>
                <Link2 className="size-4" aria-hidden /> Create share link
              </Button>
            )}
            <Button
              variant="ghost"
              className="ml-auto"
              loading={busy === "delete"}
              onClick={() =>
                void act("delete", async () => {
                  if (!window.confirm("Delete this report? Its share link stops working.")) return;
                  await api.delete(base);
                  router.push(`/w/${ws}/reports`);
                })
              }
            >
              <Trash2 className="size-4" aria-hidden /> Delete
            </Button>
          </div>
          {report.share_token && (
            <div className="flex flex-wrap items-center gap-2">
              <ShareLink token={report.share_token} />
              <Button size="sm" variant="danger" loading={busy === "revoke"} onClick={() => void act("revoke", () => api.delete(`${base}/share`).then(() => api.get<ReportDetail>(base)))}>
                <Link2Off className="size-4" aria-hidden /> Revoke
              </Button>
              <p className="w-full text-xs text-ink-3">Anyone with the link can read the published version without signing in. Revoking breaks the link at once.</p>
            </div>
          )}
        </Card>
      )}

      {!editable && report.share_token && (
        <Card className="flex flex-wrap items-center gap-2 p-4">
          <span className="text-sm font-medium">Public link</span>
          <ShareLink token={report.share_token} />
        </Card>
      )}

      <section className="space-y-4">
        {report.published && report.state !== "draft" && (
          <div className="flex gap-1" role="tablist">
            {(["published", "draft"] as const).map((v) => (
              <button
                key={v}
                role="tab"
                aria-selected={view === v}
                onClick={() => setView(v)}
                className={cx("rounded-lg px-3 py-1.5 text-sm", view === v ? "bg-accent-soft text-accent" : "text-ink-2 hover:bg-surface-2")}
              >
                {v === "published" ? "Published version" : "Current draft"}
              </button>
            ))}
          </div>
        )}
        <Card className="p-5 md:p-8">
          <ReportView content={shown} draft={shown === report.content && report.state !== "published"} />
        </Card>
      </section>
    </div>
  );
}
