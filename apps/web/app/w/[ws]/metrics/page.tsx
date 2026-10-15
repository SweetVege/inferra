"use client";

import { Gauge, Plus, Trash2 } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { LineChart } from "@/components/charts";
import { canEdit, useWorkspace } from "@/components/session";
import { Badge, Button, Card, EmptyState, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatMetric } from "@/lib/format";
import type { Dataset, MetricDefinition, MetricPart, MetricValues } from "@/lib/types";

const KINDS = ["ratio", "sum", "count", "count_distinct", "average"] as const;
const PART_KINDS = ["sum", "count", "count_distinct"] as const;

function describe(m: MetricDefinition): string {
  const part = (p: MetricPart | null) => (p ? (p.kind === "count" && !p.column ? "count(*)" : `${p.kind}(${p.column})`) : "?");
  const body = m.kind === "ratio" ? `${part(m.numerator)} / ${part(m.denominator)}` : m.kind === "count" ? "count(*)" : `${m.kind}(${m.column})`;
  return body + (m.filter ? ` where ${m.filter}` : "");
}

function MetricCard({ ws, metric, editable, onDelete }: { ws: string; metric: MetricDefinition; editable: boolean; onDelete: () => void }) {
  const [values, setValues] = useState<MetricValues | null>(null);
  useEffect(() => {
    api.get<MetricValues>(`/workspaces/${ws}/metrics/${metric.id}/values?grain=week`).then(setValues, () => {});
  }, [ws, metric.id]);
  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">{metric.label}</p>
          <code className="font-mono text-xs text-ink-3">{metric.name}</code>
        </div>
        <div className="flex items-center gap-2">
          <span className="tabular text-lg font-semibold">{formatMetric(values?.total.value ?? metric.current_value, metric.format)}</span>
          {editable && (
            <button onClick={onDelete} className="rounded-lg p-1.5 text-ink-3 hover:bg-surface-2 hover:text-bad" aria-label={`Delete ${metric.label}`}>
              <Trash2 className="size-4" />
            </button>
          )}
        </div>
      </div>
      <p className="font-mono text-xs text-ink-2">{describe(metric)}</p>
      {values && values.series.length > 1 && (
        <LineChart
          height={120}
          label={`${metric.label} by week`}
          series={[{ name: "Weekly", color: "var(--series-1)", points: values.series.map((p) => ({ x: p.period, y: p.value })) }]}
          format={(v) => formatMetric(v, metric.format)}
        />
      )}
      <div className="flex flex-wrap gap-1.5">
        <Badge>{metric.dataset_name ?? "dataset"}</Badge>
        {metric.time_column && <Badge>time: {metric.time_column}</Badge>}
        {metric.dimensions.map((d) => (
          <Badge key={d} tone="accent">
            {d}
          </Badge>
        ))}
      </div>
    </Card>
  );
}

function ColumnSelect({ value, onChange, columns, label, optional }: { value: string; onChange: (v: string) => void; columns: string[]; label: string; optional?: boolean }) {
  return (
    <label className="space-y-1 text-sm">
      <span className="text-ink-2">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className="h-9 w-full rounded-lg border border-line bg-surface px-2">
        {optional && <option value="">—</option>}
        {columns.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </label>
  );
}

function MetricForm({ ws, datasets, onCreated }: { ws: string; datasets: Dataset[]; onCreated: () => void }) {
  const [datasetId, setDatasetId] = useState(datasets[0]?.id ?? "");
  const [label, setLabel] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<(typeof KINDS)[number]>("ratio");
  const [column, setColumn] = useState("");
  const [num, setNum] = useState<MetricPart>({ kind: "sum", column: "" });
  const [den, setDen] = useState<MetricPart>({ kind: "sum", column: "" });
  const [timeColumn, setTimeColumn] = useState("");
  const [dimensions, setDimensions] = useState<string[]>([]);
  const [format, setFormat] = useState<"number" | "percent" | "currency">("number");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dataset = datasets.find((d) => d.id === datasetId);
  const columns = dataset?.columns.map((c) => c.name) ?? [];

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`/workspaces/${ws}/metrics`, {
        dataset_id: datasetId,
        name: name || label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
        label,
        kind,
        column: kind === "ratio" || kind === "count" ? null : column || null,
        numerator: kind === "ratio" ? { kind: num.kind, column: num.column || null } : null,
        denominator: kind === "ratio" ? { kind: den.kind, column: den.column || null } : null,
        time_column: timeColumn || null,
        dimensions,
        format,
        filter: filter || null,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the metric.");
    } finally {
      setBusy(false);
    }
  }

  const part = (p: MetricPart, set: (p: MetricPart) => void, title: string) => (
    <div className="grid grid-cols-2 gap-2">
      <label className="space-y-1 text-sm">
        <span className="text-ink-2">{title}</span>
        <select value={p.kind} onChange={(e) => set({ ...p, kind: e.target.value as MetricPart["kind"] })} className="h-9 w-full rounded-lg border border-line bg-surface px-2">
          {PART_KINDS.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
      </label>
      <ColumnSelect label="of column" value={p.column ?? ""} onChange={(c) => set({ ...p, column: c })} columns={columns} optional={p.kind === "count"} />
    </div>
  );

  return (
    <Card className="p-5">
      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span className="text-ink-2">Dataset</span>
            <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)} className="h-9 w-full rounded-lg border border-line bg-surface px-2">
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.display_name}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-2">Label</span>
            <input value={label} onChange={(e) => setLabel(e.target.value)} required placeholder="Signup conversion rate" className="h-9 w-full rounded-lg border border-line bg-surface px-3" />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-2">Identifier</span>
            <input value={name} onChange={(e) => setName(e.target.value)} pattern="[a-z][a-z0-9_]{1,60}" placeholder="conversion_rate (from the label if empty)" className="h-9 w-full rounded-lg border border-line bg-surface px-3 font-mono text-[13px]" />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-2">Kind</span>
            <select value={kind} onChange={(e) => setKind(e.target.value as (typeof KINDS)[number])} className="h-9 w-full rounded-lg border border-line bg-surface px-2">
              {KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
        </div>
        {kind === "ratio" ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {part(num, setNum, "Numerator")}
            {part(den, setDen, "Denominator")}
          </div>
        ) : kind !== "count" ? (
          <ColumnSelect label="Column" value={column} onChange={setColumn} columns={columns} optional />
        ) : null}
        <div className="grid gap-3 sm:grid-cols-3">
          <ColumnSelect label="Time column" value={timeColumn} onChange={setTimeColumn} columns={columns} optional />
          <label className="space-y-1 text-sm">
            <span className="text-ink-2">Format</span>
            <select value={format} onChange={(e) => setFormat(e.target.value as typeof format)} className="h-9 w-full rounded-lg border border-line bg-surface px-2">
              <option value="number">number</option>
              <option value="percent">percent</option>
              <option value="currency">currency</option>
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-ink-2">Filter (optional SQL)</span>
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="country = 'US'" className="h-9 w-full rounded-lg border border-line bg-surface px-3 font-mono text-[13px]" />
          </label>
        </div>
        <fieldset className="space-y-1 text-sm">
          <legend className="text-ink-2">Dimensions to slice by</legend>
          <div className="flex flex-wrap gap-1.5">
            {columns.map((c) => (
              <label key={c} className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs">
                <input type="checkbox" checked={dimensions.includes(c)} onChange={(e) => setDimensions((d) => (e.target.checked ? [...d, c] : d.filter((x) => x !== c)))} />
                {c}
              </label>
            ))}
          </div>
        </fieldset>
        {error && <ErrorBanner message={error} />}
        <Button variant="primary" type="submit" loading={busy}>
          Save metric
        </Button>
      </form>
    </Card>
  );
}

export default function MetricsPage() {
  const { ws } = useParams<{ ws: string }>();
  const editable = canEdit(useWorkspace(ws));
  const [metrics, setMetrics] = useState<MetricDefinition[] | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => api.get<MetricDefinition[]>(`/workspaces/${ws}/metrics`).then(setMetrics, (e) => setError(e.message)), [ws]);
  useEffect(() => {
    void load();
    api.get<Dataset[]>(`/workspaces/${ws}/datasets`).then(setDatasets, () => {});
  }, [ws, load]);

  async function remove(id: string) {
    if (!confirm("Delete this metric definition?")) return;
    await api.delete(`/workspaces/${ws}/metrics/${id}`);
    void load();
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Metrics</h1>
          <p className="text-sm text-ink-2">Governed definitions the agents reuse, so every run computes a metric the same way.</p>
        </div>
        {editable && datasets.length > 0 && (
          <Button variant="primary" onClick={() => setCreating(!creating)}>
            <Plus className="size-4" aria-hidden /> New metric
          </Button>
        )}
      </div>
      {error && <ErrorBanner message={error} />}
      {creating && (
        <MetricForm
          ws={ws}
          datasets={datasets}
          onCreated={() => {
            setCreating(false);
            void load();
          }}
        />
      )}
      {!metrics && !error && <Spinner />}
      {metrics?.length === 0 && !creating && (
        <EmptyState icon={Gauge} title="No metrics defined">
          Define a metric such as a conversion rate or revenue once, and Inferra uses the same definition in every analysis.
        </EmptyState>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {metrics?.map((m) => (
          <MetricCard key={m.id} ws={ws} metric={m} editable={editable} onDelete={() => void remove(m.id)} />
        ))}
      </div>
    </div>
  );
}
