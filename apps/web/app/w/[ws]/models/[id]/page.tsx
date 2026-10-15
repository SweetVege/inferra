"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { canEdit, useWorkspace } from "@/components/session";
import { Badge, Button, Card, ErrorBanner, SectionTitle, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber, timeAgo } from "@/lib/format";
import type { Dataset, ModelDetail } from "@/lib/types";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] text-ink-3">{label}</dt>
      <dd className="tabular text-lg font-semibold">{value}</dd>
    </div>
  );
}

export default function ModelPage() {
  const { ws, id } = useParams<{ ws: string; id: string }>();
  const router = useRouter();
  const editable = canEdit(useWorkspace(ws));
  const [model, setModel] = useState<ModelDetail | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [target, setTarget] = useState("");
  const [scoring, setScoring] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<ModelDetail>(`/workspaces/${ws}/models/${id}`).then(setModel, (e) => setError(e.message));
    api.get<Dataset[]>(`/workspaces/${ws}/datasets`).then(setDatasets, () => {});
  }, [ws, id]);

  if (!model) return error ? <ErrorBanner message={error} /> : <Spinner />;
  const card = model.card;
  const h = model.metrics.holdout ?? {};
  const compatible = datasets.filter((d) => model.features.every((f) => d.columns.some((c) => c.name === f)));

  async function score() {
    setScoring(true);
    setError(null);
    try {
      const scored = await api.post<Dataset>(`/workspaces/${ws}/models/${id}/score`, { dataset_id: target });
      router.push(`/w/${ws}/datasets/${scored.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scoring failed.");
      setScoring(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div className="space-y-2">
        <Link href={`/w/${ws}/models`} className="inline-flex items-center gap-1 text-sm text-ink-3 hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Models
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">{model.name}</h1>
        <div className="flex flex-wrap items-center gap-2 text-sm text-ink-3">
          <Badge>{model.task}</Badge>
          <span>{card.algorithm}</span>
          <span>· trained {timeAgo(model.created_at)}</span>
          {model.run_id && (
            <Link href={`/w/${ws}/runs/${model.run_id}`} className="text-accent hover:underline">
              View the run
            </Link>
          )}
        </div>
      </div>
      {error && <ErrorBanner message={error} />}

      <section>
        <SectionTitle>Holdout performance</SectionTitle>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {model.task === "classification" ? (
            <>
              <Metric label="AUC" value={formatNumber(h.auc, 3)} />
              <Metric label="Average precision" value={formatNumber(h.average_precision, 3)} />
              <Metric label="Top-decile lift" value={`${formatNumber(h.top_decile_lift, 2)}×`} />
              <Metric label="Brier score" value={formatNumber(h.brier, 4)} />
            </>
          ) : (
            <>
              <Metric label="RMSE" value={formatNumber(h.rmse)} />
              <Metric label="MAE" value={formatNumber(h.mae)} />
              <Metric label="R²" value={formatNumber(h.r2, 3)} />
            </>
          )}
        </dl>
      </section>

      <section>
        <SectionTitle>Model card</SectionTitle>
        <Card className="divide-y divide-line text-sm">
          {[
            ["Intended use", card.intended_use],
            ["Target", card.target],
            [
              "Training data",
              `${card.training_data.table}: ${formatNumber(card.training_data.rows)} rows (${formatNumber(card.training_data.train_rows)} train, ${formatNumber(card.training_data.test_rows)} holdout)${card.training_data.sampled ? ", sampled" : ""}`,
            ],
            ["Features", card.features.join(", ")],
            ["Top drivers", card.top_drivers.join(", ")],
          ].map(([k, v]) => (
            <div key={k} className="grid gap-1 px-4 py-3 sm:grid-cols-[10rem_1fr]">
              <dt className="text-ink-3">{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
          <div className="grid gap-1 px-4 py-3 sm:grid-cols-[10rem_1fr]">
            <dt className="text-ink-3">Limitations</dt>
            <dd>
              <ul className="list-disc space-y-0.5 pl-5">
                {card.limitations.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </dd>
          </div>
        </Card>
      </section>

      {editable && (
        <section>
          <SectionTitle>Batch scoring</SectionTitle>
          <Card className="flex flex-wrap items-center gap-3 p-4">
            <select value={target} onChange={(e) => setTarget(e.target.value)} className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface px-2 text-sm" aria-label="Dataset to score">
              <option value="">Choose a dataset with the model&apos;s features…</option>
              {compatible.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.display_name} ({formatNumber(d.row_count)} rows)
                </option>
              ))}
            </select>
            <Button variant="primary" disabled={!target} loading={scoring} onClick={() => void score()}>
              Score dataset
            </Button>
            <p className="w-full text-xs text-ink-3">Creates a new dataset with a {model.task === "classification" ? `${model.target}_probability` : `predicted_${model.target}`} column.</p>
          </Card>
        </section>
      )}
    </div>
  );
}
