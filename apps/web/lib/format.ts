export function formatNumber(value: unknown, maxDigits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return value == null ? "—" : String(value);
  const abs = Math.abs(value);
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  // Keep tiny values (p-values, losses) visible instead of rounding them to 0.
  if (abs > 0 && abs < 0.001) return value.toExponential(1);
  if (abs >= 1000) return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  return value.toLocaleString("en-US", { maximumFractionDigits: abs < 1 ? Math.max(maxDigits, 3) : maxDigits });
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatCompact(value: number): string {
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const units: [number, string][] = [
    [60, "minute"],
    [3600, "hour"],
    [86400, "day"],
    [604800, "week"],
  ];
  let label = "";
  for (const [size, name] of units) {
    if (seconds >= size) {
      const n = Math.floor(seconds / size);
      label = `${n} ${name}${n === 1 ? "" : "s"} ago`;
    }
  }
  return label || new Date(iso).toLocaleDateString();
}

export function titleCase(text: string): string {
  return text.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function formatMetric(value: number | null | undefined, format: string): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (format === "percent") return formatPercent(value, 2);
  if (format === "currency") return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return formatNumber(value);
}
