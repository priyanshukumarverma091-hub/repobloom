from sqlalchemy import select

from database.models import Record, Relationship
from intelligence.base import Ctx, Report, apply_ai, aware
from memory.project_memory import ProjectMemory, memory_to_dict
from services.repository_service import search_records


async def recover_context(ctx: Ctx, question: str) -> Report:
    rep = Report("context_recovery", ctx.project_id)
    question = (question or "").strip()[:500]
    if len(question) < 3:
        rep.unknown.append("Ask a question, e.g. 'Why did we switch the payment retry logic?'")
        return rep
    hits = search_records(ctx.session, ctx.project_id, question, k=12)
    if not hits:
        rep.unknown.append("Nothing in the collected data matches this question. Sync more sources or rephrase.")
        return rep
    base = [r for _, r in hits]
    ids = {r.id for r in base}
    # 1-hop expansion through the knowledge graph: pull in linked issues, commits, discussions
    keys = [f"record:{i}" for i in ids]
    rels = ctx.session.scalars(select(Relationship).where(Relationship.project_id == ctx.project_id,
                                                          (Relationship.src_key.in_(keys)) | (Relationship.dst_key.in_(keys)),
                                                          Relationship.origin == "verified")).all()
    extra_ids = set()
    for e in rels:
        for k in (e.src_key, e.dst_key):
            if k.startswith("record:") and int(k.split(":")[1]) not in ids:
                extra_ids.add(int(k.split(":")[1]))
    linked = list(ctx.session.scalars(select(Record).where(Record.id.in_(list(extra_ids)[:25]))).all()) if extra_ids else []
    pool = base + linked
    pool_sorted = sorted(pool, key=lambda r: aware(r.timestamp).timestamp() if r.timestamp else float('inf'))
    rep.fact(f"{len(base)} record(s) match the question; {len(linked)} more are linked to them through explicit references.", base[:3])
    for r in base[:5]:
        when = f"{r.timestamp:%Y-%m-%d}" if r.timestamp else "undated"
        rep.fact(f"{when} - {r.source} {r.kind} by {r.author or 'unknown'}: {r.title[:110]}", [r])
    mems = ProjectMemory(ctx.session, ctx.project_id).search(question, k=5)
    rep.extra = {"timeline": [dict(ref=f"R{r.id}", date=r.timestamp.isoformat() if r.timestamp else None, source=r.source,
                                   kind=r.kind, title=r.title[:140], author=r.author, url=r.url, category=r.category,
                                   direct=r.id in ids) for r in pool_sorted],
                 "memories": [memory_to_dict(m) for _, m in mems]}
    await apply_ai(ctx, rep, "context_recovery",
                   "Recover the lost context behind the user's question: what happened, why, which problems and decisions were involved, "
                   "who did what, what is unresolved. Build a chronological explanation from the evidence.",
                   pool, question=question)
    if not rep.ai["used"]:
        rep.unknown.append("The timeline above is retrieved evidence, not an explanation; enable AI for a synthesized narrative.")
    rep.summary = rep.summary or f"Found {len(pool)} related record(s) for: {question[:80]}"
    return rep
