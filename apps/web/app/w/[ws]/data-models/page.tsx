"use client";

import { Network, Wand2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { canEdit, useWorkspace } from "@/components/session";
import { Badge, Button, Card, EmptyState, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { DataModelDetail, DataModelSummary, Dataset } from "@/lib/types";

function TestBadge({ m }: { m: DataModelSummary }) {
  if (!m.summary) return <Badge>Not built</Badge>;
  const { tests, tests_passed, tests_failed, nodes_failed } = m.summary;
  if (nodes_failed) return <Badge tone="bad">{nodes_failed} nodes failing</Badge>;
  return <Badge tone={tests_failed ? "bad" : "good"}>{`${tests_passed}/${tests} tests pass`}</Badge>;
}

export default function DataModelsPage() {
  const { ws } = useParams<{ ws: string }>();
  const router = useRouter();
  const editable = canEdit(useWorkspace(ws));
  const [models, setModels] = useState<DataModelSummary[] | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<DataModelSummary[]>(`/workspaces/${ws}/data-models`).then(setModels, (e) => setError(e.message));
    api.get<Dataset[]>(`/workspaces/${ws}/datasets`).then((d) => setDatasets(d.filter((x) => x.source_kind !== "model")), () => {});
  }, [ws]);

  async function draft() {
    setBusy(true);
    setError(null);
    try {
      const model = await api.post<DataModelDetail>(`/workspaces/${ws}/data-models/suggest`, { dataset_id: datasetId });
      router.push(`/w/${ws}/data-models/${model.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not draft a model.");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Data models</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-2">
          Star schemas built from your datasets: SQL transforms with tests and lineage. Review a draft, publish its tables as datasets, or export it as a dbt project.
        </p>
      </div>
      {editable && (
        <Card className="flex flex-wrap items-end gap-3 p-4">
          <label className="min-w-0 flex-1 space-y-1 text-sm">
            <span className="text-ink-2">Draft a star schema from a flat dataset</span>
            <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)} className="h-9 w-full rounded-lg border border-line bg-surface px-2">
              <option value="">Choose a dataset…</option>
              {datasets.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.display_name} ({d.table_name})
                </option>
              ))}
            </select>
          </label>
          <Button variant="primary" disabled={!datasetId} loading={busy} onClick={() => void draft()}>
            <Wand2 className="size-4" aria-hidden /> Draft model
          </Button>
          <p className="w-full text-xs text-ink-3">
            Or ask in plain language on the Ask page, for example &ldquo;Turn the order lines export into a star schema&rdquo;, and the Data Modeler agent will design, build, and test it.
          </p>
        </Card>
      )}
      {error && <ErrorBanner message={error} />}
      {!models && !error && <Spinner />}
      {models?.length === 0 && (
        <EmptyState icon={Network} title="No data models yet">
          Draft one from a dataset above, or ask Inferra to model your data.
        </EmptyState>
      )}
      {models && models.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {models.map((m) => (
            <Link key={m.id} href={`/w/${ws}/data-models/${m.id}`}>
              <Card className="h-full p-4 transition-colors hover:border-accent">
                <div className="flex items-start justify-between gap-2">
                  <p className="min-w-0 font-medium">{m.name}</p>
                  <TestBadge m={m} />
                </div>
                {m.description && <p className="mt-1 line-clamp-2 text-sm text-ink-2">{m.description}</p>}
                <p className="tabular mt-3 text-xs text-ink-3">
                  {m.node_count} nodes · {m.run_id ? "designed by agents" : "drafted manually"} · updated {timeAgo(m.updated_at)}
                  {m.stale && " · unbuilt changes"}
                </p>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
