"use client";

import { DataModelArtifact, KeysView, RelationshipsView } from "@/components/data-model-views";
import { DataTable } from "@/components/data-table";
import { AbTestView, BaselineView, BayesianView, PowerView, SegmentView, SequentialView, SrmView } from "@/components/experiment-views";
import { AnomalyView, DecompositionView, MetricView } from "@/components/metric-views";
import { DidView, ForecastView, ModelView, PsmView, RegressionView } from "@/components/modeling-views";
import { Findings } from "@/components/profile-view";
import { CodeBlock } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import type { Artifact, BuildResult, Finding } from "@/lib/types";

export function ArtifactView({ artifact }: { artifact: Artifact }) {
  const d = artifact.data as Record<string, unknown>;
  if (artifact.kind === "table") {
    return (
      <div className="space-y-2">
        <DataTable columns={d.columns as string[]} rows={d.rows as Record<string, unknown>[]} />
        {Boolean(d.truncated) && <p className="text-xs text-ink-3">Showing the first {formatNumber(d.row_count)} rows.</p>}
        {typeof d.sql === "string" && <CodeBlock>{d.sql}</CodeBlock>}
      </div>
    );
  }
  if (artifact.kind === "profile") {
    const columns = d.columns as Record<string, { kind: string; null_rate: number; distinct_count: number }>;
    return (
      <div className="space-y-3">
        <p className="text-sm text-ink-2">
          {formatNumber(d.row_count)} rows · {formatNumber(d.column_count)} columns · {formatNumber(d.duplicate_rows)} duplicate rows
        </p>
        <Findings findings={d.findings as Finding[]} />
        <DataTable
          columns={["column", "kind", "missing", "distinct"]}
          rows={Object.entries(columns).map(([name, c]) => ({
            column: name,
            kind: c.kind,
            missing: `${(c.null_rate * 100).toFixed(1)}%`,
            distinct: c.distinct_count,
          }))}
        />
      </div>
    );
  }
  if (artifact.kind === "ab_test") return <AbTestView d={d} />;
  if (artifact.kind === "cuped") return <AbTestView d={d} cuped />;
  if (artifact.kind === "segment_effects") return <SegmentView d={d} />;
  if (artifact.kind === "srm") return <SrmView d={d} />;
  if (artifact.kind === "power") return <PowerView d={d} />;
  if (artifact.kind === "bayesian") return <BayesianView d={d} />;
  if (artifact.kind === "sequential") return <SequentialView d={d} />;
  if (artifact.kind === "baseline") return <BaselineView d={d} />;
  if (artifact.kind === "metric") return <MetricView d={d} />;
  if (artifact.kind === "anomalies") return <AnomalyView d={d} />;
  if (artifact.kind === "decomposition") return <DecompositionView d={d} />;
  if (artifact.kind === "regression") return <RegressionView d={d} />;
  if (artifact.kind === "did") return <DidView d={d} />;
  if (artifact.kind === "psm") return <PsmView d={d} />;
  if (artifact.kind === "forecast") return <ForecastView d={d} />;
  if (artifact.kind === "model") return <ModelView d={d} />;
  if (artifact.kind === "data_model") return <DataModelArtifact d={artifact.data as unknown as BuildResult} />;
  if (artifact.kind === "keys") return <KeysView d={artifact.data as unknown as Parameters<typeof KeysView>[0]["d"]} />;
  if (artifact.kind === "relationships") return <RelationshipsView d={artifact.data as unknown as Parameters<typeof RelationshipsView>[0]["d"]} />;
  if (artifact.kind === "correlations") {
    return <DataTable columns={["a", "b", "r", "n"]} rows={d.pairs as Record<string, unknown>[]} />;
  }
  return <CodeBlock>{JSON.stringify(d, null, 2).slice(0, 4000)}</CodeBlock>;
}

export { DataTable };
