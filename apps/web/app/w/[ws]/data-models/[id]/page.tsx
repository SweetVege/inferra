"use client";

import { ArrowLeft, Download, Hammer, Plus, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { BuildStats, graphNodes, LineageGraph, NodeOutput, TestResults } from "@/components/data-model-views";
import { canEdit, useWorkspace } from "@/components/session";
import { Badge, Button, Card, CodeBlock, ErrorBanner, SectionTitle, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber, timeAgo } from "@/lib/format";
import type { DataModelDetail, Dataset, Layer, NodeSpec } from "@/lib/types";

const LAYERS: Layer[] = ["staging", "dimension", "fact", "mart"];
const input = "h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm";

interface Draft {
  name: string;
  layer: Layer;
  description: string;
  sql: string;
  materialized: "table" | "view";
  grain: string;
  columns: string;
}

function toDraft(n: NodeSpec): Draft {
  return {
    name: n.name,
    layer: n.layer,
    description: n.description,
    sql: n.sql,
    materialized: n.materialized,
    grain: n.grain.join(", "),
    columns: JSON.stringify(n.columns, null, 2),
  };
}

function fromDraft(d: Draft): NodeSpec {
  let columns: NodeSpec["columns"];
  try {
    columns = JSON.parse(d.columns || "[]");
  } catch {
    throw new Error("Columns and tests must be valid JSON.");
  }
  return {
    name: d.name.trim(),
    layer: d.layer,
    description: d.description,
    sql: d.sql,
    materialized: d.materialized,
    grain: d.grain
      .split(",")
      .map((g) => g.trim())
      .filter(Boolean),
    columns,
  };
}

function NodeEditor({
  draft,
  setDraft,
  busy,
  onSave,
  onDelete,
}: {
  draft: Draft;
  setDraft: (d: Draft) => void;
  busy: string | null;
  onSave: (build: boolean) => void;
  onDelete: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-4">
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="text-ink-2">Name</span>
          <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={`${input} font-mono`} />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-ink-2">Layer</span>
          <select value={draft.layer} onChange={(e) => setDraft({ ...draft, layer: e.target.value as Layer })} className={input}>
            {LAYERS.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-ink-2">Materialized as</span>
          <select value={draft.materialized} onChange={(e) => setDraft({ ...draft, materialized: e.target.value as Draft["materialized"] })} className={input}>
            <option>table</option>
            <option>view</option>
          </select>
        </label>
      </div>
      <label className="block space-y-1 text-sm">
        <span className="text-ink-2">Description</span>
        <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} className={input} />
      </label>
      <label className="block space-y-1 text-sm">
        <span className="text-ink-2">
          SQL (DuckDB). Read datasets with <code className="font-mono text-[13px]">{"{{ source('table') }}"}</code> and other nodes with{" "}
          <code className="font-mono text-[13px]">{"{{ ref('node') }}"}</code>.
        </span>
        <textarea
          value={draft.sql}
          onChange={(e) => setDraft({ ...draft, sql: e.target.value })}
          rows={Math.min(22, Math.max(6, draft.sql.split("\n").length + 1))}
          spellCheck={false}
          className="w-full rounded-lg border border-line bg-code px-3 py-2 font-mono text-[12.5px] leading-relaxed"
        />
      </label>
      <label className="block space-y-1 text-sm">
        <span className="text-ink-2">Grain (columns that identify a row, comma-separated)</span>
        <input value={draft.grain} onChange={(e) => setDraft({ ...draft, grain: e.target.value })} className={`${input} font-mono`} placeholder="order_id, line_number" />
      </label>
      <details className="text-sm">
        <summary className="cursor-pointer text-accent">Columns and tests (JSON)</summary>
        <p className="mt-2 text-xs text-ink-3">
          Tests: unique, not_null, accepted_values (with values), relationships (with to and field). Add &quot;severity&quot;: &quot;warn&quot; to report without failing.
        </p>
        <textarea
          value={draft.columns}
          onChange={(e) => setDraft({ ...draft, columns: e.target.value })}
          rows={12}
          spellCheck={false}
          className="mt-2 w-full rounded-lg border border-line bg-code px-3 py-2 font-mono text-[12px]"
        />
      </details>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" loading={busy === "save-build"} disabled={busy != null} onClick={() => onSave(true)}>
          <Hammer className="size-4" aria-hidden /> Save and build
        </Button>
        <Button loading={busy === "save"} disabled={busy != null} onClick={() => onSave(false)}>
          Save
        </Button>
        <Button variant="danger" disabled={busy != null} onClick={onDelete} className="ml-auto">
          <Trash2 className="size-4" aria-hidden /> Remove node
        </Button>
      </div>
    </div>
  );
}

export default function DataModelPage() {
  const { ws, id } = useParams<{ ws: string; id: string }>();
  const router = useRouter();
  const editable = canEdit(useWorkspace(ws));
  const [model, setModel] = useState<DataModelDetail | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [publish, setPublish] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function show(m: DataModelDetail, keep?: string | null) {
    setModel(m);
    const name = keep && m.nodes.some((n) => n.name === keep) ? keep : (m.nodes.find((n) => n.layer === "fact") ?? m.nodes[0])?.name ?? null;
    setSelected(name);
    const node = m.nodes.find((n) => n.name === name);
    setDraft(node ? toDraft(node) : null);
  }

  useEffect(() => {
    api.get<DataModelDetail>(`/workspaces/${ws}/data-models/${id}`).then((m) => show(m), (e) => setError(e.message));
  }, [ws, id]);

  async function act(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  }

  function select(name: string) {
    setSelected(name);
    const node = model?.nodes.find((n) => n.name === name);
    setDraft(node ? toDraft(node) : null);
  }

  async function save(nodes: NodeSpec[], build: boolean, keep: string | null) {
    if (!model) return;
    let next = await api.put<DataModelDetail>(`/workspaces/${ws}/data-models/${id}`, { name: model.name, description: model.description, nodes });
    if (build) next = await api.post<DataModelDetail>(`/workspaces/${ws}/data-models/${id}/build`);
    show(next, keep);
  }

  if (!model) return error ? <ErrorBanner message={error} /> : <Spinner />;
  const build = model.last_build;
  const built = build?.nodes.find((n) => n.name === selected);
  const spec = model.nodes.find((n) => n.name === selected);
  const publishable = (build?.nodes ?? []).filter((n) => n.status === "ok" && n.layer !== "staging");

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="space-y-2">
        <Link href={`/w/${ws}/data-models`} className="inline-flex items-center gap-1 text-sm text-ink-3 hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Data models
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">{model.name}</h1>
            <p className="tabular text-sm text-ink-3">
              {model.nodes.length} nodes
              {model.built_at && ` · built ${timeAgo(model.built_at)}`}
              {model.run_id && (
                <>
                  {" · "}
                  <Link href={`/w/${ws}/runs/${model.run_id}`} className="text-accent hover:underline">
                    designed in a run
                  </Link>
                </>
              )}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {editable && (
              <Button
                size="sm"
                variant="primary"
                loading={busy === "build"}
                disabled={busy != null}
                onClick={() => void act("build", async () => show(await api.post<DataModelDetail>(`/workspaces/${ws}/data-models/${id}/build`), selected))}
              >
                <Hammer className="size-4" aria-hidden /> Build and test
              </Button>
            )}
            <a
              href={`/api/workspaces/${ws}/data-models/${id}/dbt`}
              download
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 text-[13px] font-medium hover:bg-surface-2"
            >
              <Download className="size-4" aria-hidden /> Export dbt project
            </a>
            {editable && (
              <Button
                size="sm"
                variant="danger"
                disabled={busy != null}
                onClick={() =>
                  confirm("Delete this data model? Published datasets stay as static datasets.") &&
                  void act("delete", async () => {
                    await api.delete(`/workspaces/${ws}/data-models/${id}`);
                    router.push(`/w/${ws}/data-models`);
                  })
                }
              >
                <Trash2 className="size-4" aria-hidden /> Delete
              </Button>
            )}
          </div>
        </div>
        {model.description && <p className="max-w-3xl text-ink-2">{model.description}</p>}
      </div>
      {error && <ErrorBanner message={error} />}
      {model.stale && (
        <p className="rounded-lg border border-line bg-warn-soft px-3 py-2 text-sm text-warn" role="status">
          {build ? "There are changes since the last build. Build to test them." : "This model has not been built yet."}
        </p>
      )}
      {build && <BuildStats summary={build.summary} />}

      <section>
        <SectionTitle
          action={
            editable && (
              <Button
                size="sm"
                variant="ghost"
                disabled={busy != null}
                onClick={() => {
                  const base = model.nodes.find((n) => n.layer === "fact") ?? model.nodes[0];
                  let name = "new_mart";
                  for (let i = 2; model.nodes.some((n) => n.name === name); i++) name = `new_mart_${i}`;
                  const node: NodeSpec = {
                    name,
                    layer: "mart",
                    description: "",
                    sql: base ? `SELECT *\nFROM {{ ref('${base.name}') }}` : "SELECT 1 AS one",
                    materialized: "table",
                    grain: [],
                    columns: [],
                  };
                  void act("add", () => save([...model.nodes, node], false, name));
                }}
              >
                <Plus className="size-4" aria-hidden /> Add node
              </Button>
            )
          }
        >
          Lineage
        </SectionTitle>
        <LineageGraph build={model.stale ? null : build} nodes={graphNodes(model.stale ? null : build, model.nodes)} selected={selected} onSelect={select} />
      </section>

      {spec && (
        <section className="space-y-4">
          <SectionTitle>Node</SectionTitle>
          <Card className="space-y-4 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-mono text-base font-semibold">{spec.name}</h2>
              <Badge tone="accent">{spec.layer}</Badge>
              {built && built.status !== "ok" && <Badge tone="bad">{built.status === "error" ? "build failed" : "skipped"}</Badge>}
              {built?.row_count != null && <span className="tabular text-sm text-ink-3">{formatNumber(built.row_count)} rows</span>}
            </div>
            {editable && draft ? (
              <NodeEditor
                draft={draft}
                setDraft={setDraft}
                busy={busy}
                onSave={(andBuild) =>
                  void act(andBuild ? "save-build" : "save", async () => {
                    const node = fromDraft(draft);
                    await save(
                      model.nodes.map((n) => (n.name === spec.name ? node : n)),
                      andBuild,
                      node.name,
                    );
                  })
                }
                onDelete={() =>
                  confirm(`Remove ${spec.name}?`) &&
                  void act("remove", () =>
                    save(
                      model.nodes.filter((n) => n.name !== spec.name),
                      false,
                      null,
                    ),
                  )
                }
              />
            ) : (
              <>
                {spec.description && <p className="text-sm text-ink-2">{spec.description}</p>}
                <CodeBlock>{spec.sql}</CodeBlock>
              </>
            )}
          </Card>
          {built && !model.stale && (
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="min-w-0 space-y-2">
                <h3 className="text-sm font-medium">Output</h3>
                <NodeOutput node={built} />
              </div>
              <div className="min-w-0 space-y-2">
                <h3 className="text-sm font-medium">Tests on this node</h3>
                <TestResults tests={build?.tests ?? []} node={spec.name} />
              </div>
            </div>
          )}
        </section>
      )}

      {build && (
        <section>
          <SectionTitle>All tests</SectionTitle>
          <TestResults tests={build.tests} />
        </section>
      )}

      <section>
        <SectionTitle>Publish as datasets</SectionTitle>
        <Card className="space-y-4 p-5">
          <p className="text-sm text-ink-2">
            Publishing builds the model and saves the chosen tables as datasets, so agents, metrics, and models can use them. Refreshing a published dataset rebuilds it from the current sources.
          </p>
          {editable && publishable.length > 0 && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              {publishable.map((n) => (
                <label key={n.name} className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={publish.includes(n.name)}
                    onChange={(e) => setPublish(e.target.checked ? [...publish, n.name] : publish.filter((p) => p !== n.name))}
                    className="size-4 accent-[var(--accent)]"
                  />
                  <span className="font-mono text-[13px]">{n.name}</span>
                </label>
              ))}
              <Button
                size="sm"
                disabled={!publish.length || busy != null}
                loading={busy === "publish"}
                onClick={() =>
                  void act("publish", async () => {
                    await api.post<Dataset[]>(`/workspaces/${ws}/data-models/${id}/publish`, { nodes: publish });
                    setPublish([]);
                    show(await api.get<DataModelDetail>(`/workspaces/${ws}/data-models/${id}`), selected);
                  })
                }
              >
                <Upload className="size-4" aria-hidden /> Publish {publish.length || ""}
              </Button>
            </div>
          )}
          {model.published.length > 0 ? (
            <ul className="divide-y divide-line rounded-lg border border-line text-sm">
              {model.published.map((p) => (
                <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                  <Link href={`/w/${ws}/datasets/${p.id}`} className="font-mono text-[13px] text-accent hover:underline">
                    {p.table_name}
                  </Link>
                  <span className="tabular text-xs text-ink-3">
                    {formatNumber(p.row_count)} rows{p.synced_at && ` · published ${timeAgo(p.synced_at)}`}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-3">Nothing published yet.</p>
          )}
        </Card>
      </section>
    </div>
  );
}
