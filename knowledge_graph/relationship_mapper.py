"""Derive relationships between records.

Explicit references (verified): "fixes #12", "#12", commit SHAs, issue/PR URLs, review->PR.
Heuristic links (inferred, lower confidence): discussion <-> commit that touch the same file
within a week.  Chain modelled: Bug -> Issue -> Discussion -> Decision -> Commit -> File -> Resolved.
"""
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Record
from knowledge_graph.graph_builder import SQLGraphStore


def _k(r: Record) -> str:
    return f"record:{r.id}"


def map_relationships(session: Session, project_id: int) -> int:
    recs = session.scalars(select(Record).where(Record.project_id == project_id)).all()
    by_number: dict[int, list[Record]] = defaultdict(list)       # issue/pr numbers
    by_sha: dict[str, Record] = {}
    commits_by_file: dict[str, list[Record]] = defaultdict(list)
    for r in recs:
        if r.kind in ("issue", "pr") and r.meta.get("number"):
            by_number[int(r.meta["number"])].append(r)
        if r.kind == "commit":
            by_sha[r.external_id] = r
            for f in r.meta.get("files", []):
                commits_by_file[f].append(r)

    def find_sha(prefix: str):
        if prefix in by_sha:
            return by_sha[prefix]
        if len(prefix) >= 7:
            for sha, c in by_sha.items():
                if sha.startswith(prefix):
                    return c
        return None

    edges: list[dict] = []

    def add(src, dst, rel, origin="verified", conf=1.0, ev=""):
        edges.append(dict(src=src, dst=dst, rel=rel, origin=origin, confidence=conf, evidence=ev))

    for r in recs:
        closes = set(r.meta.get("closes_refs", []))
        refs = set(r.meta.get("issue_refs", []))
        for n in closes:
            for target in by_number.get(n, []):
                if r.kind in ("commit", "pr") and target.id != r.id:
                    add(_k(r), _k(target), "resolves", ev=f"'{r.kind}' text closes #{n}")
        for n in refs - closes:
            for target in by_number.get(n, []):
                if target.id != r.id:
                    rel = "discusses" if r.kind == "message" else "references"
                    add(_k(r), _k(target), rel, ev=f"mentions #{n}")
        for sha in r.meta.get("sha_refs", []):
            c = find_sha(sha)
            if c and c.id != r.id:
                add(_k(r), _k(c), "references_commit", ev=f"mentions commit {sha[:8]}")
        if r.kind == "commit":
            for f in r.meta.get("files", [])[:25]:
                add(_k(r), f"file:{f}", "modifies", ev="commit file list")
        if r.kind == "review" and r.meta.get("pr_number"):
            for t in by_number.get(int(r.meta["pr_number"]), []):
                add(_k(r), _k(t), "reviews", ev="review of PR")
        if r.kind == "pr":
            for sha in r.meta.get("commit_shas", []):
                c = by_sha.get(sha)
                if c:
                    add(_k(c), _k(r), "part_of", ev="PR commit list")
        if r.kind == "issue" and r.meta.get("closed_by_commit"):
            c = by_sha.get(r.meta["closed_by_commit"])
            if c:
                add(_k(c), _k(r), "resolves", ev="GitHub closing event")
        if r.category == "decision":
            for n in refs:
                for target in by_number.get(n, []):
                    if target.kind == "issue":
                        add(_k(r), _k(target), "decided_for", ev=f"decision text references #{n}")

    # inferred: discussion/decision mentions a file that a nearby commit modified
    for r in recs:
        if r.kind != "message" or not r.timestamp:
            continue
        for f in r.meta.get("file_refs", [])[:5]:
            for c in commits_by_file.get(f, []) + commits_by_file.get(f.split("/")[-1], []):
                if c.timestamp and abs(c.timestamp - r.timestamp) <= timedelta(days=7):
                    add(_k(r), _k(c), "related_to", "inferred", 0.4, f"same file {f} within 7 days")
    return SQLGraphStore(session).replace_edges(project_id, edges)


def resolution_chains(session: Session, project_id: int) -> list[dict]:
    """For every bug-like issue: linked discussions, decisions, resolving commits/PRs, files."""
    store = SQLGraphStore(session)
    edges = store.edges(project_id)
    recs = {r.id: r for r in session.scalars(select(Record).where(Record.project_id == project_id)).all()}
    out_by_dst: dict[str, list[dict]] = defaultdict(list)
    out_by_src: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        out_by_dst[e["dst"]].append(e)
        out_by_src[e["src"]].append(e)

    def rec_of(key):
        return recs.get(int(key.split(":")[1])) if key.startswith("record:") else None

    chains = []
    for r in recs.values():
        if r.kind != "issue" or r.category != "bug":
            continue
        key = f"record:{r.id}"
        linked = out_by_dst[key]
        discussions = [rec_of(e["src"]) for e in linked if e["rel"] in ("discusses", "references") and rec_of(e["src"])]
        decisions = [rec_of(e["src"]) for e in linked if e["rel"] == "decided_for" and rec_of(e["src"])]
        resolvers = [rec_of(e["src"]) for e in linked if e["rel"] == "resolves" and rec_of(e["src"])]
        files = sorted({e["dst"][5:] for c in resolvers for e in out_by_src[f"record:{c.id}"] if e["rel"] == "modifies"})
        closed = r.meta.get("state") == "closed"
        status = "resolved" if (resolvers and closed) else ("fix_proposed" if resolvers else ("closed_without_fix_link" if closed else "open"))
        chains.append(dict(issue=r, discussions=discussions, decisions=decisions, resolvers=resolvers,
                           files=files, status=status))
    return chains
