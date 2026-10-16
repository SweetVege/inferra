import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from inferra.api import (
    auth,
    connections,
    data_models,
    datasets,
    metrics,
    models,
    reports,
    runs,
    schedules,
    workspaces,
)
from inferra.config import get_settings
from inferra.db.models import Base
from inferra.db.session import get_engine
from inferra.runtime.queue import start_inline_worker


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.env != "production":
        # Production schema changes go through Alembic migrations.
        Base.metadata.create_all(get_engine())
    stop = start_inline_worker()
    yield
    if stop is not None:
        stop.set()


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    app = FastAPI(title="Inferra API", version="0.1.0", lifespan=lifespan)
    api = APIRouter(prefix="/api")

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    modules = (auth, workspaces, datasets, runs, models, metrics, connections, data_models)
    modules += (reports, schedules)
    for module in modules:
        api.include_router(module.router)
    api.include_router(reports.public_router)
    app.include_router(api)
    return app


app = create_app()
