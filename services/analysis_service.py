import asyncio
import logging

from sqlalchemy import select

from ai import build_ai
from database.models import AnalysisResult
from intelligence.base import Ctx
from intelligence.bug_intelligence import bug_report
from intelligence.context_recovery import recover_context
from intelligence.decision_analysis import decision_report
from intelligence.repository_intelligence import activity_report, architecture_report, repository_report
from intelligence.task_recovery import task_report

log = logging.getLogger(__name__)
KINDS = {"repository": repository_report, "architecture": architecture_report, "activity": activity_report,
         "bugs": bug_report, "decisions": decision_report, "tasks": task_report}


class AnalysisService:
    def __init__(self, config, db, provider=None):
        self.config, self.db = config, db
        self.router, self.executor = build_ai(config, db, provider)

    def run(self, project_id: int, kind: str, question: str | None = None, use_ai: bool = True) -> dict:
        if kind != "context_recovery" and kind not in KINDS:
            raise ValueError(f"unknown analysis kind {kind!r}")

        async def go(session):
            ctx = Ctx(session, project_id, self.executor, use_ai)
            if kind == "context_recovery":
                return await recover_context(ctx, question or "")
            return await KINDS[kind](ctx)

        with self.db.session() as s:
            rep = asyncio.run(go(s)).to_dict()
            if rep["ai"].get("used") or rep["verified_facts"]:
                s.add(AnalysisResult(project_id=project_id, kind=kind, question=(question or "")[:500], result=rep))
        return rep

    def latest(self, project_id: int, kind: str) -> dict | None:
        with self.db.session() as s:
            r = s.scalars(select(AnalysisResult).where(AnalysisResult.project_id == project_id, AnalysisResult.kind == kind)
                          .order_by(AnalysisResult.id.desc())).first()
            return r.result if r else None
