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

export interface RunQuota {
  allowed: boolean;
  limit: number | null;
  used: number;
  remaining: number | null;
  reason: string | null;
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
  connection_id: string | null;
  data_model_id: string | null;
  synced_at: string | null;
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
  schedule_id: string | null;
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
  original_title: string | null;
  original_action: string | null;
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
  schedule_id: string | null;
  schedules: Schedule[];
  alerts: Alert[];
}

export interface ModelSummary {
  id: string;
  run_id: string | null;
  name: string;
  task: "classification" | "regression";
  target: string;
  source_table: string;
  features: string[];
  metrics: { cv?: Record<string, unknown>; holdout?: Record<string, number>; baseline?: Record<string, unknown> };
  created_at: string;
}

export interface ModelCard {
  name: string;
  task: string;
  target: string;
  algorithm: string;
  training_data: {
    table: string;
    rows: number;
    train_rows: number;
    test_rows: number;
    sampled: boolean;
    validation?: { split: "random" | "grouped"; group_column?: string; groups?: number; test_groups?: number };
  };
  features: string[];
  excluded_features: { feature: string; reason: string }[];
  top_drivers: string[];
  intended_use: string;
  limitations: string[];
}

export interface ModelDetail extends ModelSummary {
  card: ModelCard;
}

export interface MetricPart {
  kind: "sum" | "count" | "count_distinct";
  column: string | null;
}

export interface MetricDefinition {
  id: string;
  dataset_id: string;
  dataset_name: string | null;
  name: string;
  label: string;
  description: string | null;
  kind: "sum" | "count" | "count_distinct" | "average" | "ratio";
  column: string | null;
  numerator: MetricPart | null;
  denominator: MetricPart | null;
  filter: string | null;
  time_column: string | null;
  dimensions: string[];
  format: "number" | "percent" | "currency";
  created_at: string;
  current_value: number | null;
}

export interface MetricValues {
  total: { value: number | null };
  grain: string;
  series: { period: string; value: number | null }[];
}

export type ConnectionKind = "postgres" | "bigquery" | "snowflake";

export interface Connection {
  id: string;
  name: string;
  kind: ConnectionKind;
  config: Record<string, string | number | null>;
  created_at: string;
}

export interface SourceTable {
  schema: string;
  table: string;
  type: string;
  estimated_rows: number | null;
}

export type Layer = "staging" | "dimension" | "fact" | "mart";

export interface TestSpec {
  type: "unique" | "not_null" | "accepted_values" | "relationships";
  values?: string[] | null;
  to?: string | null;
  field?: string | null;
  severity?: "error" | "warn";
}

export interface NodeSpec {
  name: string;
  layer: Layer;
  description: string;
  sql: string;
  materialized: "table" | "view";
  grain: string[];
  columns: { name: string; description: string; tests: TestSpec[] }[];
}

export interface BuiltNode {
  name: string;
  layer: Layer;
  description: string;
  depends_on: string[];
  sql: string;
  grain: string[];
  status: "ok" | "error" | "skipped";
  error?: string;
  row_count?: number;
  columns?: { name: string; type: string; description: string }[];
  preview?: Record<string, unknown>[];
}

export interface TestResult {
  id: string;
  node: string;
  column: string | null;
  test: string;
  description: string;
  severity: "error" | "warn";
  status: "pass" | "fail" | "warn" | "error" | "skipped";
  failures: number | null;
  sample?: Record<string, unknown>[];
  error?: string;
  sql?: string;
}

export interface BuildSummary {
  nodes: number;
  nodes_built: number;
  nodes_failed: number;
  nodes_skipped: number;
  tests: number;
  tests_passed: number;
  tests_failed: number;
  tests_warned: number;
  duration_ms: number;
}

export interface BuildResult {
  name: string;
  sources: { name: string; row_count: number | null; columns: number }[];
  nodes: BuiltNode[];
  edges: { from: string; to: string }[];
  tests: TestResult[];
  summary: BuildSummary;
  build_ok: boolean;
  data_model_id?: string;
}

export interface DataModelSummary {
  id: string;
  name: string;
  description: string;
  run_id: string | null;
  created_at: string;
  updated_at: string;
  built_at: string | null;
  node_count: number;
  summary: BuildSummary | null;
  stale: boolean;
}

export interface DataModelDetail extends DataModelSummary {
  nodes: NodeSpec[];
  last_build: BuildResult | null;
  published: { id: string; table_name: string; node: string; row_count: number; synced_at: string | null }[];
  sources: string[];
}

export interface InsightFeedItem extends Insight {
  run_goal: string;
  run_intent: string | null;
  created_at: string;
}

export interface Schedule {
  id: string;
  source_run_id: string | null;
  goal: string;
  intent: string;
  plan_title: string | null;
  cadence: "daily" | "weekly";
  weekday: number;
  hour: number;
  description: string;
  enabled: boolean;
  next_run_at: string;
  last_run_at: string | null;
  created_at: string;
  open_alerts: number;
  recent_runs: { id: string; status: RunStatus; created_at: string }[];
}

export interface Alert {
  id: string;
  run_id: string;
  schedule_id: string | null;
  artifact_id: string | null;
  title: string;
  message: string;
  data: {
    metric?: { label?: string; format?: string };
    period?: string;
    value?: number;
    baseline?: number;
    relative_change?: number;
    grain?: string;
  };
  status: "open" | "acknowledged";
  acknowledged_at: string | null;
  created_at: string;
}

export interface Overview {
  datasets: number;
  runs: number;
  completed_runs: number;
  reviewed_insights: number;
  reports: number;
  published_reports: number;
  schedules: number;
  open_alerts: number;
}

export interface ReportInsight {
  id: string;
  run_id: string;
  title: string;
  claim: string;
  edited: boolean;
  original_claim: string | null;
  original_title: string | null;
  original_action: string | null;
  evidence: Evidence[];
  method: string;
  caveats: string[];
  recommended_action: string | null;
  confidence: "high" | "medium" | "low";
  verified: boolean;
  checks: { name: string; passed: boolean }[];
  status: InsightStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface ReportContent {
  title: string;
  summary: string;
  workspace: string;
  insights: ReportInsight[];
  artifacts: Artifact[];
  appendix: {
    run_id: string;
    goal: string;
    intent: string | null;
    plan_title: string | null;
    ran_at: string | null;
    datasets: { name: string; table: string; rows: number }[];
    steps: { agent: string; task: string; summary: string | null }[];
    computations: { artifact_id: string; title: string; tool: string | null; code: string | null }[];
  }[];
  published_at?: string;
  published_by?: string;
}

export interface ReportSummary {
  id: string;
  title: string;
  insight_count: number;
  state: "draft" | "published" | "changed";
  shared: boolean;
  published_at: string | null;
  updated_at: string;
}

export interface ReportDetail extends ReportSummary {
  summary: string;
  insight_ids: string[];
  share_token: string | null;
  content: ReportContent;
  published: ReportContent | null;
}
