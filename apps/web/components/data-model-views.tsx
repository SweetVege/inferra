"use client";

import { AlertTriangle, CheckCircle2, ChevronRight, CircleDashed, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { DataTable } from "@/components/data-table";
import { Badge, CodeBlock, cx } from "@/components/ui";
import { formatNumber, formatPercent } from "@/lib/format";
import type { BuildResult, BuildSummary, BuiltNode, Layer, TestResult } from "@/lib/types";

const LAYER_COLOR: Record<Layer | "source", string> = {
  source: "var(--text-3)",
  staging: "var(--text-3)",
  dimension: "var(--series-1)",
  fact: "var(--series-2)",
  mart: "var(--good)",
};
const LAYER_ORDER: Record<string, number> = {
  source: 0,
  staging: 1,
  dimension: 2,
  fact: 3,
  mart: 4,
};

interface GraphNode {
  name: string;
  layer: Layer | "source";
  status: string;
  rows: number | null | undefined;
  deps: string[];
  failing: number;
}

const W = 204,
  H = 46,
  GAP_X = 60,
  GAP_Y = 14,
  PAD = 8;

function shorten(name: string, max = 23): string {
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

/** A left-to-right lineage graph: sources, then each node placed one column after its deepest parent. */
export function LineageGraph({
  build,
  nodes,
  selected,
  onSelect,
}: {
  build: BuildResult | null;
  nodes: { name: string; layer: Layer; deps: string[] }[];
  selected?: string | null;
  onSelect?: (name: string) => void;
}) {
  const layout = useMemo(() => {
    const built = new Map((build?.nodes ?? []).map((n) => [n.name, n]));
    const failing = new Map<string, number>();
    for (const t of build?.tests ?? []) if (t.status === "fail" || t.status === "error") failing.set(t.node, (failing.get(t.node) ?? 0) + 1);
    const names = new Set(nodes.map((n) => n.name));
    const all: GraphNode[] = [];
    const sourceRows = new Map((build?.sources ?? []).map((s) => [s.name, s.row_count]));
    const sources = new Set(nodes.flatMap((n) => n.deps.filter((d) => !names.has(d))));
    for (const s of sources)
      all.push({
        name: s,
        layer: "source",
        status: "ok",
        rows: sourceRows.get(s),
        deps: [],
        failing: 0,
      });
    for (const n of nodes) {
      const b = built.get(n.name);
      all.push({
        name: n.name,
        layer: n.layer,
        status: b?.status ?? "pending",
        rows: b?.row_count,
        deps: n.deps,
        failing: failing.get(n.name) ?? 0,
      });
    }
    const byName = new Map(all.map((n) => [n.name, n]));
    const depth = new Map<string, number>();
    const visit = (name: string, seen: Set<string>): number => {
      if (depth.has(name)) return depth.get(name)!;
      const node = byName.get(name);
      if (!node || seen.has(name)) return 0;
      seen.add(name);
      const d = node.deps.length ? 1 + Math.max(...node.deps.map((p) => visit(p, seen))) : node.layer === "source" ? 0 : 1;
      depth.set(name, d);
      return d;
    };
    all.forEach((n) => visit(n.name, new Set()));
    const columns: GraphNode[][] = [];
    for (const n of all) (columns[depth.get(n.name) ?? 0] ??= []).push(n);
    const filled = columns.filter(Boolean);
    filled.forEach((col) => col.sort((a, b) => LAYER_ORDER[a.layer] - LAYER_ORDER[b.layer] || a.name.localeCompare(b.name)));
    const pos = new Map<string, { x: number; y: number }>();
    const tallest = Math.max(...filled.map((c) => c.length));
    const height = tallest * (H + GAP_Y) - GAP_Y + PAD * 2;
    filled.forEach((col, ci) => {
      const offset = (height - (col.length * (H + GAP_Y) - GAP_Y)) / 2;
      col.forEach((n, ri) =>
        pos.set(n.name, {
          x: PAD + ci * (W + GAP_X),
          y: offset + ri * (H + GAP_Y),
        }),
      );
    });
    const width = PAD * 2 + filled.length * W + (filled.length - 1) * GAP_X;
    return { all, pos, width, height };
  }, [build, nodes]);

  const linked = new Set<string>();
  if (selected) {
    linked.add(selected);
    for (const n of layout.all) if (n.deps.includes(selected)) linked.add(n.name);
    for (const d of layout.all.find((n) => n.name === selected)?.deps ?? []) linked.add(d);
  }

  return (
    <figure className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-line bg-bg">
        <svg width={layout.width} height={layout.height} role="img" aria-label="Lineage graph" className="block">
          {layout.all.flatMap((n) =>
            n.deps.map((d) => {
              const a = layout.pos.get(d);
              const b = layout.pos.get(n.name);
              if (!a || !b) return null;
              const x1 = a.x + W,
                y1 = a.y + H / 2,
                x2 = b.x,
                y2 = b.y + H / 2;
              const on = selected != null && (d === selected || n.name === selected);
              return (
                <path
                  key={`${d}->${n.name}`}
                  d={`M${x1},${y1} C${x1 + GAP_X / 2},${y1} ${x2 - GAP_X / 2},${y2} ${x2},${y2}`}
                  fill="none"
                  stroke={on ? "var(--accent)" : "var(--border)"}
                  strokeWidth={on ? 2 : 1.5}
                />
              );
            }),
          )}
          {layout.all.map((n) => {
            const p = layout.pos.get(n.name)!;
            const active = selected === n.name;
            const dim = selected != null && !linked.has(n.name);
            const clickable = n.layer !== "source" && onSelect;
            const subtitle =
              n.status === "error"
                ? "build failed"
                : n.status === "skipped"
                  ? "skipped"
                  : n.rows != null
                    ? `${n.layer} · ${formatNumber(n.rows)} rows`
                    : n.layer;
            return (
              <g
                key={n.name}
                transform={`translate(${p.x},${p.y})`}
                opacity={dim ? 0.45 : 1}
                className={clickable ? "cursor-pointer" : undefined}
                onClick={clickable ? () => onSelect(n.name) : undefined}
                onKeyDown={clickable ? (e) => (e.key === "Enter" || e.key === " ") && onSelect(n.name) : undefined}
                tabIndex={clickable ? 0 : undefined}
                role={clickable ? "button" : undefined}
                aria-label={clickable ? `${n.name}, ${subtitle}` : undefined}
              >
                <title>{n.name}</title>
                <rect
                  width={W}
                  height={H}
                  rx={8}
                  fill="var(--surface)"
                  stroke={n.status === "error" ? "var(--bad)" : active ? "var(--accent)" : "var(--border)"}
                  strokeWidth={active || n.status === "error" ? 2 : 1}
                  strokeDasharray={n.status === "skipped" || n.layer === "source" ? "4 3" : undefined}
                />
                <rect x={0} y={0} width={4} height={H} rx={2} fill={LAYER_COLOR[n.layer]} />
                <text x={14} y={19} fontSize="12" fontFamily="var(--font-mono, monospace)" fill="var(--text)">
                  {shorten(n.name, n.failing > 0 ? 21 : 24)}
                </text>
                <text x={14} y={35} fontSize="11" fill={n.status === "error" ? "var(--bad)" : "var(--text-3)"}>
                  {subtitle}
                </text>
                {n.failing > 0 && (
                  <g transform={`translate(${W - 14},${12})`}>
                    <circle r={9} fill="var(--bad)" />
                    <text textAnchor="middle" y={3.5} fontSize="10" fontWeight="600" fill="var(--surface)">
                      {n.failing}
                    </text>
                  </g>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <figcaption className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-3">
        {(["source", "staging", "dimension", "fact", "mart"] as const).map((l) => (
          <span key={l} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-1 rounded-full" style={{ background: LAYER_COLOR[l] }} />
            {l}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-flex size-3.5 items-center justify-center rounded-full bg-bad text-[9px] font-semibold text-surface">n</span>
          failing tests
        </span>
      </figcaption>
    </figure>
  );
}

export function graphNodes(build: BuildResult | null, specs?: { name: string; layer: Layer; sql: string }[]): { name: string; layer: Layer; deps: string[] }[] {
  if (build && (!specs || specs.every((s) => build.nodes.some((n) => n.name === s.name)))) {
    return build.nodes.map((n) => ({
      name: n.name,
      layer: n.layer,
      deps: n.depends_on,
    }));
  }
  // Unbuilt edits: read references straight from the SQL.
  return (specs ?? []).map((s) => ({
    name: s.name,
    layer: s.layer,
    deps: [...s.sql.matchAll(/\{\{\s*(?:ref|source)\(\s*(?:['"][^'"]+['"]\s*,\s*)?['"]([^'"]+)['"]\s*\)\s*\}\}/g)].map((m) => m[1]),
  }));
}

export function BuildStats({ summary }: { summary: BuildSummary }) {
  const items = [
    {
      label: "Nodes built",
      value: `${summary.nodes_built} / ${summary.nodes}`,
      tone: summary.nodes_failed ? "bad" : undefined,
    },
    {
      label: "Tests passing",
      value: `${summary.tests_passed} / ${summary.tests}`,
      tone: summary.tests_failed ? "bad" : "good",
    },
    {
      label: "Warnings",
      value: String(summary.tests_warned),
      tone: summary.tests_warned ? "warn" : undefined,
    },
    {
      label: "Build time",
      value: `${formatNumber(summary.duration_ms / 1000, 2)} s`,
    },
  ];
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {items.map((i) => (
        <div key={i.label} className="rounded-lg border border-line bg-surface px-3 py-2">
          <dt className="text-xs text-ink-3">{i.label}</dt>
          <dd
            className={cx("tabular text-lg font-semibold", i.tone === "bad" && "text-bad", i.tone === "good" && "text-good", i.tone === "warn" && "text-warn")}
          >
            {i.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

const TEST_ICON = {
  pass: { icon: CheckCircle2, className: "text-good", label: "Pass" },
  fail: { icon: XCircle, className: "text-bad", label: "Fail" },
  error: { icon: XCircle, className: "text-bad", label: "Error" },
  warn: { icon: AlertTriangle, className: "text-warn", label: "Warning" },
  skipped: { icon: CircleDashed, className: "text-ink-3", label: "Skipped" },
} as const;
const TEST_ORDER = { fail: 0, error: 1, warn: 2, skipped: 3, pass: 4 } as const;

export function TestResults({ tests, node }: { tests: TestResult[]; node?: string | null }) {
  const [open, setOpen] = useState<string | null>(null);
  const [showPassing, setShowPassing] = useState(false);
  const all = [...tests.filter((t) => !node || t.node === node)].sort((a, b) => TEST_ORDER[a.status] - TEST_ORDER[b.status] || a.id.localeCompare(b.id));
  if (!all.length) return <p className="text-sm text-ink-3">No tests{node ? " on this node" : ""}.</p>;
  const passing = all.filter((t) => t.status === "pass").length;
  const collapse = passing > 0 && all.length > 6 && !showPassing;
  const shown = collapse ? all.filter((t) => t.status !== "pass") : all;
  return (
    <div className="space-y-2">
      {shown.length > 0 && (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {shown.map((t) => {
            const s = TEST_ICON[t.status];
            const Icon = s.icon;
            const expandable = t.status !== "pass" && t.status !== "skipped";
            return (
              <li key={t.id} className="text-sm">
                <button
                  type="button"
                  className={cx("flex w-full items-center gap-2 px-3 py-2 text-left", expandable && "hover:bg-surface-2")}
                  onClick={() => expandable && setOpen(open === t.id ? null : t.id)}
                  aria-expanded={expandable ? open === t.id : undefined}
                >
                  <Icon className={cx("size-4 shrink-0", s.className)} aria-label={s.label} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{t.description}</span>
                    {!node && <span className="block truncate font-mono text-xs text-ink-3">{t.node}</span>}
                  </span>
                  {t.severity === "warn" && <Badge tone="warn">warn</Badge>}
                  {t.failures != null && t.failures > 0 && (
                    <span className="tabular shrink-0 text-xs font-medium text-bad">{formatNumber(t.failures)} failing</span>
                  )}
                  {expandable && <ChevronRight className={cx("size-4 shrink-0 text-ink-3 transition-transform", open === t.id && "rotate-90")} aria-hidden />}
                </button>
                {open === t.id && (
                  <div className="space-y-2 px-3 pb-3">
                    {t.error && <p className="text-xs text-bad">{t.error}</p>}
                    {t.sample && t.sample.length > 0 && (
                      <>
                        <p className="text-xs text-ink-3">Sample of failing rows</p>
                        <DataTable columns={Object.keys(t.sample[0])} rows={t.sample} />
                      </>
                    )}
                    {t.sql && <CodeBlock>{t.sql}</CodeBlock>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {all.length > 6 && passing > 0 && (
        <button type="button" onClick={() => setShowPassing(!showPassing)} className="inline-flex items-center gap-1.5 text-sm text-ink-2 hover:text-ink">
          <CheckCircle2 className="size-4 text-good" aria-hidden />
          {showPassing ? "Hide passing tests" : `${passing} passing tests`}
        </button>
      )}
    </div>
  );
}

export function NodeOutput({ node }: { node: BuiltNode }) {
  if (node.status !== "ok") {
    return <p className={cx("text-sm", node.status === "error" ? "text-bad" : "text-ink-3")}>{node.error}</p>;
  }
  return (
    <div className="space-y-3">
      <DataTable
        columns={["column", "type", "description"]}
        rows={(node.columns ?? []).map((c) => ({
          column: c.name,
          type: c.type,
          description: c.description,
        }))}
      />
      {node.preview && node.preview.length > 0 && (
        <>
          <p className="text-xs text-ink-3">First {node.preview.length} rows</p>
          <DataTable columns={Object.keys(node.preview[0])} rows={node.preview} />
        </>
      )}
    </div>
  );
}

/** The data_model artifact in a run: stats, lineage, and failing tests, linking to the editable model. */
export function DataModelArtifact({ d }: { d: BuildResult }) {
  const { ws } = useParams<{ ws: string }>();
  const [selected, setSelected] = useState<string | null>(null);
  const node = d.nodes.find((n) => n.name === selected);
  return (
    <div className="space-y-4">
      <BuildStats summary={d.summary} />
      <LineageGraph build={d} nodes={graphNodes(d)} selected={selected} onSelect={(n) => setSelected(selected === n ? null : n)} />
      {node && (
        <div className="space-y-2">
          <p className="font-mono text-sm">{node.name}</p>
          <NodeOutput node={node} />
          <CodeBlock>{node.sql}</CodeBlock>
        </div>
      )}
      <TestResults tests={d.tests} node={selected} />
      {ws && d.data_model_id && (
        <Link href={`/w/${ws}/data-models/${d.data_model_id}`} className="inline-flex items-center gap-1 text-sm font-medium text-accent hover:underline">
          Review and edit this data model <ChevronRight className="size-4" aria-hidden />
        </Link>
      )}
    </div>
  );
}

interface KeysData {
  table: string;
  row_count: number;
  duplicate_rows: number;
  grain: string[];
  candidate_keys: string[][];
  entities: {
    column: string;
    name: string;
    distinct: number;
    role: string;
    attributes: string[];
    references: string[];
  }[];
  unstable_attributes: {
    entity: string;
    column: string;
    violations: number;
    groups: number;
    examples: string[];
  }[];
  measures: string[];
}

export function KeysView({ d }: { d: KeysData }) {
  return (
    <div className="space-y-3 text-sm">
      <p className="text-ink-2">
        One row per <strong className="font-mono text-[13px] text-ink">{d.grain.join(" + ") || "(no unique key found)"}</strong> across{" "}
        {formatNumber(d.row_count)} rows
        {d.duplicate_rows > 0 && <>, with {formatNumber(d.duplicate_rows)} exact duplicate rows</>}.
      </p>
      {d.entities.length > 0 && (
        <DataTable
          columns={["entity", "distinct", "role", "attributes"]}
          rows={d.entities.map((e) => ({
            entity: e.column,
            distinct: e.distinct,
            role: e.role,
            attributes: e.role === "transaction" ? [...e.attributes, ...e.references.map((r) => `→ ${r}`)].join(", ") : e.attributes.join(", ") || "—",
          }))}
        />
      )}
      {d.unstable_attributes.length > 0 && (
        <ul className="space-y-1">
          {d.unstable_attributes.map((u) => (
            <li key={`${u.entity}.${u.column}`} className="flex items-start gap-2 text-ink-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
              <span>
                <code className="font-mono text-[13px]">{u.column}</code> takes more than one value for {formatNumber(u.violations)} of {formatNumber(u.groups)}{" "}
                <code className="font-mono text-[13px]">{u.entity}</code> values (e.g. {u.examples.join(", ")}).
              </span>
            </li>
          ))}
        </ul>
      )}
      {d.measures.length > 0 && (
        <p className="text-ink-3">
          Measures: <span className="font-mono text-[13px]">{d.measures.join(", ")}</span>
        </p>
      )}
    </div>
  );
}

interface Relationship {
  left: string;
  right: string;
  cardinality: string;
  left_coverage: number;
  right_coverage: number;
  orphan_left_values: number;
}

export function RelationshipsView({ d }: { d: { relationships: Relationship[] } }) {
  if (!d.relationships.length) return <p className="text-sm text-ink-3">No shared keys found between these tables.</p>;
  return (
    <DataTable
      columns={["left", "right", "cardinality", "left values matched", "right values matched", "orphan left values"]}
      rows={d.relationships.map((r) => ({
        left: r.left,
        right: r.right,
        cardinality: r.cardinality,
        "left values matched": formatPercent(r.left_coverage),
        "right values matched": formatPercent(r.right_coverage),
        "orphan left values": r.orphan_left_values,
      }))}
    />
  );
}
