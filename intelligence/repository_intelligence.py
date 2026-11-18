"""Repository Intelligence, Architecture Intelligence and Developer Activity Analysis."""
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import select

from database.models import Record
from intelligence.base import Ctx, Report, apply_ai, aware, now
from memory.architecture_memory import analyze_tree


def _records(ctx: Ctx, kinds=None, limit=600):
    q = select(Record).where(Record.project_id == ctx.project_id)
    if kinds:
        q = q.where(Record.kind.in_(kinds))
    return list(ctx.session.scalars(q.order_by(Record.timestamp.desc().nullslast()).limit(limit)).all())


async def repository_report(ctx: Ctx) -> Report:
    rep = Report("repository", ctx.project_id)
    recs = _records(ctx)
    if not recs:
        rep.unknown.append("No data collected yet. Add a repository and run a sync.")
        return rep
    by_kind = Counter(r.kind for r in recs)
    commits = [r for r in recs if r.kind == "commit"]
    prs = [r for r in recs if r.kind == "pr"]
    issues = [r for r in recs if r.kind == "issue"]
    rep.fact(f"{len(recs)} records stored: " + ", ".join(f"{k} {v}" for k, v in by_kind.most_common()) + ".", recs[:3])
    if commits:
        ts = [aware(c.timestamp) for c in commits if c.timestamp]
        authors = Counter(c.author for c in commits if c.author)
        rep.fact(f"{len(commits)} commits between {min(ts):%Y-%m-%d} and {max(ts):%Y-%m-%d} by {len(authors)} author(s).", commits[:3])
        top, n = authors.most_common(1)[0]
        share = n / len(commits)
        rep.fact(f"Top committer: {top} with {n} commit(s) ({share:.0%}).", [c for c in commits if c.author == top][:3])
        if len(commits) >= 10 and share >= 0.8:
            rep.recommend(f"{top} authored {share:.0%} of commits; knowledge is concentrated. Spread ownership via reviews and docs.",
                          [c for c in commits if c.author == top][:2], "medium")
    if prs:
        merged = [p for p in prs if p.meta.get("merged")]
        open_ = [p for p in prs if p.meta.get("state") == "open"]
        rep.fact(f"{len(prs)} pull requests: {len(merged)} merged, {len(open_)} open.", prs[:3])
        stale = [p for p in open_ if p.timestamp and now() - aware(p.timestamp) > timedelta(days=14)]
        if stale:
            rep.recommend(f"{len(stale)} open PR(s) older than 14 days: " + ", ".join(f"#{p.meta.get('number')}" for p in stale[:6]) +
                          ". Review or close them.", stale, "medium")
    if issues:
        open_i = [i for i in issues if i.meta.get("state") == "open"]
        rep.fact(f"{len(issues)} issues: {len(open_i)} open, {len(issues) - len(open_i)} closed.", issues[:3])
    tree = next((r for r in recs if r.kind == "file_tree"), None)
    if tree:
        a = analyze_tree(tree.meta.get("paths", []))
        if a["files"] and a["test_files"] == 0:
            rep.fact("No test-related files were found in the repository file tree.", [tree])
            rep.recommend("Add automated tests for critical modules.", [tree], "high")
    else:
        rep.unknown.append("Repository file structure has not been collected.")
    facts = "\n".join(f"- {f['statement']}" for f in rep.verified_facts)
    await apply_ai(ctx, rep, "repository",
                   "Explain how this repository evolved: major features, code changes and their purpose, what is in progress. "
                   "Use the commits, PRs and structure.", recs, facts_block=facts)
    rep.summary = rep.summary or f"Repository has {len(commits)} commits, {len(prs)} PRs and {len(issues)} issues collected."
    return rep


async def architecture_report(ctx: Ctx) -> Report:
    rep = Report("architecture", ctx.project_id)
    recs = _records(ctx, {"file_tree", "commit", "pr", "repo", "branch"})
    tree = next((r for r in recs if r.kind == "file_tree"), None)
    if not tree:
        rep.unknown.append("No file-tree data. Sync the GitHub repository (or local git path) first.")
        return rep
    a = analyze_tree(tree.meta.get("paths", []))
    rep.fact(f"{a['files']} files in repository.", [tree])
    if a["languages"]:
        rep.fact("Languages (by file count): " + ", ".join(f"{l} {c}" for l, c in a["languages"]) + ".", [tree])
    if a["top_dirs"]:
        rep.fact("Top-level directories: " + ", ".join(f"{d}/ ({c})" for d, c in a["top_dirs"]) + ".", [tree])
    if a["markers"]:
        rep.fact("Detected from file names: " + ", ".join(a["markers"]) + ".", [tree])
    hot = Counter(f for r in recs if r.kind == "commit" for f in r.meta.get("files", []))
    if hot:
        rep.fact("Most frequently changed files: " + ", ".join(f"{f} ({c})" for f, c in hot.most_common(6)) + ".",
                 [r for r in recs if r.kind == "commit"][:3])
    rep.extra = {"tree": a, "hotspots": hot.most_common(12)}
    if a["test_files"] == 0:
        rep.recommend("No test files detected; add tests before large refactors.", [tree], "high")
    await apply_ai(ctx, rep, "architecture",
                   "Describe the architecture: components, layers, entry points, data flow, and notable architectural risks. "
                   "Base it only on the file tree and commit history.", recs,
                   facts_block="\n".join(f"- {f['statement']}" for f in rep.verified_facts))
    rep.summary = rep.summary or f"{a['files']} files; main languages: {', '.join(l for l, _ in a['languages'][:3]) or 'unknown'}."
    return rep


async def activity_report(ctx: Ctx) -> Report:
    rep = Report("activity", ctx.project_id)
    recs = _records(ctx, {"commit", "pr", "review", "issue", "message"}, 1500)
    commits = [r for r in recs if r.kind == "commit" and r.timestamp]
    if not commits and not recs:
        rep.unknown.append("No activity data yet.")
        return rep
    weekly = defaultdict(int)
    for c in commits:
        y, w, _ = aware(c.timestamp).isocalendar()
        weekly[f"{y}-W{w:02d}"] += 1
    per_author = defaultdict(lambda: Counter())
    for r in recs:
        if r.author:
            per_author[r.author][r.kind] += 1
    rows = sorted(({"author": a, **dict(c), "total": sum(c.values())} for a, c in per_author.items()), key=lambda x: -x["total"])
    rep.extra = {"weekly_commits": sorted(weekly.items()), "authors": rows[:20]}
    if rows:
        rep.fact(f"{len(rows)} active contributor(s). Most active: {rows[0]['author']} ({rows[0]['total']} items).", recs[:3])
    if weekly:
        busiest = max(weekly.items(), key=lambda x: x[1])
        rep.fact(f"Busiest week: {busiest[0]} with {busiest[1]} commits. Active weeks: {len(weekly)}.", commits[:3])
        cutoff = now() - timedelta(days=30)
        recent = [c for c in commits if aware(c.timestamp) >= cutoff]
        rep.fact(f"{len(recent)} commit(s) in the last 30 days.", recent[:3] or commits[:1])
        if not recent:
            rep.recommend("No commits in 30 days: confirm whether the project is paused or data is stale (re-sync).", commits[:1], "low")
    rep.summary = f"{len(rows)} contributor(s) across {len(recs)} activity records."
    await apply_ai(ctx, rep, "activity", "Analyse developer activity: who works on what, collaboration patterns, review behaviour, risks.",
                   recs, facts_block="\n".join(f"- {f['statement']}" for f in rep.verified_facts))
    return rep
