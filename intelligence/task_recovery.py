from datetime import timedelta

from sqlalchemy import select

from database.models import Record
from intelligence.base import Ctx, Report, apply_ai, aware, now
from knowledge_graph.relationship_mapper import resolution_chains


async def task_report(ctx: Ctx) -> Report:
    rep = Report("tasks", ctx.project_id)
    recs = list(ctx.session.scalars(select(Record).where(Record.project_id == ctx.project_id)
                                    .order_by(Record.timestamp.desc().nullslast()).limit(800)).all())
    tasks = []
    for r in recs:
        if r.kind == "issue" and r.meta.get("state") == "open":
            age = (now() - aware(r.timestamp)).days if r.timestamp else None
            pr = 3 if r.category == "bug" else 2 if r.category == "feature" else 1
            tasks.append(dict(type="issue", number=r.meta.get("number"), title=r.title, category=r.category, age_days=age,
                              url=r.url, ref=f"R{r.id}", score=pr * 10 + min(age or 0, 30) / 3))
        elif r.kind == "pr" and r.meta.get("state") == "open":
            tasks.append(dict(type="pr", number=r.meta.get("number"), title=r.title, category="review", age_days=None,
                              url=r.url, ref=f"R{r.id}", score=25))
        for t in r.meta.get("todos", [])[:3]:
            tasks.append(dict(type="todo", number=None, title=t[:100], category="task", age_days=None, url=r.url,
                              ref=f"R{r.id}", score=12))
    unresolved = [c for c in resolution_chains(ctx.session, ctx.project_id) if c["status"] in ("open", "fix_proposed")]
    tasks.sort(key=lambda t: -t["score"])
    by_id = {f"R{r.id}": r for r in recs}
    n_issue = sum(1 for t in tasks if t["type"] == "issue")
    n_pr = sum(1 for t in tasks if t["type"] == "pr")
    n_todo = sum(1 for t in tasks if t["type"] == "todo")
    if not tasks:
        rep.unknown.append("No open issues, open PRs or TODO markers found in collected data.")
    else:
        rep.fact(f"{n_issue} open issue(s), {n_pr} open PR(s), {n_todo} TODO/FIXME marker(s) in collected text.",
                 [by_id[t["ref"]] for t in tasks[:3]])
    for t in tasks[:5]:
        label = {"issue": f"issue #{t['number']}", "pr": f"PR #{t['number']}", "todo": "TODO"}[t["type"]]
        rep.recommend(f"Next up: {label} '{t['title'][:80]}'" + (f" (open {t['age_days']}d)" if t["age_days"] else ""),
                      [by_id[t["ref"]]], "high" if t["category"] == "bug" else "medium")
    for c in unresolved[:5]:
        rep.fact(f"Bug #{c['issue'].meta.get('number')} is {c['status'].replace('_', ' ')}.", [c["issue"]])
    rep.extra = {"tasks": tasks[:60]}
    pool = [by_id[t["ref"]] for t in tasks[:40]] + [c["issue"] for c in unresolved]
    await apply_ai(ctx, rep, "tasks",
                   "Recover incomplete work: what is unfinished, why it stalled, and what developers should do next, in priority order.",
                   list({r.id: r for r in pool}.values()) or recs[:40],
                   facts_block="\n".join(f"- {f['statement']}" for f in rep.verified_facts))
    rep.summary = rep.summary or f"{len(tasks)} open work item(s)."
    return rep
