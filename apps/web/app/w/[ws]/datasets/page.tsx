"use client";

import { Database, Upload } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ConnectionsPanel } from "@/components/connections";
import { SampleDataButton } from "@/components/sample-data";
import { canEdit, useWorkspace } from "@/components/session";
import { Badge, Button, Card, cx, EmptyState, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { uploadDataset } from "@/lib/upload";
import { formatNumber, timeAgo } from "@/lib/format";
import type { Dataset } from "@/lib/types";

export default function DatasetsPage() {
  const { ws } = useParams<{ ws: string }>();
  const router = useRouter();
  const workspace = useWorkspace(ws);
  const editable = canEdit(workspace);
  const input = useRef<HTMLInputElement>(null);
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => api.get<Dataset[]>(`/workspaces/${ws}/datasets`).then(setDatasets, (e) => setError(e.message)), [ws]);
  useEffect(() => {
    void load();
  }, [load]);

  async function upload(file: File) {
    setUploading(true);
    setProgress(null);
    setError(null);
    try {
      await uploadDataset(ws, file, setProgress);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      setProgress(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Datasets</h1>
        {editable && (
          <Button variant="primary" loading={uploading} onClick={() => input.current?.click()}>
            <Upload className="size-4" aria-hidden /> Upload file
          </Button>
        )}
        <input
          ref={input}
          type="file"
          accept=".csv,.tsv,.txt,.parquet,.xlsx,.xls"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void upload(f);
            e.target.value = "";
          }}
        />
      </div>
      {error && <ErrorBanner message={error} />}
      {editable && datasets?.length === 0 && (
        <Card className="flex flex-col items-center gap-3 p-6 text-center">
          <p className="font-medium">No data yet? Start with the samples.</p>
          <p className="max-w-lg text-sm text-ink-2">
            Seven small datasets that match the example questions: e-commerce orders, a checkout A/B test, customer churn, a store loyalty program, a daily
            signup funnel with defined metrics, raw order lines, and daily sales.
          </p>
          <SampleDataButton ws={ws} variant="primary" onAdded={() => void load()} />
        </Card>
      )}
      {editable && (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const f = e.dataTransfer.files?.[0];
            if (f) void upload(f);
          }}
          className={cx(
            "rounded-xl border border-dashed px-6 py-8 text-center text-sm text-ink-2 transition-colors",
            dragging ? "border-accent bg-accent-soft" : "border-line",
          )}
        >
          {uploading ? (
            <Spinner label={progress != null && progress < 1 ? `Uploading… ${Math.round(progress * 100)}%` : "Converting to Parquet and profiling…"} />
          ) : (
            "Drop a CSV, TSV, Parquet, or Excel file here."
          )}
        </div>
      )}
      {editable && <ConnectionsPanel ws={ws} onImported={(d) => router.push(`/w/${ws}/datasets/${d.id}`)} />}
      {!datasets && !error && <Spinner />}
      {datasets?.length === 0 && !editable && <EmptyState icon={Database} title="No datasets in this workspace" />}
      {datasets && datasets.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {datasets.map((d) => (
            <Link key={d.id} href={`/w/${ws}/datasets/${d.id}`}>
              <Card className="h-full p-4 transition-colors hover:border-accent">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{d.display_name}</p>
                    <code className="font-mono text-xs text-ink-3">{d.table_name}</code>
                  </div>
                  <Badge tone={d.source_kind === "file" ? "neutral" : d.source_kind === "model" ? "good" : "accent"}>
                    {d.source_kind === "model" ? "data model" : d.source_kind}
                  </Badge>
                </div>
                {d.description && <p className="mt-2 line-clamp-2 text-sm text-ink-2">{d.description}</p>}
                <p className="tabular mt-3 text-xs text-ink-3">
                  {formatNumber(d.row_count)} rows · {d.columns.length} columns · added {timeAgo(d.created_at)}
                </p>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
