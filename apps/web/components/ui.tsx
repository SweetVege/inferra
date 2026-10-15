"use client";

import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  CircleDashed,
  Compass,
  Database,
  FlaskConical,
  Loader2,
  type LucideIcon,
  MinusCircle,
  Network,
  Sparkles,
  Brain,
  XCircle,
} from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }) {
  const styles: Record<Variant, string> = {
    primary: "bg-accent text-accent-ink hover:opacity-90",
    secondary: "bg-surface text-ink border border-line hover:bg-surface-2",
    ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
    danger: "bg-surface text-bad border border-line hover:bg-bad-soft",
  };
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-3 text-[13px]" : "h-9 px-4 text-sm",
        styles[variant],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx("rounded-xl border border-line bg-surface", className)}>{children}</div>;
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-[13px] font-semibold uppercase tracking-wide text-ink-3">{children}</h2>
      {action}
    </div>
  );
}

type Tone = "neutral" | "accent" | "good" | "warn" | "bad";

export function Badge({ tone = "neutral", children, icon: Icon }: { tone?: Tone; children: ReactNode; icon?: LucideIcon }) {
  const tones: Record<Tone, string> = {
    neutral: "bg-surface-2 text-ink-2",
    accent: "bg-accent-soft text-accent",
    good: "bg-good-soft text-good",
    warn: "bg-warn-soft text-warn",
    bad: "bg-bad-soft text-bad",
  };
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", tones[tone])}>
      {Icon && <Icon className="size-3.5" aria-hidden />}
      {children}
    </span>
  );
}

const RUN_STATUS: Record<string, { label: string; tone: Tone; icon: LucideIcon; spin?: boolean }> = {
  planning: { label: "Planning", tone: "accent", icon: Loader2, spin: true },
  needs_input: { label: "Needs your input", tone: "warn", icon: AlertTriangle },
  awaiting_approval: { label: "Plan ready", tone: "warn", icon: CircleDashed },
  running: { label: "Running", tone: "accent", icon: Loader2, spin: true },
  completed: { label: "Completed", tone: "good", icon: CheckCircle2 },
  failed: { label: "Failed", tone: "bad", icon: XCircle },
  cancelled: { label: "Cancelled", tone: "neutral", icon: MinusCircle },
  unsupported: { label: "Not available yet", tone: "neutral", icon: MinusCircle },
};

export function RunStatusBadge({ status }: { status: string }) {
  const s = RUN_STATUS[status] ?? { label: status, tone: "neutral" as Tone, icon: CircleDashed };
  const Icon = s.icon;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        { neutral: "bg-surface-2 text-ink-2", accent: "bg-accent-soft text-accent", good: "bg-good-soft text-good", warn: "bg-warn-soft text-warn", bad: "bg-bad-soft text-bad" }[s.tone],
      )}
    >
      <Icon className={cx("size-3.5", s.spin && "animate-spin")} aria-hidden />
      {s.label}
    </span>
  );
}

export const AGENTS: Record<string, { title: string; icon: LucideIcon; role: string }> = {
  orchestrator: { title: "Orchestrator", icon: Compass, role: "Plans the work" },
  data: { title: "Data Agent", icon: Database, role: "Finds and checks the data" },
  analyst: { title: "Analyst Agent", icon: BarChart3, role: "Computes and compares" },
  experiment: { title: "Experiment Agent", icon: FlaskConical, role: "Designs and reads A/B tests" },
  ml: { title: "ML Agent", icon: Brain, role: "Builds predictive models" },
  modeler: { title: "Data Modeler", icon: Network, role: "Designs and tests data models" },
  insight: { title: "Insight Agent", icon: Sparkles, role: "Writes verified insights" },
};

export function AgentAvatar({ agent, size = "md" }: { agent: string; size?: "sm" | "md" }) {
  const Icon = AGENTS[agent]?.icon ?? Sparkles;
  return (
    <span
      className={cx(
        "inline-flex shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent",
        size === "sm" ? "size-6" : "size-8",
      )}
      title={AGENTS[agent]?.title ?? agent}
    >
      <Icon className={size === "sm" ? "size-3.5" : "size-4"} aria-hidden />
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-3" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label ?? "Loading…"}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-line bg-bad-soft px-3 py-2 text-sm text-bad" role="alert">
      <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{message}</span>
    </div>
  );
}

export function EmptyState({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-line px-6 py-12 text-center">
      <Icon className="size-6 text-ink-3" aria-hidden />
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-md text-sm text-ink-2">{children}</div>}
    </div>
  );
}

export function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg bg-code px-3 py-2 font-mono text-[12px] leading-relaxed text-ink-2">
      {children}
    </pre>
  );
}
