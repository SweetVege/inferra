"use client";

import { Check, ChevronDown, Pencil, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber, titleCase } from "@/lib/format";
import type { Artifact, Insight, InsightStatus } from "@/lib/types";

const STATUS: Record<InsightStatus, { label: string; tone: "neutral" | "good" | "accent" | "bad" }> = {
  draft: { label: "Awaiting review", tone: "neutral" },
  accepted: { label: "Accepted", tone: "good" },
  edited: { label: "Edited", tone: "accent" },
  rejected: { label: "Rejected", tone: "bad" },
};

interface Draft {
  title: string;
  claim: string;
  recommended_action: string;
}

const draftOf = (i: Insight): Draft => ({ title: i.title, claim: i.claim, recommended_action: i.recommended_action ?? "" });

const field = "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-accent";

export function InsightCard({
  insight,
  workspaceId,
  artifacts,
  editable,
  onChange,
  onShowArtifact,
}: {
  insight: Insight;
  workspaceId: string;
  artifacts: Artifact[];
  editable: boolean;
  onChange: (next: Insight) => void;
  onShowArtifact: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(draftOf(insight));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function review(status: InsightStatus, edit?: Draft) {
    setBusy(status);
    setError(null);
    try {
      const next = await api.patch<Insight>(`/workspaces/${workspaceId}/insights/${insight.id}`, { status, ...edit });
      onChange(next);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the review.");
    } finally {
      setBusy(null);
    }
  }

  const titleOf = (id: string) => artifacts.find((a) => a.id === id)?.title ?? id;
  const status = STATUS[insight.status];

  return (
    <Card className={cx("p-5", insight.status === "rejected" && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-2">
        {insight.verified ? (
          <Badge tone="good" icon={ShieldCheck}>
            Verified
          </Badge>
        ) : (
          <Badge tone="warn" icon={ShieldAlert}>
            Unverified
          </Badge>
        )}
        <Badge>{titleCase(insight.confidence)} confidence</Badge>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      {editing ? (
        <div className="mt-3 space-y-3">
          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink-3">Title</span>
            <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} maxLength={300} className={cx(field, "h-9 font-semibold")} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink-3">Claim</span>
            <textarea value={draft.claim} onChange={(e) => setDraft({ ...draft, claim: e.target.value })} rows={4} className={field} />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink-3">Next step</span>
            <textarea
              value={draft.recommended_action}
              onChange={(e) => setDraft({ ...draft, recommended_action: e.target.value })}
              rows={2}
              placeholder="Leave empty to remove the next step."
              className={field}
            />
          </label>
          <p className="text-xs text-ink-3">The verifier checks your edit: every number must still match the cited evidence.</p>
          <div className="flex gap-2">
            <Button size="sm" variant="primary" loading={busy === "edited"} onClick={() => void review("edited", draft)}>
              Save edit
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <h3 className="mt-3 text-base font-semibold">{insight.title}</h3>
          <p className="mt-1 text-[15px] leading-relaxed">{insight.claim}</p>
          {insight.recommended_action && (
            <p className="mt-3 text-sm">
              <span className="font-medium">Next step: </span>
              <span className="text-ink-2">{insight.recommended_action}</span>
            </p>
          )}
          {(insight.original_title != null || insight.original_claim != null || insight.original_action != null) && (
            <details className="mt-2 text-xs text-ink-3">
              <summary className="cursor-pointer">Edited by a reviewer. Show what the agent wrote</summary>
              <div className="mt-1 space-y-1 border-l-2 border-line pl-3">
                {insight.original_title != null && <p>Title: {insight.original_title}</p>}
                {insight.original_claim != null && <p>Claim: {insight.original_claim}</p>}
                {insight.original_action != null && <p>Next step: {insight.original_action || "(none)"}</p>}
              </div>
            </details>
          )}
        </>
      )}

      <button onClick={() => setOpen(!open)} className="mt-4 inline-flex items-center gap-1 text-sm text-accent hover:underline" aria-expanded={open}>
        <ChevronDown className={cx("size-4 transition-transform", open && "rotate-180")} aria-hidden />
        Evidence, method, and checks
      </button>
      {open && (
        <div className="mt-3 space-y-4 border-t border-line pt-4 text-sm">
          <div>
            <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-3">Evidence</h4>
            <ul className="space-y-1">
              {insight.evidence.map((e, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="tabular font-medium">{formatNumber(e.value, 4)}</span>
                  <code className="font-mono text-xs text-ink-3">{e.path}</code>
                  <button onClick={() => onShowArtifact(e.artifact_id)} className="text-xs text-accent hover:underline">
                    {titleOf(e.artifact_id)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-3">Method</h4>
            <p className="text-ink-2">{insight.method}</p>
          </div>
          {insight.caveats.length > 0 && (
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-3">Caveats</h4>
              <ul className="list-disc space-y-0.5 pl-5 text-ink-2">
                {insight.caveats.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-3">Verifier checks</h4>
            <ul className="space-y-1">
              {insight.checks.map((c) => (
                <li key={c.name} className="flex items-start gap-2">
                  {c.passed ? <Check className="mt-0.5 size-4 shrink-0 text-good" aria-label="passed" /> : <X className="mt-0.5 size-4 shrink-0 text-bad" aria-label="failed" />}
                  <span>
                    <span className="font-medium">{titleCase(c.name)}</span> <span className="text-ink-2">{c.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {editable && !editing && (
        <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4">
          <Button size="sm" variant={insight.status === "accepted" ? "primary" : "secondary"} loading={busy === "accepted"} onClick={() => void review("accepted")}>
            <Check className="size-4" aria-hidden /> Accept
          </Button>
          <Button size="sm" onClick={() => { setDraft(draftOf(insight)); setEditing(true); }}>
            <Pencil className="size-4" aria-hidden /> Edit
          </Button>
          <Button size="sm" variant="danger" loading={busy === "rejected"} onClick={() => void review("rejected")}>
            <X className="size-4" aria-hidden /> Reject
          </Button>
          {insight.status !== "draft" && (
            <Button size="sm" variant="ghost" loading={busy === "draft"} onClick={() => void review("draft")}>
              Reset
            </Button>
          )}
        </div>
      )}
      {error && <p className="mt-2 text-sm text-bad">{error}</p>}
    </Card>
  );
}
