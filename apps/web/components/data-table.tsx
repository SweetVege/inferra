"use client";

import { formatNumber } from "@/lib/format";

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
