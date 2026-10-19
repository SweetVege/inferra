"""Persists everything an agent does: tool calls, artifacts, timeline events, and token usage."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from inferra import storage
from inferra.db.models import Artifact, DataModel, Model, Run, RunEvent, Step, ToolCall, utcnow
from inferra.llm.gateway import Usage
from inferra.tools.base import ArtifactDraft, ModelDraft, ProjectDraft


class Recorder:
    def __init__(self, db: Session, run: Run):
        self.db = db
        self.run = run

    def event(self, kind: str, message: str, **data: Any) -> None:
        self.db.add(RunEvent(run_id=self.run.id, kind=kind, message=message, data=data))
        self.db.commit()

    def usage(self, usage: Usage) -> None:
        total = Usage(**{k: int(v) for k, v in (self.run.usage or {}).items() if k in Usage().__dict__})
        total.add(usage)
        self.run.usage = total.as_dict()
        self.db.commit()

    def model(self, draft: ModelDraft) -> str:
        """Register a trained model: payload to object storage, metadata to the registry."""
        model = Model(
            workspace_id=self.run.workspace_id,
            run_id=self.run.id,
            name=draft.name,
            task=draft.task,
            target=draft.target,
            source_table=draft.table,
            features=draft.features,
            metrics=draft.metrics,
            card=draft.card,
            storage_key="",
            created_by=self.run.created_by,
        )
        self.db.add(model)
        self.db.flush()
        model.storage_key = f"workspaces/{self.run.workspace_id}/models/{model.id}.json"
        path = storage.local_path(model.storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft.payload)
        storage.put_file(model.storage_key, path)
        self.db.commit()
        return model.id

    def data_model(self, draft: ProjectDraft) -> str:
        """Save the run's data model as a draft. Rebuilding in the same run updates it in place."""
        model = self.db.scalars(
            select(DataModel).where(DataModel.run_id == self.run.id).order_by(DataModel.created_at)
        ).first()
        if model is None:
            model = DataModel(
                workspace_id=self.run.workspace_id, run_id=self.run.id, created_by=self.run.created_by
            )
            self.db.add(model)
        model.name, model.description, model.nodes = draft.name, draft.description, draft.nodes
        now = utcnow()  # one instant: a later updated_at would mark the fresh build as stale
        model.last_build, model.built_at, model.updated_at = draft.build, now, now
        self.db.commit()
        return model.id

    def artifact(self, step: Step | None, draft: ArtifactDraft) -> Artifact:
        art = Artifact(
            run_id=self.run.id,
            step_id=step.id if step else None,
            kind=draft.kind,
            title=draft.title,
            data=draft.data,
        )
        self.db.add(art)
        self.db.flush()
        return art

    def tool_call(
        self,
        step: Step,
        agent: str,
        tool: str,
        input: dict[str, Any],
        *,
        code: str | None,
        artifact_id: str | None,
        error: str | None,
        duration_ms: int,
    ) -> ToolCall:
        call = ToolCall(
            run_id=self.run.id,
            step_id=step.id,
            agent=agent,
            tool=tool,
            input=input,
            code=code,
            artifact_id=artifact_id,
            error=error,
            duration_ms=duration_ms,
        )
        self.db.add(call)
        self.db.commit()
        return call
