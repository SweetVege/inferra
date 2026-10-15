"use client";

import { Findings } from "@/components/profile-view";
import { CodeBlock } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import type { Artifact, Finding } from "@/lib/types";

export function DataTable({ columns, rows, max = 50 }: { columns: string[]; rows: Record<string, unknown>[]; max?: number }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-[13px]">
        <thead className="bg-surface-2 text-left text-xs text-ink-3">
          <tr>
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.slice(0, max).map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={`whitespace-nowrap px-3 py-1.5 ${typeof row[c] === "number" ? "tabular text-right" : ""}`}>
                  {formatNumber(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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
  if (artifact.kind === "correlations") {
    return <DataTable columns={["a", "b", "r", "n"]} rows={d.pairs as Record<string, unknown>[]} />;
  }
  return <CodeBlock>{JSON.stringify(d, null, 2).slice(0, 4000)}</CodeBlock>;
}
