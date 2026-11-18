from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from database.models import Record
from intelligence.base import Ctx, Report, apply_ai, aware, now
from knowledge_graph.relationship_mapper import resolution_chains


async def bug_report(ctx: Ctx) -> Report:
    rep = Report("bugs", ctx.project_id)
    chains = resolution_chains(ctx.session, ctx.project_id)
    if not chains:
        rep.unknown.append("No bug-like issues found. Sync issues, or label them 'bug'.")
        return rep
    st = Counter(c["status"] for c in chains)
    rep.fact(f"{len(chains)} bug-like issue(s): " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in st.most_common()) + ".",
             [c["issue"] for c in chains[:3]])
    table, ctx_records = [], []
    for c in chains:
        i = c["issue"]
        ctx_records += [i] + c["discussions"] + c["decisions"] + c["resolvers"]
        table.append(dict(number=i.meta.get("number"), title=i.title, status=c["status"], url=i.url, ref=f"R{i.id}",
                          resolved_by=[dict(ref=f"R{r.id}", title=r.title, url=r.url, kind=r.kind) for r in c["resolvers"]],
                          discussions=len(c["discussions"]), decisions=len(c["decisions"]), files=c["files"][:6]))
        if c["status"] == "resolved":
            rep.fact(f"Bug #{i.meta.get('number')} '{i.title[:70]}' was resolved by " +
                     ", ".join(f"{r.kind} {r.external_id[:8]}" for r in c["resolvers"][:3]) +
                     (f" (changed: {', '.join(c['files'][:4])})" if c["files"] else "") + ".", [i] + c["resolvers"][:2])
        elif c["status"] == "closed_without_fix_link":
            rep.unknown.append(f"Bug #{i.meta.get('number')} is closed but no commit/PR is linked to it; the fix is not recorded.")
        elif c["status"] == "open":
            old = i.timestamp and now() - aware(i.timestamp) > timedelta(days=14)
            if not c["discussions"] and old:
                rep.recommend(f"Triage bug #{i.meta.get('number')} '{i.title[:60]}': open >14 days with no linked discussion.", [i], "high")
    errs = Counter(e for r in ctx.session.scalars(select(Record).where(Record.project_id == ctx.project_id)) for e in r.meta.get("errors", []))
    recur = [(e, n) for e, n in errs.most_common(5) if n >= 2]
    if recur:
        rep.fact("Recurring error types: " + ", ".join(f"{e} ({n} records)" for e, n in recur) + ".")
    rep.extra = {"bugs": table}
    uniq = list({r.id: r for r in ctx_records}.values())
    await apply_ai(ctx, rep, "bugs",
                   "For each bug: what happened, the investigation, the root cause if stated, how it was resolved, and what remains open.",
                   uniq or [c["issue"] for c in chains], facts_block="\n".join(f"- {f['statement']}" for f in rep.verified_facts))
    rep.summary = rep.summary or f"{st.get('resolved', 0)} resolved, {st.get('open', 0)} open of {len(chains)} bug-like issues."
    return rep
