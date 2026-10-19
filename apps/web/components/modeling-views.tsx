"use client";

import { AlertTriangle, CheckCircle2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { BarList, IntervalPlot, LineChart } from "@/components/charts";
import { DataTable } from "@/components/data-table";
import { Badge } from "@/components/ui";
import { formatNumber, formatPercent } from "@/lib/format";

type Row = Record<string, unknown>;
const num = (v: unknown) => (typeof v === "number" ? v : NaN);

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-[11px] text-ink-3">{label}</dt>
      <dd className="tabular text-lg font-semibold">{value}</dd>
      {hint && <dd className="text-[11px] text-ink-3">{hint}</dd>}
    </div>
  );
}

function Check({ ok, good, bad }: { ok: boolean; good: string; bad: string }) {
  return ok ? (
    <Badge tone="good" icon={CheckCircle2}>
      {good}
    </Badge>
  ) : (
    <Badge tone="bad" icon={AlertTriangle}>
      {bad}
    </Badge>
  );
}

function Notes({ notes }: { notes?: unknown }) {
  if (!Array.isArray(notes) || notes.length === 0) return null;
  return (
    <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-3">
      {notes.map((n, i) => (
        <li key={i}>{String(n)}</li>
      ))}
    </ul>
  );
}

export function RegressionView({ d }: { d: Row }) {
  const logistic = String(d.model).startsWith("logistic");
  const coefs = (d.coefficients as Row[]).filter((c) => c.term !== "intercept");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge>{String(d.model)}</Badge>
        <Badge>{String(d.standard_errors)}</Badge>
        <Badge tone="warn">Associations, not causal effects</Badge>
      </div>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Rows used" value={formatNumber(d.n)} hint={num(d.dropped_rows) ? `${formatNumber(d.dropped_rows)} dropped (missing)` : undefined} />
        {logistic ? <Stat label="Pseudo R²" value={formatNumber(d.pseudo_r_squared, 3)} /> : <Stat label="R²" value={formatNumber(d.r_squared, 3)} hint={`adjusted ${formatNumber(d.adj_r_squared, 3)}`} />}
      </dl>
      <IntervalPlot
        label={logistic ? "Odds ratios with 95% CIs" : "Coefficients with 95% CIs"}
        reference={logistic ? 1 : 0}
        format={(v) => formatNumber(v, 3)}
        rows={coefs.map((c) =>
          logistic
            ? { label: String(c.term), value: num(c.odds_ratio), low: num(c.odds_ratio_ci_low), high: num(c.odds_ratio_ci_high) }
            : { label: String(c.term), value: num(c.estimate), low: num(c.ci_low), high: num(c.ci_high) },
        )}
      />
      <DataTable columns={["term", "estimate", "std_error", "ci_low", "ci_high", "p_value", ...(logistic ? ["odds_ratio"] : [])]} rows={d.coefficients as Row[]} />
      <Notes notes={d.notes} />
    </div>
  );
}

export function DidView({ d }: { d: Row }) {
  const series = (d.series as Row[]) ?? [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Check ok={Boolean(d.pretrend_ok)} good={`Pre-trends parallel (p = ${formatNumber(d.pretrend_p_value, 3)})`} bad={`Pre-trends diverge (p = ${formatNumber(d.pretrend_p_value, 3)})`} />
        <Badge>{formatNumber(d.n_treated_units)} treated of {formatNumber(d.n_units)} units</Badge>
      </div>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Effect per unit-period" value={formatNumber(d.att)} hint={`95% CI ${formatNumber(d.ci_low)} – ${formatNumber(d.ci_high)}`} />
        <Stat
          label="Relative effect"
          value={formatPercent(num(d.relative_effect))}
          hint={`95% CI ${formatPercent(num(d.relative_effect_ci_low))} – ${formatPercent(num(d.relative_effect_ci_high))}`}
        />
        <Stat label="p-value" value={formatNumber(d.p_value, 4)} hint="clustered by unit" />
      </dl>
      <LineChart
        label="Treated and control group means by period"
        marker={{ x: String(series.find((s) => String(s.period) >= String(d.post_start))?.period ?? d.post_start), label: "treatment starts" }}
        series={[
          { name: "Treated", color: "var(--series-1)", points: series.map((s) => ({ x: String(s.period), y: num(s.treated_mean) })) },
          { name: "Control", color: "var(--series-2)", points: series.map((s) => ({ x: String(s.period), y: num(s.control_mean) })) },
        ]}
      />
    </div>
  );
}

export function PsmView({ d }: { d: Row }) {
  const balance = (d.balance as Row[]) ?? [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Check ok={Boolean(d.balance_ok)} good={`Covariates balanced (max |SMD| ${formatNumber(d.max_abs_smd_after, 3)})`} bad={`Imbalance remains (max |SMD| ${formatNumber(d.max_abs_smd_after, 3)})`} />
        <Badge>
          {formatNumber(d.n_matched)} of {formatNumber(d.n_treated)} treated matched
        </Badge>
      </div>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Effect on the treated" value={formatNumber(d.att, 4)} hint={`95% CI ${formatNumber(d.ci_low, 4)} – ${formatNumber(d.ci_high, 4)}`} />
        <Stat label="Relative to matched controls" value={formatPercent(num(d.relative_effect))} />
        <Stat label="p-value" value={formatNumber(d.p_value, 4)} />
      </dl>
      <div className="space-y-1">
        <p className="text-xs text-ink-3">Standardized mean difference, before → after matching (|SMD| below 0.1 is balanced)</p>
        <DataTable
          columns={["covariate", "smd_before", "smd_after"]}
          rows={balance.map((b) => ({ ...b, smd_before: formatNumber(b.smd_before, 3), smd_after: formatNumber(b.smd_after, 3) }))}
        />
      </div>
      <Notes notes={d.notes} />
    </div>
  );
}

export function ForecastView({ d }: { d: Row }) {
  const history = (d.history as Row[]) ?? [];
  const forecasts = (d.forecasts as Row[]) ?? [];
  const total = (d.horizon_total as Row) ?? {};
  const bt = (d.backtest as Row) ?? {};
  const last = history[history.length - 1];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge>{String(d.model)}</Badge>
        <Check ok={num(bt.mape) < num(bt.seasonal_naive_mape)} good="Beats seasonal-naive in backtest" bad="Does not beat seasonal-naive" />
      </div>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label={`Next ${d.horizon} ${d.freq}s, total`} value={formatNumber(Math.round(num(total.forecast)))} hint={`95% interval ${formatNumber(Math.round(num(total.lower)))} – ${formatNumber(Math.round(num(total.upper)))}`} />
        <Stat label="Backtest error (MAPE)" value={formatPercent(num(bt.mape))} hint={`seasonal-naive ${formatPercent(num(bt.seasonal_naive_mape))}`} />
        <Stat label="History" value={`${formatNumber(d.history_periods)} ${d.freq}s`} />
      </dl>
      <LineChart
        label="History and forecast with 95% prediction interval"
        series={[
          { name: "Actual", color: "var(--series-1)", points: history.map((h) => ({ x: String(h.period), y: num(h.value) })) },
          {
            name: "Forecast (95% interval)",
            color: "var(--series-1)",
            dashed: true,
            points: [...(last ? [{ x: String(last.period), y: num(last.value) }] : []), ...forecasts.map((f) => ({ x: String(f.period), y: num(f.forecast) }))],
            band: forecasts.map((f) => ({ x: String(f.period), low: num(f.lower), high: num(f.upper) })),
          },
        ]}
      />
    </div>
  );
}

export function ModelView({ d }: { d: Row }) {
  const { ws } = useParams<{ ws: string }>();
  const classification = d.task === "classification";
  const h = (d.holdout as Row) ?? {};
  const base = (d.baseline as Row) ?? {};
  // When the linear baseline was selected, compare it with the tree model instead of itself.
  const tree = d.selected_model !== "gradient-boosted trees" ? ((d.tree_model_holdout as Row) ?? null) : null;
  const cv = (d.cv as Row) ?? {};
  const drivers = (d.drivers as Row[]) ?? [];
  const calibration = (d.calibration as Row[]) ?? [];
  const confusion = (h.confusion as Row) ?? null;
  const excluded = (d.excluded_features as Row[]) ?? [];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">Selected: {String(d.selected_model)}</Badge>
        <Badge>
          {formatNumber(d.n_train)} train · {formatNumber(d.n_test)} holdout
        </Badge>
        {(d.validation as Row | undefined)?.split === "grouped" && (
          <Badge>
            Held out by {String((d.validation as Row).group_column)}: {formatNumber((d.validation as Row).test_groups)} of{" "}
            {formatNumber((d.validation as Row).groups)} groups unseen in training
          </Badge>
        )}
        {d.beats_naive === false && (
          <Badge tone="bad" icon={AlertTriangle}>
            Not meaningfully better than {classification ? "random ranking" : "predicting the mean"} on the holdout
          </Badge>
        )}
        {typeof d.model_id === "string" && (
          <Link href={`/w/${ws}/models/${d.model_id}`} className="text-xs text-accent hover:underline">
            Open in model registry →
          </Link>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {classification ? (
          <>
            <Stat
              label="Holdout AUC"
              value={formatNumber(h.auc, 3)}
              hint={tree ? `trees ${formatNumber(tree.auc, 3)}` : `baseline ${formatNumber(base.auc, 3)}`}
            />
            <Stat label="Average precision" value={formatNumber(h.average_precision, 3)} hint={`positive rate ${formatPercent(num(h.positive_rate))}`} />
            <Stat label="Top-decile lift" value={`${formatNumber(h.top_decile_lift, 2)}×`} hint={`captures ${formatPercent(num(h.top_decile_capture))}`} />
          </>
        ) : (
          <>
            <Stat
              label="Holdout RMSE"
              value={formatNumber(h.rmse)}
              hint={tree ? `trees ${formatNumber(tree.rmse)}` : `baseline ${formatNumber(base.rmse)}`}
            />
            <Stat label="Holdout R²" value={formatNumber(h.r2, 3)} />
            <Stat label="MAE" value={formatNumber(h.mae)} />
          </>
        )}
        <Stat label={`${Array.isArray(cv.folds) ? cv.folds.length : 5}-fold CV ${String(cv.metric).toUpperCase()}`} value={formatNumber(cv.mean, 3)} hint={`± ${formatNumber(cv.std, 3)}`} />
      </dl>
      <div className="grid gap-5 md:grid-cols-2">
        <div className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-3">What the model relies on (SHAP)</h4>
          <BarList
            format={(v) => formatPercent(v)}
            rows={drivers.slice(0, 8).map((r) => ({
              label: String(r.feature),
              value: num(r.share_of_importance),
              note: String(r.direction ?? (r.raises_most ? `${r.raises_most} raises, ${r.lowers_most} lowers` : "")),
            }))}
          />
          <p className="text-[11px] text-ink-3">Describes the model&apos;s behavior, not causes.</p>
        </div>
        {classification && calibration.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-3">Calibration (holdout deciles)</h4>
            <DataTable
              columns={["predicted", "observed", "n"]}
              rows={calibration.map((c) => ({ ...c, predicted: formatPercent(num(c.predicted)), observed: formatPercent(num(c.observed)) }))}
            />
          </div>
        )}
      </div>
      {confusion && (
        <p className="tabular text-xs text-ink-2">
          At threshold {formatNumber(h.threshold, 3)} (best F1 {formatNumber(h.f1, 3)}): {formatNumber(confusion.tp)} true positives,{" "}
          {formatNumber(confusion.fp)} false positives, {formatNumber(confusion.fn)} missed, {formatNumber(confusion.tn)} true negatives.
        </p>
      )}
      {excluded.length > 0 && (
        <div className="text-xs">
          <p className="font-medium text-ink-2">Excluded inputs</p>
          <ul className="list-disc pl-5 text-ink-3">
            {excluded.map((e) => (
              <li key={String(e.feature)}>
                <code className="font-mono">{String(e.feature)}</code>: {String(e.reason)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
