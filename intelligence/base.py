"""Shared structures for the Intelligence Engine.

Every report separates four kinds of statements:
  verified_facts  - computed from stored records (basis=computed) or cited by a model with evidence that
                    was checked against supplied records (basis=ai_cited)
  inferences      - AI reasoning beyond the data (basis=ai_inference) with confidence
  recommendations - suggested actions (computed or AI)
  unknown         - what the evidence cannot tell
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now() -> datetime:
    return datetime.now(timezone.utc)


def ev_of(r) -> dict:
    return dict(ref=f"R{r.id}", record_id=r.id, source=r.source, kind=r.kind, title=(r.title or "")[:120], url=r.url or "")


@dataclass
class Report:
    kind: str
    project_id: int
    summary: str = ""
    verified_facts: list = field(default_factory=list)
    inferences: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    unknown: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    ai: dict = field(default_factory=lambda: {"used": False, "roles_called": [], "roles_ok": [], "errors": {}, "synthesized": False})
    generated_at: str = field(default_factory=lambda: now().isoformat(timespec="seconds"))

    def fact(self, statement: str, recs=(), basis="computed"):
        self.verified_facts.append({"statement": statement, "evidence": [ev_of(r) for r in list(recs)[:5]], "basis": basis})

    def recommend(self, statement: str, recs=(), priority="medium", basis="computed"):
        self.recommendations.append({"statement": statement, "evidence": [ev_of(r) for r in list(recs)[:5]],
                                     "priority": priority, "basis": basis})

    def to_dict(self) -> dict:
        return dict(kind=self.kind, project_id=self.project_id, summary=self.summary, verified_facts=self.verified_facts,
                    inferences=self.inferences, recommendations=self.recommendations, unknown=self.unknown,
                    extra=self.extra, ai=self.ai, generated_at=self.generated_at)


@dataclass
class Ctx:
    session: Session
    project_id: int
    executor: object = None      # ParallelExecutor | None
    use_ai: bool = True


async def apply_ai(ctx: Ctx, report: Report, task: str, instruction: str, records: list,
                   question: str | None = None, facts_block: str = "") -> None:
    """Run the minimal set of models and merge their (validated) output into the report."""
    if not ctx.use_ai:
        report.unknown.append("AI analysis was skipped for this run.")
        return
    router = getattr(ctx.executor, "router", None)
    needed = router.plan(task).analysts if router else ()
    if ctx.executor is None or not any(router.is_ready(r) for r in needed):
        report.unknown.append("AI analysis unavailable: configure OPENROUTER_API_KEY and the model role variables "
                              "(REPO_ANALYST_MODEL / CONTEXT_ANALYST_MODEL / SYNTHESIZER_MODEL). Showing computed facts only.")
        return
    by_ref = {f"R{r.id}": r for r in records}

    def ev(refs):
        return [ev_of(by_ref[x]) for x in refs if x in by_ref]

    try:
        res = await ctx.executor.run(task, ctx.project_id, records, instruction, question, facts_block)
    except Exception as e:  # never let AI failure break a report
        log.exception("AI execution failed")
        report.ai["errors"] = {"executor": str(e)}
        report.unknown.append(f"AI analysis failed: {e}")
        return
    report.ai.update(used=res.ok, roles_called=res.roles_called, roles_ok=res.roles_ok, errors=res.errors,
                     synthesized=res.synthesized)
    if not res.ok:
        report.unknown.append("AI analysis returned no usable result: " + "; ".join(f"{k}: {v}" for k, v in res.errors.items()))
        return
    a = res.analysis
    if a.get("summary"):
        report.summary = (report.summary + " " if report.summary else "") + a["summary"]
    for f in a["verified_facts"]:
        report.verified_facts.append({"statement": f["statement"], "evidence": ev(f["evidence"]), "basis": "ai_cited",
                                      "by": f.get("by", [])})
    for i in a["inferences"]:
        report.inferences.append({"statement": i["statement"], "evidence": ev(i.get("evidence", [])),
                                  "confidence": i.get("confidence", 0.5), "basis": "ai_inference", "by": i.get("by", []),
                                  **({"note": i["note"]} if i.get("note") else {})})
    for r in a["recommendations"]:
        report.recommendations.append({"statement": r["statement"], "evidence": ev(r.get("evidence", [])),
                                       "priority": r.get("priority", "medium"), "basis": "ai", "by": r.get("by", [])})
    report.unknown.extend(a["unknown"])
