"use client";

import { Check, ChevronRight, ShieldAlert, ShieldCheck, UserCheck } from "lucide-react";

import { ArtifactView } from "@/components/artifact-view";
import { AGENTS, Badge, Card, CodeBlock } from "@/components/ui";
import { formatNumber, titleCase } from "@/lib/format";
import type { ReportContent } from "@/lib/types";

function dateOf(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" }) : "";
}

/** A report as readers see it: findings first, each with the chart behind it, then an appendix
 * with the method and the code for every cited number. Used by the public page and the editor. */
export function ReportView({ content, draft = false }: { content: ReportContent; draft?: boolean }) {
  const artifacts = new Map(content.artifacts.map((a) => [a.id, a]));
  const shown = new Set<string>();
  const verified = content.insights.filter((i) => i.verified).length;

  return (
    <article className="space-y-10">
      <header className="space-y-3 border-b border-line pb-6">
        <p className="text-xs font-medium tracking-wide text-ink-3 uppercase">{content.workspace} · Report</p>
        <h1 className="text-2xl font-semibold tracking-tight text-balance md:text-3xl">{content.title}</h1>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-ink-3">
          {draft ? <Badge tone="warn">Draft preview</Badge> : content.published_at && <span>Published {dateOf(content.published_at)}{content.published_by && ` by ${content.published_by}`}</span>}
          <span className="inline-flex items-center gap-1">
            <ShieldCheck className="size-4 text-good" aria-hidden /> {verified} of {content.insights.length} findings verified
          </span>
          <span className="inline-flex items-center gap-1">
            <UserCheck className="size-4 text-accent" aria-hidden /> Every finding reviewed by a person
          </span>
        </div>
        {content.summary && (
          <div className="space-y-3 pt-2 text-[15px] leading-relaxed">
            {content.summary.split(/\n{2,}/).map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </div>
        )}
      </header>

      {content.insights.length === 0 && <p className="text-sm text-ink-3">No findings yet. Add reviewed insights to the report.</p>}

      <ol className="space-y-8">
        {content.insights.map((insight, n) => {
          const primary = insight.evidence.map((e) => e.artifact_id).find((id) => artifacts.has(id) && !shown.has(id));
          if (primary) shown.add(primary);
          const artifact = primary ? artifacts.get(primary) : undefined;
          return (
            <li key={insight.id} className="space-y-3">
              <div className="flex items-start gap-3">
                <span className="tabular mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent">{n + 1}</span>
                <div className="min-w-0 flex-1 space-y-2">
                  <h2 className="text-lg font-semibold leading-snug">{insight.title}</h2>
                  <p className="text-[15px] leading-relaxed">{insight.claim}</p>
                  <div className="flex flex-wrap gap-2">
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
                    {insight.edited && <Badge tone="accent">Edited by reviewer</Badge>}
                    {insight.reviewed_by && <Badge>Reviewed by {insight.reviewed_by}</Badge>}
                  </div>
                  {insight.recommended_action && (
                    <p className="text-sm">
                      <span className="font-medium">Next step: </span>
                      <span className="text-ink-2">{insight.recommended_action}</span>
                    </p>
                  )}
                </div>
              </div>
              {artifact && (
                <Card className="p-4 md:ml-10">
                  <p className="mb-3 text-sm font-medium">{artifact.title}</p>
                  <ArtifactView artifact={artifact} />
                </Card>
              )}
              <details className="group text-sm md:ml-10">
                <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-accent hover:underline">
                  <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden />
                  Evidence and caveats
                </summary>
                <div className="mt-3 space-y-3">
                  <ul className="space-y-1">
                    {insight.evidence.map((e, i) => (
                      <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                        <span className="tabular font-medium">{formatNumber(e.value, 4)}</span>
                        <code className="font-mono text-xs break-all text-ink-3">{e.path}</code>
                        <span className="text-xs text-ink-3">in {artifacts.get(e.artifact_id)?.title ?? e.artifact_id}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="text-ink-2">
                    <span className="font-medium text-ink">Method: </span>
                    {insight.method}
                  </p>
                  {insight.caveats.length > 0 && (
                    <ul className="list-disc space-y-0.5 pl-5 text-ink-2">
                      {insight.caveats.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  )}
                  <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-3">
                    {insight.checks.map((c) => (
                      <li key={c.name} className="inline-flex items-center gap-1">
                        <Check className={c.passed ? "size-3.5 text-good" : "size-3.5 text-bad"} aria-hidden />
                        {titleCase(c.name)}
                      </li>
                    ))}
                  </ul>
                </div>
              </details>
            </li>
          );
        })}
      </ol>

      {content.appendix.length > 0 && (
        <section className="space-y-4 border-t border-line pt-6">
          <h2 className="text-[13px] font-semibold tracking-wide text-ink-3 uppercase">Appendix: how the numbers were produced</h2>
          {content.appendix.map((a) => (
            <div key={a.run_id} className="space-y-2 text-sm">
              <p className="font-medium">{a.goal}</p>
              <p className="text-ink-3">
                {a.plan_title}
                {a.ran_at && ` · ran ${dateOf(a.ran_at)}`}
                {a.datasets.length > 0 && ` · ${a.datasets.map((d) => `${d.name} (${formatNumber(d.rows)} rows)`).join(", ")}`}
              </p>
              <ol className="list-decimal space-y-1 pl-5 text-ink-2">
                {a.steps.map((s, i) => (
                  <li key={i}>
                    <span className="font-medium text-ink">{AGENTS[s.agent]?.title ?? s.agent}:</span> {s.summary ?? s.task}
                  </li>
                ))}
              </ol>
              {a.computations.map((c) => (
                <details key={c.artifact_id} className="group">
                  <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-accent hover:underline">
                    <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden />
                    {c.title}
                    {c.tool && <code className="font-mono text-xs text-ink-3">{c.tool}</code>}
                  </summary>
                  {c.code && (
                    <div className="mt-2">
                      <CodeBlock>{c.code}</CodeBlock>
                    </div>
                  )}
                </details>
              ))}
            </div>
          ))}
        </section>
      )}
      <footer className="border-t border-line pt-4 text-xs text-ink-3">
        Produced with Inferra. Language models reason, tools compute, people decide: every number above was checked against the tool output that produced it, and a
        person accepted each finding before it was published.
      </footer>
    </article>
  );
}
