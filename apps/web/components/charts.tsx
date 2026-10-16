"use client";

import { useState } from "react";

import { formatNumber } from "@/lib/format";

export interface Series {
  name: string;
  color: string; // a CSS color token, e.g. "var(--series-1)"
  points: { x: string; y: number | null }[];
  dashed?: boolean;
  band?: { x: string; low: number; high: number }[];
}

/** Multi-series line chart over a shared categorical x axis (dates), with an optional interval band,
 * a vertical marker, a crosshair tooltip, and a legend. */
export function LineChart({
  series,
  marker,
  flags = [],
  height = 200,
  label,
  format = (v: number) => formatNumber(v),
}: {
  series: Series[];
  marker?: { x: string; label: string };
  flags?: { x: string; y: number }[];
  height?: number;
  label: string;
  format?: (v: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const xs = Array.from(new Set(series.flatMap((s) => [...s.points.map((p) => p.x), ...(s.band ?? []).map((b) => b.x)]))).sort();
  const values = series.flatMap((s) => [
    ...s.points.map((p) => p.y).filter((v): v is number => v != null && Number.isFinite(v)),
    ...(s.band ?? []).flatMap((b) => [b.low, b.high]),
  ]);
  if (xs.length < 2 || values.length === 0) return null;
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
  lo -= pad;
  hi += pad;
  const w = 640,
    m = { l: 52, r: 12, t: 10, b: 22 };
  const px = (i: number) => m.l + (i / (xs.length - 1)) * (w - m.l - m.r);
  const py = (v: number) => m.t + (1 - (v - lo) / (hi - lo)) * (height - m.t - m.b);
  const index = new Map(xs.map((x, i) => [x, i]));
  const ticks = [lo + pad, (lo + hi) / 2, hi - pad];
  const path = (pts: { x: string; y: number | null }[]) =>
    pts
      .filter((p) => p.y != null && Number.isFinite(p.y))
      .map((p, i) => `${i ? "L" : "M"}${px(index.get(p.x)!).toFixed(1)},${py(p.y as number).toFixed(1)}`)
      .join(" ");
  const hoverX = hover != null ? xs[hover] : null;

  return (
    <figure className="space-y-2">
      {/* A single series is named by its title; a legend only helps when there are several. */}
      <div className={series.length > 1 ? "flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2" : "hidden"}>
        {series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1.5">
            <svg width="16" height="8" aria-hidden>
              <line x1="0" x2="16" y1="4" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={s.dashed ? "4 3" : undefined} />
            </svg>
            {s.name}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${w} ${height}`}
        className="w-full"
        role="img"
        aria-label={label}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const rel = ((e.clientX - rect.left) / rect.width) * w;
          setHover(Math.max(0, Math.min(xs.length - 1, Math.round(((rel - m.l) / (w - m.l - m.r)) * (xs.length - 1)))));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={m.l} x2={w - m.r} y1={py(t)} y2={py(t)} stroke="var(--border)" />
            <text x={m.l - 6} y={py(t) + 3} textAnchor="end" fontSize="10" fill="var(--text-3)">
              {format(t)}
            </text>
          </g>
        ))}
        {series.map(
          (s) =>
            s.band && (
              <path
                key={`${s.name}-band`}
                d={
                  s.band.map((b, i) => `${i ? "L" : "M"}${px(index.get(b.x)!).toFixed(1)},${py(b.high).toFixed(1)}`).join(" ") +
                  " " +
                  [...s.band].reverse().map((b) => `L${px(index.get(b.x)!).toFixed(1)},${py(b.low).toFixed(1)}`).join(" ") +
                  " Z"
                }
                fill={s.color}
                fillOpacity="0.14"
              />
            ),
        )}
        {marker && index.has(marker.x) && (
          <g>
            <line x1={px(index.get(marker.x)!)} x2={px(index.get(marker.x)!)} y1={m.t} y2={height - m.b} stroke="var(--text-3)" strokeDasharray="4 3" />
            {/* Near the right edge the label goes on the left of the line so it is not clipped. */}
            <text
              x={px(index.get(marker.x)!) + (index.get(marker.x)! > (xs.length - 1) * 0.8 ? -4 : 4)}
              y={m.t + 10}
              textAnchor={index.get(marker.x)! > (xs.length - 1) * 0.8 ? "end" : "start"}
              fontSize="10"
              fill="var(--text-3)"
            >
              {marker.label}
            </text>
          </g>
        )}
        {series.map((s) => (
          <path key={s.name} d={path(s.points)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeDasharray={s.dashed ? "5 4" : undefined} />
        ))}
        {flags.map((f) =>
          index.has(f.x) ? (
            <circle key={`flag-${f.x}`} cx={px(index.get(f.x)!)} cy={py(f.y)} r="4.5" fill="var(--surface)" stroke="var(--bad)" strokeWidth="2">
              <title>{`${f.x}: anomaly`}</title>
            </circle>
          ) : null,
        )}
        {hoverX && (
          <g>
            <line x1={px(hover!)} x2={px(hover!)} y1={m.t} y2={height - m.b} stroke="var(--text-3)" strokeOpacity="0.5" />
            {series.map((s) => {
              const p = s.points.find((pt) => pt.x === hoverX);
              return p && p.y != null ? <circle key={s.name} cx={px(hover!)} cy={py(p.y)} r="4" fill={s.color} stroke="var(--surface)" strokeWidth="2" /> : null;
            })}
          </g>
        )}
        <text x={m.l} y={height - 6} fontSize="10" fill="var(--text-3)">
          {xs[0]}
        </text>
        <text x={w - m.r} y={height - 6} textAnchor="end" fontSize="10" fill="var(--text-3)">
          {xs[xs.length - 1]}
        </text>
      </svg>
      <figcaption className="tabular min-h-4 text-xs text-ink-2">
        {hoverX &&
          [
            hoverX,
            ...series.flatMap((s) => {
              const p = s.points.find((pt) => pt.x === hoverX);
              const b = s.band?.find((bb) => bb.x === hoverX);
              if (!p || p.y == null) return [];
              return [`${s.name}: ${format(p.y)}${b ? ` (${format(b.low)} – ${format(b.high)})` : ""}`];
            }),
          ].join(" · ")}
      </figcaption>
    </figure>
  );
}

/** Horizontal bars for a share or magnitude per category, labeled with values. */
export function BarList({ rows, format }: { rows: { label: string; value: number; note?: string }[]; format: (v: number) => string }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-12);
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-2 text-xs" title={r.note}>
          <span className="truncate text-ink-2">{r.label}</span>
          <span className="h-2.5 rounded-r-[4px] bg-series-1" style={{ width: `${Math.max(2, (Math.abs(r.value) / max) * 100)}%` }} />
          <span className="tabular text-ink-2">{format(r.value)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Point estimates with intervals on a shared scale, with a reference line (usually zero). */
export function IntervalPlot({
  rows,
  reference = 0,
  format,
  label,
}: {
  rows: { label: string; value: number; low: number; high: number }[];
  reference?: number;
  format: (v: number) => string;
  label: string;
}) {
  const valid = rows.filter((r) => [r.value, r.low, r.high].every(Number.isFinite));
  if (!valid.length) return null;
  const lo = Math.min(reference, ...valid.map((r) => r.low));
  const hi = Math.max(reference, ...valid.map((r) => r.high));
  const span = hi - lo || 1;
  const x = (v: number) => ((v - lo) / span) * 100;
  return (
    <figure aria-label={label} className="space-y-0.5">
      {valid.map((r) => (
        <div key={r.label} className="grid h-7 grid-cols-[minmax(0,10rem)_1fr_9.5rem] items-center gap-2 text-xs" title={`${r.label}: ${format(r.value)} [${format(r.low)}, ${format(r.high)}]`}>
          <span className="truncate text-ink-2">{r.label}</span>
          <div className="relative h-full">
            <div className="absolute inset-y-1 border-l border-dashed border-ink-3" style={{ left: `${x(reference)}%` }} />
            <div className="absolute top-1/2 h-[2px] -translate-y-1/2 bg-series-1" style={{ left: `${x(r.low)}%`, width: `${Math.max(0.5, x(r.high) - x(r.low))}%` }} />
            <div className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-series-1" style={{ left: `${x(r.value)}%` }} />
          </div>
          <span className="tabular text-right text-ink-2">
            {format(r.value)} <span className="text-ink-3">[{format(r.low)}, {format(r.high)}]</span>
          </span>
        </div>
      ))}
    </figure>
  );
}

/** Signed bars around a zero line: positive values in one hue, negative in another. */
export function DivergingBars({ rows, format }: { rows: { label: string; value: number; note?: string }[]; format: (v: number) => string }) {
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-12);
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => {
        const width = `${Math.max(1, (Math.abs(r.value) / max) * 50)}%`;
        return (
          <li key={r.label} className="grid grid-cols-[minmax(0,8rem)_1fr_auto] items-center gap-2 text-xs" title={r.note}>
            <span className="truncate text-ink-2">{r.label}</span>
            <span className="relative h-3">
              <span className="absolute inset-y-0 left-1/2 border-l border-ink-3" />
              <span
                className={r.value < 0 ? "absolute inset-y-0 rounded-l-[4px] bg-series-2" : "absolute inset-y-0 rounded-r-[4px] bg-series-1"}
                style={r.value < 0 ? { right: "50%", width } : { left: "50%", width }}
              />
            </span>
            <span className="tabular w-20 text-right text-ink-2">{format(r.value)}</span>
          </li>
        );
      })}
    </ul>
  );
}
