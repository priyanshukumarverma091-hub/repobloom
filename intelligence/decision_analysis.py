from sqlalchemy import select

from database.models import Record
from intelligence.base import Ctx, Report, apply_ai
from memory.decision_memory import STRONG


async def decision_report(ctx: Ctx) -> Report:
    rep = Report("decisions", ctx.project_id)
    cands = list(ctx.session.scalars(select(Record).where(
        Record.project_id == ctx.project_id, Record.category == "decision", Record.source != "docs")
        .order_by(Record.timestamp.desc().nullslast()).limit(80)).all())
    if not cands:
        rep.unknown.append("No decision-like statements found in issues, PRs or Discord yet.")
        return rep
    rows = []
    for r in cands:
        strong = bool(STRONG.search(f"{r.title} {r.content}"))
        rows.append(dict(ref=f"R{r.id}", title=r.title[:120], excerpt=r.content[:300], source=r.source, author=r.author, url=r.url,
                         timestamp=r.timestamp.isoformat() if r.timestamp else None, explicit=strong,
                         issues=r.meta.get("issue_refs", [])))
        if strong:
            rep.fact(f"Explicit decision wording by {r.author or 'unknown'} in {r.source} {r.kind}: '{r.title[:100]}'.", [r])
    weak = len(cands) - sum(1 for x in rows if x["explicit"])
    if weak:
        rep.unknown.append(f"{weak} further statement(s) look decision-like (keyword match) but lack explicit decision wording; review manually.")
    rep.extra = {"decisions": rows}
    await apply_ai(ctx, rep, "decisions",
                   "Extract technical decisions: what was decided, the rationale, alternatives considered, who participated, and what it affected.",
                   cands, facts_block=f"{len(cands)} decision-like records")
    rep.summary = rep.summary or f"{len(cands)} decision-like statements ({len(cands) - weak} explicit)."
    return rep
