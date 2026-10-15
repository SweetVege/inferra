"use client";

import { Brain } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge, Card, EmptyState, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber, timeAgo } from "@/lib/format";
import type { ModelSummary } from "@/lib/types";

function headline(m: ModelSummary): string {
  const h = m.metrics.holdout ?? {};
  return m.task === "classification" ? `AUC ${formatNumber(h.auc, 3)}` : `RMSE ${formatNumber(h.rmse)}`;
}

export default function ModelsPage() {
  const { ws } = useParams<{ ws: string }>();
  const [models, setModels] = useState<ModelSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<ModelSummary[]>(`/workspaces/${ws}/models`).then(setModels, (e) => setError(e.message));
  }, [ws]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Models</h1>
      {error && <ErrorBanner message={error} />}
      {!models && !error && <Spinner />}
      {models?.length === 0 && (
        <EmptyState icon={Brain} title="No models yet">
          Ask Inferra to predict something, for example &ldquo;Predict which customers will churn&rdquo;. Trained models appear here with their model cards.
        </EmptyState>
      )}
      {models && models.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {models.map((m) => (
            <Link key={m.id} href={`/w/${ws}/models/${m.id}`}>
              <Card className="h-full p-4 transition-colors hover:border-accent">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium">{m.name}</p>
                  <Badge>{m.task}</Badge>
                </div>
                <p className="mt-1 text-sm text-ink-2">
                  Predicts <code className="font-mono text-[13px]">{m.target}</code> from {m.features.length} features in{" "}
                  <code className="font-mono text-[13px]">{m.source_table}</code>
                </p>
                <p className="tabular mt-3 text-xs text-ink-3">
                  Holdout {headline(m)} · trained {timeAgo(m.created_at)}
                </p>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
