export type Role = "owner" | "editor" | "viewer";

export interface User {
  id: string;
  login: string;
  name: string | null;
  avatar_url: string | null;
  is_demo: boolean;
}

export interface Workspace {
  id: string;
  name: string;
  is_demo: boolean;
  auto_approve_plans: boolean;
  role: Role;
}

export interface Providers {
  github: boolean;
  dev_login: boolean;
  demo: boolean;
}

export interface IntentInfo {
  intent: string;
  title: string;
  description: string;
  examples: string[];
  available: boolean;
  phase: string;
}

export interface Meta {
  llm_available: boolean;
  intents: IntentInfo[];
}

export interface Column {
  name: string;
  type: string;
}

export interface Dataset {
  id: string;
  table_name: string;
  display_name: string;
  description: string | null;
  original_filename: string | null;
  source_kind: string;
  row_count: number;
  columns: Column[];
  has_profile: boolean;
  created_at: string;
}

export interface HistogramBin {
  bin_start: number;
  bin_end: number;
  count: number;
}

export interface TopValue {
  value: string;
  count: number;
  share: number;
}

export interface ColumnProfile {
  type: string;
  kind: "numeric" | "categorical" | "temporal" | "boolean" | "other";
  null_count: number;
  null_rate: number;
  distinct_count: number;
  min?: number | string | null;
  max?: number | string | null;
  mean?: number | null;
  std?: number | null;
  p25?: number | null;
  median?: number | null;
  p75?: number | null;
  outlier_count?: number;
  negative_count?: number;
  future_count?: number;
  histogram?: HistogramBin[];
  top_values?: TopValue[];
}

export interface Finding {
  severity: "high" | "medium" | "low" | "info";
  column: string | null;
  issue: string;
  detail: string;
  metric: string;
  value: number;
}

export interface Profile {
  table: string;
  row_count: number;
  column_count: number;
  duplicate_rows: number;
  columns: Record<string, ColumnProfile>;
  findings: Finding[];
}

export interface DatasetDetail extends Dataset {
  preview: Record<string, unknown>[];
  profile: Profile | null;
}

export type RunStatus =
  | "planning"
  | "needs_input"
  | "awaiting_approval"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "unsupported";

export interface RunSummary {
  id: string;
  goal: string;
  status: RunStatus;
  intent: string | null;
  created_at: string;
  updated_at: string;
  insight_count: number;
}

export interface PlanStep {
  key: string;
  agent: string;
  task: string;
  depends_on: string[];
}

export interface Plan {
  intent: string;
  title: string;
  params: Record<string, string>;
  steps: PlanStep[];
  dataset_ids: string[];
  rationale: string;
  confidence: string;
}

export interface Step {
  id: string;
  key: string;
  position: number;
  agent: string;
  task: string;
  depends_on: string[];
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  summary: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ToolCall {
  id: string;
  step_id: string;
  agent: string;
  tool: string;
  input: Record<string, unknown>;
  code: string | null;
  artifact_id: string | null;
  error: string | null;
  duration_ms: number;
  created_at: string;
}

export interface Artifact {
  id: string;
  step_id: string | null;
  kind: string;
  title: string;
  data: Record<string, unknown>;
}

export interface Evidence {
  artifact_id: string;
  path: string;
  value: number | string;
  label?: string;
  kind?: string;
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string;
}

export type InsightStatus = "draft" | "accepted" | "edited" | "rejected";

export interface Insight {
  id: string;
  run_id: string;
  position: number;
  title: string;
  claim: string;
  evidence: Evidence[];
  method: string;
  caveats: string[];
  recommended_action: string | null;
  confidence: "high" | "medium" | "low";
  checks: Check[];
  verified: boolean;
  status: InsightStatus;
  original_claim: string | null;
  review_note: string | null;
  reviewed_at: string | null;
}

export interface RunEvent {
  id: number;
  kind: string;
  message: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface RunDetail {
  id: string;
  goal: string;
  status: RunStatus;
  intent: string | null;
  plan: Plan | null;
  dataset_ids: string[];
  clarifications: { question: string; answer: string | null }[];
  error: string | null;
  usage: Partial<Record<"input_tokens" | "output_tokens" | "cache_read_input_tokens" | "requests", number>>;
  created_at: string;
  updated_at: string;
  steps: Step[];
  tool_calls: ToolCall[];
  artifacts: Artifact[];
  insights: Insight[];
  events: RunEvent[];
}
