from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra.datasources.base import DataSource, TableInfo
from inferra.datasources.files import FileSource
from inferra.db.models import Dataset


def table_info(dataset: Dataset) -> TableInfo:
    return TableInfo(
        name=dataset.table_name,
        columns=list(dataset.columns),
        row_count=dataset.row_count,
        description=dataset.description,
    )


def datasource_for(db: Session, workspace_id: str, dataset_ids: list[str] | None = None) -> DataSource:
    """Build the DataSource an agent run works against.

    Only file datasets exist today; warehouse connectors will plug in here by source_kind."""
    query = select(Dataset).where(Dataset.workspace_id == workspace_id)
    if dataset_ids:
        query = query.where(Dataset.id.in_(dataset_ids))
    datasets = db.scalars(query.order_by(Dataset.created_at)).all()
    return FileSource(workspace_id, [(table_info(d), d.storage_key) for d in datasets])
