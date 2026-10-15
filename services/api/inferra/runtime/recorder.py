"""Persists everything an agent does: tool calls, artifacts, timeline events, and token usage."""

from typing import Any

from sqlalchemy.orm import Session

from inferra.db.models import Artifact, Run, RunEvent, Step, ToolCall
from inferra.llm.gateway import Usage
from inferra.tools.base import ArtifactDraft


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
