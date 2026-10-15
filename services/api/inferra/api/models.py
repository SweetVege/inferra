import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra import storage
from inferra.api.datasets import _out as dataset_out
from inferra.api.datasets import ingest_file
from inferra.api.schemas import ORM, DatasetOut
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.datasources.base import QueryError
from inferra.datasources.registry import datasource_for
from inferra.db.models import Dataset, Model
from inferra.db.session import get_db
from inferra.tools.ml import predict

router = APIRouter(prefix="/workspaces/{workspace_id}/models", tags=["models"])


class ModelOut(ORM):
    id: str
    run_id: str | None
    name: str
    task: str
    target: str
    source_table: str
    features: list[str]
    metrics: dict[str, Any]
    created_at: datetime


class ModelDetail(ModelOut):
    card: dict[str, Any]


class ScoreRequest(BaseModel):
    dataset_id: str


def _get(db: Session, access: WorkspaceAccess, model_id: str) -> Model:
    model = db.get(Model, model_id)
    if model is None or model.workspace_id != access.workspace.id:
        raise HTTPException(404, "Model not found.")
    return model


@router.get("", response_model=list[ModelOut])
def list_models(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[Model]:
    return list(
        db.scalars(
            select(Model).where(Model.workspace_id == access.workspace.id).order_by(Model.created_at.desc())
        )
    )


@router.get("/{model_id}", response_model=ModelDetail)
def get_model(
    model_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> Model:
    return _get(db, access, model_id)


@router.post("/{model_id}/score", response_model=DatasetOut, status_code=201)
def score_dataset(
    model_id: str,
    body: ScoreRequest,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> DatasetOut:
    """Score every row of a dataset and save the result as a new dataset with a prediction column."""
    access.require("editor")
    model = _get(db, access, model_id)
    dataset = db.get(Dataset, body.dataset_id)
    if dataset is None or dataset.workspace_id != access.workspace.id:
        raise HTTPException(404, "Dataset not found.")
    available = {c["name"] for c in dataset.columns}
    missing = [f for f in model.features if f not in available]
    if missing:
        raise HTTPException(400, f"The dataset is missing model features: {', '.join(missing)}.")
    source = datasource_for(db, access.workspace.id, [dataset.id])
    try:
        frame = source.query_frame(f"SELECT * FROM {source.quote(dataset.table_name)}")
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    payload = storage.ensure_local(model.storage_key).read_text()
    column = f"{model.target}_probability" if model.task == "classification" else f"predicted_{model.target}"
    frame[column] = predict(payload, frame)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{dataset.table_name}_scored.parquet"
        frame.to_parquet(path, index=False)
        scored = ingest_file(
            db,
            access.workspace.id,
            path,
            path.name,
            access.user.id,
            description=f"{dataset.display_name} scored by model '{model.name}' (column {column}).",
        )
    return dataset_out(scored)


@router.delete("/{model_id}", status_code=204)
def delete_model(
    model_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> None:
    access.require("editor")
    model = _get(db, access, model_id)
    storage.delete_file(model.storage_key)
    db.delete(model)
    db.commit()
