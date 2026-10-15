"use client";

import { DatabaseZap, Download } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Button, Card, ErrorBanner, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { Connection, Dataset, SourceTable } from "@/lib/types";

const FIELDS = [
  { key: "name", label: "Name", placeholder: "Production replica" },
  { key: "host", label: "Host", placeholder: "db.example.com" },
  { key: "port", label: "Port", placeholder: "5432" },
  { key: "database", label: "Database", placeholder: "analytics" },
  { key: "user", label: "User", placeholder: "readonly_user" },
  { key: "password", label: "Password", placeholder: "", type: "password" },
] as const;

function ConnectForm({ ws, onConnected }: { ws: string; onConnected: () => void }) {
  const [form, setForm] = useState<Record<string, string>>({ port: "5432", sslmode: "require" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`/workspaces/${ws}/connections`, { ...form, port: Number(form.port) });
      onConnected();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        {FIELDS.map((f) => (
          <label key={f.key} className="space-y-1 text-sm">
            <span className="text-ink-2">{f.label}</span>
            <input
              type={"type" in f ? f.type : "text"}
              value={form[f.key] ?? ""}
              onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
              placeholder={f.placeholder}
              required={f.key !== "password"}
              className="h-9 w-full rounded-lg border border-line bg-surface px-3"
            />
          </label>
        ))}
        <label className="space-y-1 text-sm">
          <span className="text-ink-2">SSL</span>
          <select value={form.sslmode} onChange={(e) => setForm({ ...form, sslmode: e.target.value })} className="h-9 w-full rounded-lg border border-line bg-surface px-2">
            {["require", "verify-full", "prefer", "disable"].map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </label>
      </div>
      <p className="text-xs text-ink-3">
        Use a read-only database user. Inferra opens read-only transactions, pulls the tables you choose into a snapshot, and stores the password encrypted.
      </p>
      {error && <ErrorBanner message={error} />}
      <Button type="submit" variant="primary" loading={busy}>
        Test and save
      </Button>
    </form>
  );
}

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
                {t.estimated_rows > 0 ? `~${formatNumber(t.estimated_rows)} rows` : "row count not estimated"}
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
          {adding ? "Cancel" : "Connect Postgres"}
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
            <span className="font-medium">{c.name}</span>
            <span className="font-mono text-xs text-ink-3">
              {c.config.user}@{c.config.host}/{c.config.database}
            </span>
          </button>
          {open === c.id && <ConnectionTables ws={ws} connection={c} onImported={onImported} />}
        </div>
      ))}
      {connections.length === 0 && !adding && <p className="text-sm text-ink-3">Connect a Postgres database to analyze its tables alongside uploaded files.</p>}
    </Card>
  );
}
