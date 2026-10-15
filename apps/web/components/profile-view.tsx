"use client";

import { AlertTriangle, Info } from "lucide-react";
import { useState } from "react";

import { Badge, Card, cx } from "@/components/ui";
import { formatNumber, formatPercent, titleCase } from "@/lib/format";
import type { ColumnProfile, Finding, HistogramBin, Profile, TopValue } from "@/lib/types";

/** Single-series histogram: one hue, 2px gaps, rounded data ends on the baseline, hover tooltip. */
export function Histogram({ bins, label }: { bins: HistogramBin[]; label: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...bins.map((b) => b.count));
  const active = hover == null ? null : bins[hover];
  return (
    <figure className="space-y-1">
      <div className="relative flex h-16 items-end gap-[2px]" role="img" aria-label={`Distribution of ${label}`} onMouseLeave={() => setHover(null)}>
        {bins.map((b, i) => (
          <div key={i} className="flex h-full flex-1 items-end" onMouseEnter={() => setHover(i)}>
            <div
              className={cx("w-full rounded-t-[4px] bg-series-1 transition-opacity", hover != null && hover !== i && "opacity-40")}
              style={{ height: `${Math.max(b.count > 0 ? 3 : 0, (b.count / max) * 100)}%` }}
            />
          </div>
        ))}
        {active && (
          <div className="pointer-events-none absolute -top-9 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-surface px-2 py-1 text-xs shadow-sm">
            <span className="text-ink-2">
              {formatNumber(active.bin_start)} – {formatNumber(active.bin_end)}:
            </span>{" "}
            <span className="tabular font-medium">{formatNumber(active.count)}</span>
          </div>
        )}
      </div>
      <figcaption className="tabular flex justify-between text-[11px] text-ink-3">
        <span>{formatNumber(bins[0]?.bin_start)}</span>
        <span>{formatNumber(bins[bins.length - 1]?.bin_end)}</span>
      </figcaption>
    </figure>
  );
}

export function TopValues({ values }: { values: TopValue[] }) {
  const max = Math.max(...values.map((v) => v.share), 0.0001);
  return (
    <ul className="space-y-1.5">
      {values.slice(0, 6).map((v) => (
        <li key={v.value} className="grid grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-2 text-xs" title={`${v.value}: ${formatNumber(v.count)} rows`}>
          <span className="truncate text-ink-2">{v.value}</span>
          <span className="h-2 rounded-r-[4px] bg-series-1" style={{ width: `${Math.max(2, (v.share / max) * 100)}%` }} />
          <span className="tabular text-ink-2">{formatPercent(v.share)}</span>
        </li>
      ))}
    </ul>
  );
}

const SEVERITY: Record<Finding["severity"], "bad" | "warn" | "neutral" | "accent"> = {
  high: "bad",
  medium: "warn",
  low: "neutral",
  info: "accent",
};

export function Findings({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) return <p className="text-sm text-ink-3">No data-quality issues found.</p>;
  return (
    <ul className="space-y-2">
      {findings.map((f, i) => (
        <li key={i} className="flex items-start gap-3 text-sm">
          {f.severity === "info" ? <Info className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden /> : <AlertTriangle className={cx("mt-0.5 size-4 shrink-0", f.severity === "high" ? "text-bad" : f.severity === "medium" ? "text-warn" : "text-ink-3")} aria-hidden />}
          <div className="min-w-0 flex-1">
            <span className="font-medium">{f.column ? <code className="font-mono text-[13px]">{f.column}</code> : "Table"}</span>{" "}
            <span className="text-ink-2">{f.detail}</span>{" "}
            <span className="tabular text-ink-3">
              ({f.metric.endsWith("rate") ? formatPercent(f.value) : formatNumber(f.value)})
            </span>
          </div>
          <Badge tone={SEVERITY[f.severity]}>{f.severity}</Badge>
        </li>
      ))}
    </ul>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] text-ink-3">{label}</dt>
      <dd className="tabular text-sm">{value}</dd>
    </div>
  );
}

export function ColumnCard({ name, col }: { name: string; col: ColumnProfile }) {
  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <code className="truncate font-mono text-sm font-medium">{name}</code>
        <Badge>{titleCase(col.kind)}</Badge>
      </div>
      <dl className="grid grid-cols-3 gap-2">
        <Stat label="Missing" value={formatPercent(col.null_rate)} />
        <Stat label="Distinct" value={formatNumber(col.distinct_count)} />
        {col.kind === "numeric" ? <Stat label="Median" value={formatNumber(col.median)} /> : <Stat label="Type" value={col.type.toLowerCase()} />}
      </dl>
      {col.kind === "numeric" && (
        <>
          <dl className="grid grid-cols-3 gap-2">
            <Stat label="Mean" value={formatNumber(col.mean)} />
            <Stat label="Min" value={formatNumber(col.min)} />
            <Stat label="Max" value={formatNumber(col.max)} />
          </dl>
          {col.histogram && <Histogram bins={col.histogram} label={name} />}
        </>
      )}
      {col.top_values && col.top_values.length > 0 && <TopValues values={col.top_values} />}
      {col.kind === "temporal" && (
        <dl className="grid grid-cols-2 gap-2">
          <Stat label="From" value={String(col.min ?? "—").slice(0, 10)} />
          <Stat label="To" value={String(col.max ?? "—").slice(0, 10)} />
        </dl>
      )}
    </Card>
  );
}

export function ProfileView({ profile }: { profile: Profile }) {
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-3 gap-4 sm:max-w-md">
        <Stat label="Rows" value={formatNumber(profile.row_count)} />
        <Stat label="Columns" value={formatNumber(profile.column_count)} />
        <Stat label="Duplicate rows" value={formatNumber(profile.duplicate_rows)} />
      </dl>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Data quality</h3>
        <Findings findings={profile.findings} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Object.entries(profile.columns).map(([name, col]) => (
          <ColumnCard key={name} name={name} col={col} />
        ))}
      </div>
    </div>
  );
}
