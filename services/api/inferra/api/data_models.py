"""Data models: review and edit drafts, build and test them, publish nodes as datasets, and export
to dbt. Builds run synchronously; they are DuckDB queries over workspace Parquet files."""

import re
from datetime import UTC, datetime
from typing import Any

import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra import storage
from inferra.api.datasets import _out as dataset_out
from inferra.api.datasets import _unique_table_name
from inferra.api.schemas import ORM, DatasetOut
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.datasources.base import QueryError
from inferra.datasources.files import FileSource
from inferra.datasources.registry import table_info
from inferra.db.models import DataModel, Dataset, new_id, utcnow
from inferra.db.session import get_db
from inferra.transform.build import build_project
from inferra.transform.dbt import export_project, slug
from inferra.transform.design import analyze_table, low_cardinality_values, suggest_star_schema
from inferra.transform.project import NodeSpec, ProjectError, compile_project, parse_nodes

router = APIRouter(prefix="/workspaces/{workspace_id}/data-models", tags=["data models"])


class DataModelSummary(ORM):
    id: str
    name: str
    description: str
    run_id: str | None
    created_at: datetime
    updated_at: datetime
    built_at: datetime | None
    node_count: int = 0
    summary: dict[str, Any] | None = None
    stale: bool = False


class PublishedDataset(BaseModel):
    id: str
    table_name: str
    node: str
    row_count: int
    synced_at: datetime | None


class DataModelDetail(DataModelSummary):
    nodes: list[dict[str, Any]]
    last_build: dict[str, Any] | None
    published: list[PublishedDataset]
    sources: list[str]


class DataModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    nodes: list[dict[str, Any]] = Field(default_factory=list, max_length=40)


class SuggestIn(BaseModel):
    dataset_id: str
    name: str | None = Field(default=None, max_length=200)


class PublishIn(BaseModel):
    nodes: list[str] = Field(min_length=1, max_length=40)


def _get(db: Session, access: WorkspaceAccess, model_id: str) -> DataModel:
    model = db.get(DataModel, model_id)
    if model is None or model.workspace_id != access.workspace.id:
        raise HTTPException(404, "Data model not found.")
    return model


def _source(db: Session, workspace_id: str, model_id: str | None) -> FileSource:
    """Every dataset in the workspace except the ones this model publishes (whose names are the
    model's own node names)."""
    datasets = db.scalars(
        select(Dataset).where(Dataset.workspace_id == workspace_id).order_by(Dataset.created_at)
    ).all()
    usable = [d for d in datasets if model_id is None or d.data_model_id != model_id]
    return FileSource(workspace_id, [(table_info(d), d.storage_key) for d in usable])


def _nodes(raw: list[dict[str, Any]]) -> list[NodeSpec]:
    try:
        return parse_nodes(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise HTTPException(400, f"Invalid node ({where}): {first['msg']}") from exc


def _published(db: Session, model: DataModel) -> list[Dataset]:
    return list(db.scalars(select(Dataset).where(Dataset.data_model_id == model.id)))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)  # SQLite drops the offset


def _summary(model: DataModel) -> DataModelSummary:
    out = DataModelSummary.model_validate(model)
    out.node_count = len(model.nodes)
    out.summary = (model.last_build or {}).get("summary")
    out.stale = model.built_at is None or (_aware(model.updated_at) > _aware(model.built_at))
    return out


def _detail(db: Session, model: DataModel) -> DataModelDetail:
    source = _source(db, model.workspace_id, model.id)
    return DataModelDetail(
        **_summary(model).model_dump(),
        nodes=model.nodes,
        last_build=model.last_build,
        published=[
            PublishedDataset(
                id=d.id,
                table_name=d.table_name,
                node=d.source_query or "",
                row_count=d.row_count,
                synced_at=d.synced_at,
            )
            for d in _published(db, model)
        ],
        sources=[t.name for t in source.list_tables()],
    )


def _build(db: Session, model: DataModel, keep: set[str] | None = None) -> Any:
    try:
        built = build_project(
            _source(db, model.workspace_id, model.id),
            _nodes(model.nodes),
            name=model.name,
            description=model.description,
            keep=keep,
        )
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc
    model.last_build, model.built_at = built.result, utcnow()
    db.commit()
    return built


@router.get("", response_model=list[DataModelSummary])
def list_data_models(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[DataModelSummary]:
    rows = db.scalars(
        select(DataModel)
        .where(DataModel.workspace_id == access.workspace.id)
        .order_by(DataModel.updated_at.desc())
    )
    return [_summary(m) for m in rows]


@router.post("", response_model=DataModelDetail, status_code=201)
def create_data_model(
    body: DataModelIn, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> DataModelDetail:
    access.require("editor")
    nodes = _nodes(body.nodes)
    try:
        compile_project(nodes, {t.name for t in _source(db, access.workspace.id, None).list_tables()})
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc
    model = DataModel(
        workspace_id=access.workspace.id,
        name=body.name,
        description=body.description,
        nodes=[n.model_dump() for n in nodes],
        created_by=access.user.id,
    )
    db.add(model)
    db.commit()
    return _detail(db, model)


@router.post("/suggest", response_model=DataModelDetail, status_code=201)
def suggest_data_model(
    body: SuggestIn, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> DataModelDetail:
    """Draft and build a star schema for one dataset without a language model."""
    access.require("editor")
    dataset = db.get(Dataset, body.dataset_id)
    if dataset is None or dataset.workspace_id != access.workspace.id:
        raise HTTPException(404, "Dataset not found.")
    source = FileSource(access.workspace.id, [(table_info(dataset), dataset.storage_key)])
    try:
        analysis = analyze_table(source, dataset.table_name)
        nodes = suggest_star_schema(analysis, low_cardinality_values(source, analysis))
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    model = DataModel(
        workspace_id=access.workspace.id,
        name=body.name or f"{dataset.display_name[:1].upper()}{dataset.display_name[1:]} star schema",
        description=f"Star schema drafted from {dataset.table_name} "
        f"(grain: {', '.join(analysis['grain']) or 'none found'}).",
        nodes=nodes,
        created_by=access.user.id,
    )
    db.add(model)
    db.commit()
    _build(db, model)
    return _detail(db, model)


@router.get("/{model_id}", response_model=DataModelDetail)
def get_data_model(
    model_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> DataModelDetail:
    return _detail(db, _get(db, access, model_id))


@router.put("/{model_id}", response_model=DataModelDetail)
def update_data_model(
    model_id: str,
    body: DataModelIn,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> DataModelDetail:
    """Save edits after checking that the project still compiles. Run a build to test them."""
    access.require("editor")
    model = _get(db, access, model_id)
    nodes = _nodes(body.nodes)
    try:
        compile_project(nodes, {t.name for t in _source(db, model.workspace_id, model.id).list_tables()})
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc
    model.name, model.description = body.name, body.description
    model.nodes = [n.model_dump() for n in nodes]
    model.updated_at = utcnow()
    db.commit()
    return _detail(db, model)


@router.post("/{model_id}/build", response_model=DataModelDetail)
def build_data_model(
    model_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> DataModelDetail:
    access.require("editor")
    model = _get(db, access, model_id)
    _build(db, model)
    return _detail(db, model)


def publish_nodes(db: Session, model: DataModel, names: list[str], user_id: str | None) -> list[Dataset]:
    """Build, then write the chosen nodes to Parquet as datasets (replacing earlier publishes)."""
    known = {n["name"] for n in model.nodes}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise HTTPException(400, f"Unknown nodes: {', '.join(unknown)}.")
    built = _build(db, model, keep=set(names))
    failed = [n for n in names if n not in built.tables]
    if failed:
        raise HTTPException(400, f"These nodes did not build: {', '.join(failed)}. Fix them and try again.")
    existing = {d.source_query: d for d in _published(db, model)}
    nodes = {n["name"]: n for n in built.result["nodes"]}
    out = []
    for name in names:
        dataset = existing.get(name)
        if dataset is None:
            dataset_id = new_id()
            dataset = Dataset(
                id=dataset_id,
                workspace_id=model.workspace_id,
                source_kind="model",
                table_name=_unique_table_name(db, model.workspace_id, name),
                display_name=name.replace("_", " "),
                storage_key=f"workspaces/{model.workspace_id}/{dataset_id}.parquet",
                created_by=user_id,
                data_model_id=model.id,
                source_query=name,
            )
            db.add(dataset)
        dataset.description = f"{nodes[name]['layer'].capitalize()} from data model '{model.name}'. " + (
            nodes[name]["description"] or ""
        )
        path = storage.local_path(dataset.storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(built.tables[name], path)
        storage.put_file(dataset.storage_key, path)
        dataset.row_count = nodes[name]["row_count"]
        dataset.columns = [{"name": c["name"], "type": c["type"]} for c in nodes[name]["columns"]]
        dataset.profile = None
        dataset.synced_at = utcnow()
        out.append(dataset)
    db.commit()
    return out


@router.post("/{model_id}/publish", response_model=list[DatasetOut])
def publish(
    model_id: str,
    body: PublishIn,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> list[DatasetOut]:
    access.require("editor")
    model = _get(db, access, model_id)
    return [dataset_out(d) for d in publish_nodes(db, model, list(dict.fromkeys(body.nodes)), access.user.id)]


@router.get("/{model_id}/dbt")
def export_dbt(
    model_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> Response:
    """A zipped dbt-duckdb project with the model's SQL, tests, docs, and source data."""
    model = _get(db, access, model_id)
    nodes = _nodes(model.nodes)
    datasets = {
        d.table_name: d
        for d in db.scalars(select(Dataset).where(Dataset.workspace_id == model.workspace_id))
        if d.data_model_id != model.id
    }
    try:
        project = compile_project(nodes, set(datasets))
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc
    sources = [
        {"name": s, "description": datasets[s].description or datasets[s].display_name}
        for s in project.sources
    ]
    files = {s: storage.ensure_local(datasets[s].storage_key) for s in project.sources}
    content = export_project(model.name, model.description, project.nodes, sources, files)
    filename = re.sub(r"[^a-z0-9_]+", "_", slug(model.name)) + "_dbt.zip"
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{model_id}", status_code=204)
def delete_data_model(
    model_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> None:
    """Published datasets stay as static datasets."""
    access.require("editor")
    db.delete(_get(db, access, model_id))
    db.commit()
