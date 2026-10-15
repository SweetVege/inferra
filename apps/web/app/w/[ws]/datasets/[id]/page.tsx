"use client";

import { ArrowLeft, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { DataTable } from "@/components/artifact-view";
import { ProfileView } from "@/components/profile-view";
import { canEdit, useWorkspace } from "@/components/session";
import { Button, Card, ErrorBanner, SectionTitle, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber, timeAgo } from "@/lib/format";
import type { DatasetDetail } from "@/lib/types";

export default function DatasetPage() {
  const { ws, id } = useParams<{ ws: string; id: string }>();
  const router = useRouter();
  const editable = canEdit(useWorkspace(ws));
  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  const [profiling, setProfiling] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<DatasetDetail>(`/workspaces/${ws}/datasets/${id}`).then(setDataset, (e) => setError(e.message));
  }, [ws, id]);

  async function profile() {
    setProfiling(true);
    try {
      setDataset(await api.post<DatasetDetail>(`/workspaces/${ws}/datasets/${id}/profile`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Profiling failed.");
    } finally {
      setProfiling(false);
    }
  }

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      await api.post(`/workspaces/${ws}/datasets/${id}/refresh`);
      setDataset(await api.get<DatasetDetail>(`/workspaces/${ws}/datasets/${id}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed.");
    } finally {
      setRefreshing(false);
    }
  }

  async function remove() {
    if (!confirm("Delete this dataset? Runs that used it keep their results.")) return;
    await api.delete(`/workspaces/${ws}/datasets/${id}`);
    router.push(`/w/${ws}/datasets`);
  }

  if (!dataset) return error ? <ErrorBanner message={error} /> : <Spinner />;
  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="space-y-2">
        <Link href={`/w/${ws}/datasets`} className="inline-flex items-center gap-1 text-sm text-ink-3 hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Datasets
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{dataset.display_name}</h1>
            <p className="tabular text-sm text-ink-3">
              <code className="font-mono">{dataset.table_name}</code> · {formatNumber(dataset.row_count)} rows · {dataset.columns.length} columns
              {dataset.original_filename && ` · ${dataset.original_filename}`}
              {dataset.synced_at && ` · extracted ${timeAgo(dataset.synced_at)}`}
            </p>
          </div>
          {editable && (
            <div className="flex gap-2">
              {dataset.connection_id && (
                <Button size="sm" loading={refreshing} onClick={() => void refresh()}>
                  <RefreshCw className="size-4" aria-hidden /> Refresh extract
                </Button>
              )}
              <Button variant="danger" size="sm" onClick={() => void remove()}>
                <Trash2 className="size-4" aria-hidden /> Delete
              </Button>
            </div>
          )}
        </div>
        {dataset.description && <p className="max-w-2xl text-ink-2">{dataset.description}</p>}
      </div>
      {error && <ErrorBanner message={error} />}

      <section>
        <SectionTitle>Profile</SectionTitle>
        {dataset.profile ? (
          <ProfileView profile={dataset.profile} />
        ) : (
          <Card className="flex flex-wrap items-center justify-between gap-3 p-5">
            <p className="text-sm text-ink-2">Profile every column for distributions, missing values, and data-quality issues.</p>
            {editable && (
              <Button variant="primary" loading={profiling} onClick={() => void profile()}>
                Profile dataset
              </Button>
            )}
          </Card>
        )}
      </section>

      <section>
        <SectionTitle>Preview · first {dataset.preview.length} rows</SectionTitle>
        <DataTable columns={dataset.columns.map((c) => c.name)} rows={dataset.preview} />
      </section>
    </div>
  );
}
