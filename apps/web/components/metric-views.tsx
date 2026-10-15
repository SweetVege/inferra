"use client";

import { DivergingBars, LineChart, type Series } from "@/components/charts";
import { DataTable } from "@/components/data-table";
import { Badge } from "@/components/ui";
import { formatMetric, formatNumber, formatPercent } from "@/lib/format";

type Row = Record<string, unknown>;
const num = (v: unknown) => (typeof v === "number" ? v : NaN);
const SERIES = ["var(--series-1)", "var(--series-2)"];

function metricInfo(d: Row) {
  const m = (d.metric as Row) ?? {};
  return { label: String(m.label ?? m.name), format: String(m.format ?? "number") };
}

/** Change in a rate, in percentage points; in metric units otherwise. */
function formatDelta(v: number, format: string) {
  if (format === "percent") return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)} pp`;
  return `${v >= 0 ? "+" : ""}${formatMetric(v, format)}`;
}

export function MetricView({ d }: { d: Row }) {
  const { label, format } = metricInfo(d);
  const rows = (d.rows as Row[]) ?? [];
  const groupBy = (d.group_by as string[]) ?? [];
  const total = (d.total as Row) ?? {};
  let chart: Series[] = [];
  if (d.time_grain && groupBy.length <= 1) {
    const groups = groupBy.length ? Array.from(new Set(rows.map((r) => String(r[groupBy[0]])))) : [label];
    if (groups.length <= 2) {
      chart = groups.map((g, i) => ({
        name: g,
        color: SERIES[i],
        points: rows.filter((r) => !groupBy.length || String(r[groupBy[0]]) === g).map((r) => ({ x: String(r.period), y: num(r.value) })),
      }));
    }
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="tabular text-2xl font-semibold">{formatMetric(num(total.value), format)}</span>
        <span className="text-sm text-ink-3">
          {label}
          {d.start || d.end ? ` · ${d.start ?? "…"} to ${d.end ?? "…"}` : " · all time"}
        </span>
      </div>
      {chart.length > 0 && <LineChart label={label} series={chart} format={(v) => formatMetric(v, format)} />}
      <DataTable
        columns={[...(d.time_grain ? ["period"] : []), ...groupBy, "value", ...(rows[0] && "numerator" in rows[0] ? ["numerator", "denominator"] : []), "rows"]}
        rows={rows.map((r) => ({ ...r, value: formatMetric(num(r.value), format) }))}
      />
    </div>
  );
}

export function AnomalyView({ d }: { d: Row }) {
  const { label, format } = metricInfo(d);
  const series = (d.series as Row[]) ?? [];
  const cp = (d.change_point as Row) ?? null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge tone={num(d.anomaly_count) > 0 ? "warn" : "neutral"}>{formatNumber(d.anomaly_count)} anomalous periods</Badge>
        {cp && (
          <Badge tone="accent">
            Level shift on {String(cp.period)}: {formatMetric(num(cp.before_mean), format)} → {formatMetric(num(cp.after_mean), format)} (
            {formatPercent(num(cp.relative_change))})
          </Badge>
        )}
      </div>
      <LineChart
        label={`${label} with anomalies`}
        marker={cp ? { x: String(cp.period), label: "level shift" } : undefined}
        flags={series.filter((p) => p.anomaly).map((p) => ({ x: String(p.period), y: num(p.value) }))}
        series={[
          { name: label, color: "var(--series-1)", points: series.map((p) => ({ x: String(p.period), y: num(p.value) })) },
          {
            name: `Trailing ${d.window}-${d.grain} median`,
            color: "var(--series-2)",
            dashed: true,
            points: series.filter((p) => p.expected != null).map((p) => ({ x: String(p.period), y: num(p.expected) })),
          },
        ]}
        format={(v) => formatMetric(v, format)}
      />
      <p className="text-xs text-ink-3">Red rings mark periods with a robust z-score of {String(d.threshold)} or more.</p>
    </div>
  );
}

export function DecompositionView({ d }: { d: Row }) {
  const { label, format } = metricInfo(d);
  const base = (d.baseline as Row) ?? {};
  const comp = (d.comparison as Row) ?? {};
  const dims = (d.dimensions as Row[]) ?? [];
  const ratio = String(d.method).startsWith("mix");
  return (
    <div className="space-y-5">
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div>
          <dt className="text-[11px] text-ink-3">
            Baseline {String(base.start)} – {String(base.end)}
          </dt>
          <dd className="tabular text-lg font-semibold">{formatMetric(num(base.value), format)}</dd>
        </div>
        <div>
          <dt className="text-[11px] text-ink-3">
            Comparison {String(comp.start)} – {String(comp.end)}
          </dt>
          <dd className="tabular text-lg font-semibold">{formatMetric(num(comp.value), format)}</dd>
        </div>
        <div>
          <dt className="text-[11px] text-ink-3">Change</dt>
          <dd className="tabular text-lg font-semibold">
            {formatDelta(num(d.change), format)} <span className="text-sm font-normal text-ink-3">({formatPercent(num(d.relative_change))})</span>
          </dd>
        </div>
      </dl>
      {dims.map((dim, i) => {
        const segs = (dim.segments as Row[]) ?? [];
        return (
          <div key={String(dim.dimension)} className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h4 className="text-sm font-semibold">{String(dim.dimension)}</h4>
              {i === 0 && <Badge tone="accent">Most disproportionate</Badge>}
              <span className="text-xs text-ink-3">
                {String(dim.top_segment)} carries {formatPercent(num(dim.excess_share))} more of the change than of the volume
                {ratio && ` · mix ${formatDelta(num(dim.mix_effect), format)}, rate ${formatDelta(num(dim.rate_effect), format)}`}
              </span>
            </div>
            <DivergingBars
              format={(v) => formatDelta(v, format)}
              rows={segs.map((s) => ({
                label: String(s.segment),
                value: num(s.effect),
                note: ratio
                  ? `${s.segment}: ${formatMetric(num(s.baseline_value), format)} → ${formatMetric(num(s.comparison_value), format)}, weight ${formatPercent(num(s.baseline_weight))} → ${formatPercent(num(s.comparison_weight))}`
                  : `${s.segment}: ${formatMetric(num(s.baseline_value), format)} → ${formatMetric(num(s.comparison_value), format)}`,
              }))}
            />
          </div>
        );
      })}
      <p className="text-xs text-ink-3">
        {label}: {ratio ? "mix effects come from segments' weights moving; rate effects from segments' own rates moving." : "segment deltas add up to the total change."}{" "}
        Attribution, not causation.
      </p>
    </div>
  );
}
