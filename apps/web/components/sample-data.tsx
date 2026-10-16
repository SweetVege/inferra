"use client";

import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { Dataset } from "@/lib/types";

/** Load the sample datasets (orders, a checkout A/B test, churn, a loyalty program, a daily funnel,
 * order lines, daily sales) into a workspace. Safe to click twice: existing samples are skipped. */
export function SampleDataButton({ ws, onAdded, variant = "secondary" }: { ws: string; onAdded: (added: Dataset[]) => void; variant?: "primary" | "secondary" }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function add() {
    setBusy(true);
    setError(null);
    try {
      onAdded(await api.post<Dataset[]>(`/workspaces/${ws}/datasets/samples`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the samples.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="inline-flex flex-col items-center gap-1">
      <Button variant={variant} loading={busy} onClick={() => void add()}>
        <FlaskConical className="size-4" aria-hidden /> Load sample datasets
      </Button>
      {error && <span className="text-sm text-bad">{error}</span>}
    </span>
  );
}
