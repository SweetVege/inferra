"use client";

import { CheckCircle2, AlertTriangle } from "lucide-react";
import { useState } from "react";

import { DataTable } from "@/components/data-table";
import { Badge } from "@/components/ui";
import { formatNumber, formatPercent } from "@/lib/format";

type Row = Record<string, unknown>;
const num = (v: unknown) => (typeof v === "number" ? v : NaN);

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-[11px] text-ink-3">{label}</dt>
      <dd className="tabular text-lg font-semibold">{value}</dd>
      {hint && <dd className="text-[11px] text-ink-3">{hint}</dd>}
    </div>
  );
}

export function SrmBadge({ ok, p }: { ok: boolean; p: number }) {
  return ok ? (
    <Badge tone="good" icon={CheckCircle2}>
      Sample ratio OK (p = {formatNumber(p, 3)})
    </Badge>
  ) : (
    <Badge tone="bad" icon={AlertTriangle}>
      Sample ratio mismatch (p = {p.toExponential(1)})
    </Badge>
  );
}

/** Interval plot of relative lift with its confidence interval, one row per comparison. */
export function LiftIntervals({ rows, label }: { rows: { label: string; lift: number; low: number; high: number; note?: string }[]; label: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const valid = rows.filter((r) => [r.lift, r.low, r.high].every(Number.isFinite));
  if (valid.length === 0) return null;
  const extent = Math.max(0.01, ...valid.flatMap((r) => [Math.abs(r.low), Math.abs(r.high)])) * 1.15;
  const x = (v: number) => ((v + extent) / (2 * extent)) * 100;
  return (
    <figure className="space-y-1" aria-label={label}>
      <div className="relative" onMouseLeave={() => setHover(null)}>
        <div className="absolute inset-y-0 border-l border-dashed border-ink-3" style={{ left: `calc(7.5rem + (100% - 7.5rem - 9rem) * ${x(0) / 100})` }} aria-hidden />
        {valid.map((r, i) => (
          <div key={r.label} className="grid h-8 grid-cols-[7.5rem_1fr_9rem] items-center gap-0 text-xs" onMouseEnter={() => setHover(i)}>
            <span className="truncate pr-2 text-ink-2">{r.label}</span>
            <div className="relative h-full">
              <div
                className="absolute top-1/2 h-[2px] -translate-y-1/2 rounded-full bg-series-1"
                style={{ left: `${x(r.low)}%`, width: `${Math.max(0.5, x(r.high) - x(r.low))}%`, opacity: hover == null || hover === i ? 1 : 0.4 }}
              />
              <div
                className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-series-1"
                style={{ left: `${x(r.lift)}%` }}
              />
            </div>
            <span className="tabular pl-2 text-right text-ink-2">
              {formatPercent(r.lift)} <span className="text-ink-3">[{formatPercent(r.low)}, {formatPercent(r.high)}]</span>
            </span>
          </div>
        ))}
      </div>
      <figcaption className="flex justify-between pl-[7.5rem] pr-[9rem] text-[11px] text-ink-3">
        <span>{formatPercent(-extent, 0)}</span>
        <span>0%</span>
        <span>{formatPercent(extent, 0)}</span>
      </figcaption>
      {hover != null && valid[hover].note && <p className="text-xs text-ink-3">{valid[hover].note}</p>}
    </figure>
  );
}

function comparisonRows(comparisons: Row[], key = "treatment") {
  return comparisons.map((c) => ({
    label: String(c[key]),
    lift: num(c.relative_lift),
    low: num(c.relative_lift_ci_low),
    high: num(c.relative_lift_ci_high),
    note: `p = ${formatNumber(c.p_value, 4)}${c.p_value_adjusted != null ? `, adjusted p = ${formatNumber(c.p_value_adjusted, 4)}` : ""}`,
  }));
}

function comparisonTable(comparisons: Row[], first: string) {
  return (
    <DataTable
      columns={[first, "mean_control", "mean_treatment", "diff", "relative_lift", "p_value", "p_value_adjusted"]}
      rows={comparisons.map((c) => ({ ...c, relative_lift: c.relative_lift == null ? "—" : formatPercent(num(c.relative_lift), 2) }))}
    />
  );
}

export function AbTestView({ d, cuped }: { d: Row; cuped?: boolean }) {
  const comparisons = d.comparisons as Row[];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <SrmBadge ok={Boolean(d.srm_ok)} p={num(d.srm_p_value)} />
        <Badge>{String(d.metric_type)} metric</Badge>
        {cuped && <Badge tone="accent">CUPED on {String(d.covariate)}: variance −{formatPercent(num(d.variance_reduction))}</Badge>}
      </div>
      <LiftIntervals rows={comparisonRows(comparisons)} label={`Relative lift in ${String(d.metric)}`} />
      {comparisonTable(comparisons, "treatment")}
      <DataTable columns={["variant", "n", "mean", "std"]} rows={d.arms as Row[]} />
      {cuped && Array.isArray(d.unadjusted) && (
        <div className="space-y-1">
          <p className="text-xs text-ink-3">Without CUPED</p>
          <LiftIntervals rows={comparisonRows(d.unadjusted as Row[])} label="Unadjusted relative lift" />
        </div>
      )}
    </div>
  );
}

export function SegmentView({ d }: { d: Row }) {
  const segments = d.segments as Row[];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge>Holm-adjusted across {segments.length} segments</Badge>
        <Badge tone={num(d.heterogeneity_p_value) < num(d.alpha) ? "warn" : "neutral"}>
          Heterogeneity p = {formatNumber(d.heterogeneity_p_value, 3)}
        </Badge>
      </div>
      <LiftIntervals rows={comparisonRows(segments, "segment")} label={`Relative lift by ${String(d.segment_column)}`} />
      {comparisonTable(segments, "segment")}
    </div>
  );
}

export function SrmView({ d }: { d: Row }) {
  return (
    <div className="space-y-3">
      <SrmBadge ok={Boolean(d.srm_ok)} p={num(d.p_value)} />
      <DataTable
        columns={["variant", "count", "share", "expected_share"]}
        rows={(d.observed as Row[]).map((r) => ({ ...r, share: formatPercent(num(r.share), 2), expected_share: formatPercent(num(r.expected_share), 2) }))}
      />
    </div>
  );
}

export function PowerView({ d }: { d: Row }) {
  const rows = (d.mde_by_duration as Row[]) ?? [];
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Per variant" value={formatNumber(d.sample_size_per_variant)} />
        <Stat label="Total units" value={formatNumber(d.total_sample_size)} />
        <Stat label="Estimated duration" value={d.estimated_days ? `${d.estimated_days} days` : "—"} hint={d.daily_units ? `${formatNumber(d.daily_units)} units/day` : undefined} />
        <Stat label="Detectable lift" value={d.mde_relative != null ? formatPercent(num(d.mde_relative)) : formatNumber(d.mde_absolute)} hint={`α ${d.alpha}, power ${d.power}`} />
      </dl>
      {rows.length > 0 && (
        <DataTable
          columns={["days", "sample_size_per_variant", "detectable relative lift", "detectable absolute lift"]}
          rows={rows.map((r) => ({
            ...r,
            "detectable relative lift": r.mde_relative == null ? "—" : formatPercent(num(r.mde_relative)),
            "detectable absolute lift": formatNumber(r.mde_absolute, 4),
          }))}
        />
      )}
    </div>
  );
}

export function BayesianView({ d }: { d: Row }) {
  return (
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <Stat label="P(treatment better)" value={formatPercent(num(d.prob_treatment_better))} />
      <Stat label="Expected loss if shipped" value={formatNumber(d.expected_loss_treatment, 5)} />
      <Stat label="Mean relative lift" value={formatPercent(num(d.relative_lift_mean))} />
      <Stat label="95% credible interval" value={`${formatPercent(num(d.relative_lift_ci_low))} – ${formatPercent(num(d.relative_lift_ci_high))}`} hint={String(d.prior)} />
    </dl>
  );
}

/** Always-valid p-value at each daily look, against the alpha threshold. */
export function SequentialView({ d }: { d: Row }) {
  const looks = (d.looks as Row[]) ?? [];
  const alpha = num(d.alpha);
  const [hover, setHover] = useState<number | null>(null);
  const w = 600,
    h = 160,
    pad = { l: 36, r: 8, t: 8, b: 20 };
  const px = (i: number) => pad.l + (looks.length > 1 ? (i / (looks.length - 1)) * (w - pad.l - pad.r) : 0);
  const py = (p: number) => pad.t + (1 - p) * (h - pad.t - pad.b);
  const path = looks.map((lk, i) => `${i ? "L" : "M"}${px(i).toFixed(1)},${py(num(lk.always_valid_p_value)).toFixed(1)}`).join(" ");
  const active = hover != null ? looks[hover] : looks[looks.length - 1];
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Always-valid p-value" value={formatNumber(d.always_valid_p_value, 4)} />
        <Stat label="Decision" value={d.significant ? "Significant" : "Keep running"} hint={`α ${alpha}`} />
        <Stat label="First significant look" value={d.first_significant_day ? String(d.first_significant_day) : "None yet"} />
      </dl>
      {looks.length > 1 && (
        <figure>
          <svg
            viewBox={`0 0 ${w} ${h}`}
            className="w-full"
            role="img"
            aria-label="Always-valid p-value by day"
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const rel = ((e.clientX - rect.left) / rect.width) * w;
              const i = Math.round(((rel - pad.l) / (w - pad.l - pad.r)) * (looks.length - 1));
              setHover(Math.max(0, Math.min(looks.length - 1, i)));
            }}
            onMouseLeave={() => setHover(null)}
          >
            {[0, 0.5, 1].map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={w - pad.r} y1={py(t)} y2={py(t)} stroke="var(--border)" />
                <text x={pad.l - 6} y={py(t) + 3} textAnchor="end" fontSize="10" fill="var(--text-3)">
                  {t}
                </text>
              </g>
            ))}
            <line x1={pad.l} x2={w - pad.r} y1={py(alpha)} y2={py(alpha)} stroke="var(--text-3)" strokeDasharray="4 3" />
            <text x={w - pad.r} y={py(alpha) - 4} textAnchor="end" fontSize="10" fill="var(--text-3)">
              α = {alpha}
            </text>
            <path d={path} fill="none" stroke="var(--series-1)" strokeWidth="2" strokeLinejoin="round" />
            {hover != null && (
              <>
                <line x1={px(hover)} x2={px(hover)} y1={pad.t} y2={h - pad.b} stroke="var(--text-3)" strokeOpacity="0.5" />
                <circle cx={px(hover)} cy={py(num(looks[hover].always_valid_p_value))} r="4" fill="var(--series-1)" stroke="var(--surface)" strokeWidth="2" />
              </>
            )}
            <text x={pad.l} y={h - 4} fontSize="10" fill="var(--text-3)">
              {String(looks[0].day)}
            </text>
            <text x={w - pad.r} y={h - 4} textAnchor="end" fontSize="10" fill="var(--text-3)">
              {String(looks[looks.length - 1].day)}
            </text>
          </svg>
          {active && (
            <figcaption className="tabular text-xs text-ink-2">
              {String(active.day)}: p = {formatNumber(active.always_valid_p_value, 4)} with {formatNumber(active.n_control)} control and{" "}
              {formatNumber(active.n_treatment)} treatment units
            </figcaption>
          )}
        </figure>
      )}
    </div>
  );
}

export function BaselineView({ d }: { d: Row }) {
  return (
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <Stat label={d.metric_type === "binary" ? "Baseline rate" : "Baseline mean"} value={d.metric_type === "binary" ? formatPercent(num(d.mean), 2) : formatNumber(d.mean)} />
      <Stat label="Std. deviation" value={formatNumber(d.std, 4)} />
      <Stat label="Units" value={formatNumber(d.n)} />
      <Stat label="Units per day" value={d.daily_units ? formatNumber(d.daily_units) : "—"} hint={d.first_day ? `${d.first_day} – ${d.last_day}` : undefined} />
    </dl>
  );
}
