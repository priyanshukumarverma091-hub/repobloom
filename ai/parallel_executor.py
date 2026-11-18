"""Runs the minimal set of models for a task: analysts in parallel (asyncio.gather), the synthesizer
only when it adds value (>=2 successful analyst outputs). Partial failures degrade gracefully."""
import asyncio
import logging
from dataclasses import dataclass, field

from ai.base_analyst import AnalystOutput
from ai.context_analyst import ContextAnalyst
from ai.intelligence_synthesizer import IntelligenceSynthesizer
from ai.model_router import CTX, REPO, SYN, ModelRouter
from ai.repository_analyst import RepositoryAnalyst
from ai.result_merger import merge_analyses

log = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    analysis: dict | None = None
    roles_called: list = field(default_factory=list)
    roles_ok: list = field(default_factory=list)
    errors: dict = field(default_factory=dict)
    synthesized: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.analysis)


class ParallelExecutor:
    def __init__(self, router: ModelRouter):
        self.router = router
        self.analysts = {REPO: RepositoryAnalyst(), CTX: ContextAnalyst()}
        self.synth = IntelligenceSynthesizer()

    async def run(self, task: str, project_id: int, records, instruction: str, question: str | None = None,
                  facts_block: str = "") -> ExecutionResult:
        plan = self.router.plan(task)
        out = ExecutionResult()
        roles = []
        for r in plan.analysts:
            if not self.router.is_ready(r):
                out.errors[r] = "model not configured"
            elif not self.analysts[r].select(records):
                out.errors[r] = "no relevant records"
            else:
                roles.append(r)
        if not roles:
            return out
        out.roles_called = list(roles)
        results = await asyncio.gather(
            *[self.analysts[r].run(self.router, task, project_id, records, instruction, question, facts_block)
              for r in roles], return_exceptions=True)
        good: list[AnalystOutput] = []
        for r, res in zip(roles, results):
            if isinstance(res, Exception):
                out.errors[r] = f"{type(res).__name__}: {res}"
            elif res.ok:
                good.append(res)
                out.roles_ok.append(r)
            else:
                out.errors[r] = res.error
        if not good:
            return out
        if plan.synthesize and len(good) >= 2 and self.router.is_ready(SYN):
            out.roles_called.append(SYN)
            syn = await self.synth.run(self.router, task, project_id, good, records, instruction, question)
            if syn.ok:
                out.analysis, out.synthesized = syn.analysis, True
                out.roles_ok.append(SYN)
                return out
            out.errors[SYN] = syn.error
        out.analysis = merge_analyses([(g.role, g.analysis) for g in good])   # fallback / single analyst
        return out
