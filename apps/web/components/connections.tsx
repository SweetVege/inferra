"use client";

import { DatabaseZap, Download } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Badge, Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { Connection, ConnectionKind, Dataset, SourceTable } from "@/lib/types";

type Field = { key: string; label: string; placeholder?: string; type?: "password" | "textarea" | "number"; optional?: boolean; wide?: boolean };

const KINDS: { kind: ConnectionKind; label: string; fields: Field[]; defaults: Record<string, string>; note: string }[] = [
  {
    kind: "postgres",
    label: "PostgreSQL",
    defaults: { port: "5432", sslmode: "require" },
    fields: [
      { key: "host", label: "Host", placeholder: "db.example.com" },
      { key: "port", label: "Port", placeholder: "5432", type: "number" },
      { key: "database", label: "Database", placeholder: "analytics" },
      { key: "user", label: "User", placeholder: "readonly_user" },
      { key: "password", label: "Password", type: "password", optional: true },
    ],
    note: "Use a read-only database user. Inferra opens read-only transactions with a statement timeout and stores the password encrypted.",
  },
  {
    kind: "bigquery",
    label: "BigQuery",
    defaults: { maximum_gb_billed: "10" },
    fields: [
      { key: "project_id", label: "Project ID", placeholder: "acme-analytics" },
      { key: "dataset", label: "Dataset (optional)", placeholder: "sales", optional: true },
      { key: "location", label: "Location (optional)", placeholder: "US", optional: true },
      { key: "maximum_gb_billed", label: "Max GB scanned per query", type: "number" },
      { key: "credentials_json", label: "Service account key (JSON)", type: "textarea", wide: true },
    ],
    note: "Grant the service account BigQuery Data Viewer and BigQuery Job User. Every query is dry-run first and refused if it is not a SELECT or would scan more than the limit.",
  },
  {
    kind: "snowflake",
    label: "Snowflake",
    defaults: { schema: "PUBLIC" },
    fields: [
      { key: "account", label: "Account identifier", placeholder: "myorg-myaccount" },
      { key: "user", label: "User", placeholder: "INFERRA_READER" },
      { key: "warehouse", label: "Warehouse", placeholder: "ANALYTICS_XS" },
      { key: "database", label: "Database", placeholder: "ANALYTICS" },
      { key: "schema", label: "Schema", placeholder: "PUBLIC" },
      { key: "role", label: "Role (optional)", placeholder: "READ_ONLY", optional: true },
      { key: "password", label: "Password (or use a key)", type: "password", optional: true },
      { key: "private_key", label: "Private key, PEM (instead of a password)", type: "textarea", optional: true, wide: true },
    ],
    note: "Use a role that can only read. Custom SQL runs as a single wrapped SELECT with a two-minute timeout, and credentials are stored encrypted.",
  },
];

function ConnectForm({ ws, onConnected }: { ws: string; onConnected: () => void }) {
  const [kind, setKind] = useState<ConnectionKind>("postgres");
  const spec = KINDS.find((k) => k.kind === kind)!;
  const [form, setForm] = useState<Record<string, string>>({ ...spec.defaults });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = { kind };
    for (const [key, value] of Object.entries(form)) {
      const field = spec.fields.find((f) => f.key === key);
      if (key !== "name" && !field) continue;
      if (value === "" && field?.optional) continue;
      body[key] = field?.type === "number" ? Number(value) : value;
    }
    try {
      await api.post(`/workspaces/${ws}/connections`, body);
      onConnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="flex flex-wrap gap-1 rounded-lg bg-surface-2 p-1 text-sm" role="radiogroup" aria-label="Database type">
        {KINDS.map((k) => (
          <button
            key={k.kind}
            type="button"
            role="radio"
            aria-checked={kind === k.kind}
            onClick={() => {
              setKind(k.kind);
              setForm({ name: form.name ?? "", ...k.defaults });
            }}
            className={kind === k.kind ? "rounded-md bg-surface px-3 py-1 font-medium shadow-sm" : "rounded-md px-3 py-1 text-ink-2 hover:text-ink"}
          >
            {k.label}
          </button>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="space-y-1 text-sm">
          <span className="text-ink-2">Name</span>
          <input value={form.name ?? ""} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Production warehouse" required className="h-9 w-full rounded-lg border border-line bg-surface px-3" />
        </label>
        {spec.fields.map((f) => (
          <label key={f.key} className={f.wide ? "space-y-1 text-sm sm:col-span-3" : "space-y-1 text-sm"}>
            <span className="text-ink-2">{f.label}</span>
            {f.type === "textarea" ? (
              <textarea
                value={form[f.key] ?? ""}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                required={!f.optional}
                rows={4}
                spellCheck={false}
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[12px]"
              />
            ) : (
              <input
                type={f.type === "password" ? "password" : "text"}
                inputMode={f.type === "number" ? "decimal" : undefined}
                value={form[f.key] ?? ""}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                placeholder={f.placeholder}
                required={!f.optional}
                className="h-9 w-full rounded-lg border border-line bg-surface px-3"
              />
            )}
          </label>
        ))}
        {kind === "postgres" && (
          <label className="space-y-1 text-sm">
            <span className="text-ink-2">SSL</span>
            <select value={form.sslmode} onChange={(e) => setForm({ ...form, sslmode: e.target.value })} className="h-9 w-full rounded-lg border border-line bg-surface px-2">
              {["require", "verify-full", "prefer", "disable"].map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      <p className="text-xs text-ink-3">{spec.note} Tables you import become snapshots you can refresh.</p>
      {error && <ErrorBanner message={error} />}
      <Button type="submit" variant="primary" loading={busy}>
        Test and save
      </Button>
    </form>
  );
}

function describe(c: Connection): string {
  const k = c.config;
  if (c.kind === "bigquery") return `${k.project_id}${k.dataset ? `.${k.dataset}` : ""}`;
  if (c.kind === "snowflake") return `${k.user}@${k.account}/${k.database}.${k.schema}`;
  return `${k.user}@${k.host}/${k.database}`;
}

const KIND_LABEL: Record<string, string> = { postgres: "Postgres", bigquery: "BigQuery", snowflake: "Snowflake" };

function ConnectionTables({ ws, connection, onImported }: { ws: string; connection: Connection; onImported: (d: Dataset) => void }) {
  const [tables, setTables] = useState<SourceTable[] | null>(null);
  const [sql, setSql] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<SourceTable[]>(`/workspaces/${ws}/connections/${connection.id}/tables`).then(setTables, (e) => setError(e.message));
  }, [ws, connection.id]);
  async function extract(key: string, body: Record<string, string>) {
    setBusy(key);
    setError(null);
    try {
      onImported(await api.post<Dataset>(`/workspaces/${ws}/connections/${connection.id}/extracts`, body));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed.");
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="space-y-3">
      {error && <ErrorBanner message={error} />}
      {!tables && !error && <Spinner />}
      {tables && (
        <ul className="max-h-64 divide-y divide-line overflow-y-auto rounded-lg border border-line text-sm">
          {tables.map((t) => (
            <li key={`${t.schema}.${t.table}`} className="flex items-center justify-between gap-2 px-3 py-2">
              <span className="min-w-0 truncate font-mono text-[13px]">
                {t.schema}.{t.table}
              </span>
              <span className="flex shrink-0 items-center gap-3 text-xs text-ink-3">
                {t.estimated_rows ? `~${formatNumber(t.estimated_rows)} rows` : "row count not estimated"}
                <Button size="sm" loading={busy === t.table} onClick={() => void extract(t.table, { schema: t.schema, table: t.table })}>
                  <Download className="size-3.5" aria-hidden /> Import
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer text-accent">Import the result of a query</summary>
        <div className="mt-2 space-y-2">
          <textarea value={sql} onChange={(e) => setSql(e.target.value)} rows={4} placeholder="SELECT date_trunc('day', created_at) AS day, count(*) AS signups FROM users GROUP BY 1" className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-[13px]" />
          <div className="flex gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="dataset name" className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface px-3" />
            <Button disabled={!sql.trim()} loading={busy === "sql"} onClick={() => void extract("sql", { sql, name })}>
              Import query
            </Button>
          </div>
        </div>
      </details>
    </div>
  );
}

export function ConnectionsPanel({ ws, onImported }: { ws: string; onImported: (d: Dataset) => void }) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => api.get<Connection[]>(`/workspaces/${ws}/connections`).then(setConnections, () => {}), [ws]);
  useEffect(() => {
    void load();
  }, [load]);
  return (
    <Card className="space-y-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <DatabaseZap className="size-4 text-accent" aria-hidden />
          <h2 className="font-semibold">Databases</h2>
        </div>
        <Button size="sm" onClick={() => setAdding(!adding)}>
          {adding ? "Cancel" : "Connect a database"}
        </Button>
      </div>
      {adding && (
        <ConnectForm
          ws={ws}
          onConnected={() => {
            setAdding(false);
            void load();
          }}
        />
      )}
      {connections.map((c) => (
        <div key={c.id} className="space-y-2 rounded-lg border border-line p-3">
          <button onClick={() => setOpen(open === c.id ? null : c.id)} className="flex w-full items-center justify-between text-left text-sm" aria-expanded={open === c.id}>
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate font-medium">{c.name}</span>
              <Badge>{KIND_LABEL[c.kind] ?? c.kind}</Badge>
            </span>
            <span className="min-w-0 truncate font-mono text-xs text-ink-3">{describe(c)}</span>
          </button>
          {open === c.id && <ConnectionTables ws={ws} connection={c} onImported={onImported} />}
        </div>
      ))}
      {connections.length === 0 && !adding && <p className="text-sm text-ink-3">Connect PostgreSQL, BigQuery, or Snowflake to analyze warehouse tables alongside uploaded files.</p>}
    </Card>
  );
}
