import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra import storage
from inferra.api.schemas import DatasetDetail, DatasetOut, DatasetUpdate
from inferra.auth.session import WorkspaceAccess, workspace_access
from inferra.config import get_settings
from inferra.datasources.base import QueryError
from inferra.datasources.files import SUPPORTED_EXTENSIONS, convert_to_parquet, to_table_name
from inferra.datasources.registry import datasource_for
from inferra.db.models import Dataset, new_id
from inferra.db.session import get_db
from inferra.tools.base import ToolError
from inferra.tools.profiling import profile_table

router = APIRouter(prefix="/workspaces/{workspace_id}/datasets", tags=["datasets"])


def _out(d: Dataset) -> DatasetOut:
    out = DatasetOut.model_validate(d)
    out.has_profile = d.profile is not None
    return out


def _get(db: Session, access: WorkspaceAccess, dataset_id: str) -> Dataset:
    d = db.get(Dataset, dataset_id)
    if d is None or d.workspace_id != access.workspace.id:
        raise HTTPException(404, "Dataset not found.")
    return d


def _unique_table_name(db: Session, workspace_id: str, base: str) -> str:
    taken = set(db.scalars(select(Dataset.table_name).where(Dataset.workspace_id == workspace_id)))
    name, i = base, 2
    while name in taken:
        name, i = f"{base}_{i}", i + 1
    return name


def ingest_file(
    db: Session,
    workspace_id: str,
    upload_path: Path,
    filename: str,
    user_id: str | None,
    description: str | None = None,
) -> Dataset:
    """Convert a file to Parquet, store it, and register the dataset (shared with the seed)."""
    dataset_id = new_id()
    key = f"workspaces/{workspace_id}/{dataset_id}.parquet"
    dest = storage.local_path(key)
    row_count, columns = convert_to_parquet(upload_path, dest)
    storage.put_file(key, dest)
    table = _unique_table_name(db, workspace_id, to_table_name(filename))
    dataset = Dataset(
        id=dataset_id,
        workspace_id=workspace_id,
        table_name=table,
        display_name=Path(filename).stem.replace("_", " ").strip() or table,
        description=description,
        original_filename=filename,
        storage_key=key,
        row_count=row_count,
        columns=columns,
        created_by=user_id,
    )
    db.add(dataset)
    db.commit()
    return dataset


@router.get("", response_model=list[DatasetOut])
def list_datasets(
    access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> list[DatasetOut]:
    rows = db.scalars(
        select(Dataset).where(Dataset.workspace_id == access.workspace.id).order_by(Dataset.created_at.desc())
    )
    return [_out(d) for d in rows]


@router.post("", response_model=DatasetOut, status_code=201)
def upload_dataset(
    file: UploadFile = File(...),
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> DatasetOut:
    access.require("editor")
    filename = Path(file.filename or "upload.csv").name
    if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, "Upload a CSV, TSV, Parquet, or Excel file.")
    limit = get_settings().max_upload_mb * 1024 * 1024
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / filename
        with path.open("wb") as fh:
            shutil.copyfileobj(file.file, fh, length=1024 * 1024)
        if path.stat().st_size > limit:
            raise HTTPException(413, f"Files are limited to {get_settings().max_upload_mb} MB.")
        try:
            dataset = ingest_file(db, access.workspace.id, path, filename, access.user.id)
        except QueryError as exc:
            raise HTTPException(400, str(exc)) from exc
    return _out(dataset)


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(
    dataset_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> DatasetDetail:
    d = _get(db, access, dataset_id)
    source = datasource_for(db, access.workspace.id, [d.id])
    preview = source.query(f"SELECT * FROM {source.quote(d.table_name)}", limit=20).rows
    return DatasetDetail(**_out(d).model_dump(), preview=preview, profile=d.profile)


@router.post("/{dataset_id}/profile", response_model=DatasetDetail)
def build_profile(
    dataset_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> DatasetDetail:
    d = _get(db, access, dataset_id)
    if d.profile is None:
        access.require("editor")
        try:
            d.profile = profile_table(datasource_for(db, access.workspace.id, [d.id]), d.table_name)
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc
        db.commit()
    return get_dataset(dataset_id, access, db)


@router.patch("/{dataset_id}", response_model=DatasetOut)
def update_dataset(
    dataset_id: str,
    body: DatasetUpdate,
    access: WorkspaceAccess = Depends(workspace_access),
    db: Session = Depends(get_db),
) -> DatasetOut:
    access.require("editor")
    d = _get(db, access, dataset_id)
    if body.display_name is not None:
        d.display_name = body.display_name
    if body.description is not None:
        d.description = body.description or None
    db.commit()
    return _out(d)


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str, access: WorkspaceAccess = Depends(workspace_access), db: Session = Depends(get_db)
) -> None:
    access.require("editor")
    d = _get(db, access, dataset_id)
    storage.delete_file(d.storage_key)
    db.delete(d)
    db.commit()
