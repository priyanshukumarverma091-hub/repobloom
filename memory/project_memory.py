"""Persistent project memory: creation, versioned updates, retrieval, history, provenance.

Rules: identical re-statements only add provenance (no duplicates); changed statements create a
new version, the old one is kept and linked (superseded_by) so contradictions are never silently lost.
"""
import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Memory, Record
from processors.normalizer import cosine, embed_text

CATEGORIES = ("architecture", "development", "decision", "problem", "task")
TERMINAL = {"resolved", "done", "merged", "closed"}


def _h(s: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", s.lower()).strip().encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectMemory:
    def __init__(self, session: Session, project_id: int):
        self.s, self.pid = session, project_id

    # ---- create / update ----
    def upsert(self, category: str, key: str, title: str, summary: str, source_record_ids: list[int],
               status: str = "active", origin: str = "verified", note: str = "") -> tuple[Memory, str]:
        if category not in CATEGORIES:
            raise ValueError(f"unknown memory category {category!r}")
        ids = sorted({int(i) for i in source_record_ids})
        cur = self.s.scalars(select(Memory).where(
            Memory.project_id == self.pid, Memory.category == category, Memory.key == key,
            Memory.superseded_by.is_(None))).first()
        sh = _h(summary)
        if cur is None:
            m = Memory(project_id=self.pid, category=category, key=key, title=title[:400], summary=summary,
                       status=status, origin=origin, source_record_ids=ids, summary_hash=sh,
                       provenance=[{"at": _now(), "event": "created", "records": ids, "note": note}])
            self.s.add(m)
            self.s.flush()
            return m, "created"
        if cur.summary_hash == sh and cur.status == status:
            if set(ids) - set(cur.source_record_ids):
                cur.source_record_ids = sorted(set(cur.source_record_ids) | set(ids))
                cur.provenance = cur.provenance + [{"at": _now(), "event": "reconfirmed", "records": ids, "note": note}]
                return cur, "reconfirmed"
            return cur, "unchanged"
        review = (cur.origin == "verified" and origin == "inferred") or (cur.status in TERMINAL and status not in TERMINAL)
        new = Memory(project_id=self.pid, category=category, key=key, title=title[:400], summary=summary,
                     status=status, origin=origin, version=cur.version + 1, needs_review=review,
                     source_record_ids=ids, summary_hash=sh,
                     provenance=[{"at": _now(), "event": "updated", "records": ids, "supersedes": cur.id,
                                  "previous_status": cur.status, "previous_origin": cur.origin, "note": note}])
        self.s.add(new)
        self.s.flush()
        cur.superseded_by = new.id
        return new, "updated"

    # ---- retrieval ----
    def list_memories(self, category: str | None = None, include_superseded: bool = False, limit: int = 200) -> list[Memory]:
        q = select(Memory).where(Memory.project_id == self.pid)
        if category:
            q = q.where(Memory.category == category)
        if not include_superseded:
            q = q.where(Memory.superseded_by.is_(None))
        return list(self.s.scalars(q.order_by(Memory.updated_at.desc()).limit(limit)).all())

    def history(self, memory_id: int) -> list[Memory]:
        m = self.s.get(Memory, memory_id)
        if not m or m.project_id != self.pid:
            return []
        rows = self.s.scalars(select(Memory).where(Memory.project_id == self.pid, Memory.category == m.category,
                                                   Memory.key == m.key).order_by(Memory.version)).all()
        return list(rows)

    def search(self, query: str, k: int = 8) -> list[tuple[float, Memory]]:
        qv = embed_text(query)
        words = set(re.findall(r"\w+", query.lower()))
        out = []
        for m in self.list_memories(limit=1000):
            text = f"{m.title}\n{m.summary}"
            score = cosine(qv, embed_text(text)) + 0.05 * len(words & set(re.findall(r"\w+", text.lower())))
            out.append((score, m))
        out.sort(key=lambda x: -x[0])
        return [x for x in out[:k] if x[0] > 0.05]

    def stats(self) -> dict:
        c = Counter(m.category for m in self.list_memories())
        return {cat: c.get(cat, 0) for cat in CATEGORIES}


def memory_to_dict(m: Memory, session: Session | None = None) -> dict:
    return dict(id=m.id, category=m.category, key=m.key, title=m.title, summary=m.summary, status=m.status,
                origin=m.origin, version=m.version, needs_review=m.needs_review, superseded=m.superseded_by is not None,
                source_record_ids=m.source_record_ids, provenance=m.provenance,
                updated_at=m.updated_at.isoformat() if m.updated_at else None)


class DevelopmentMemory(ProjectMemory):
    """Weekly development digests + merged work, derived deterministically from commits/PRs."""

    def refresh(self) -> int:
        n = 0
        commits = self.s.scalars(select(Record).where(Record.project_id == self.pid, Record.kind == "commit",
                                                      Record.timestamp.is_not(None))).all()
        weeks: dict[str, list[Record]] = defaultdict(list)
        for c in commits:
            y, w, _ = c.timestamp.isocalendar()
            weeks[f"{y}-W{w:02d}"].append(c)
        for wk, cs in weeks.items():
            authors = Counter(c.author for c in cs if c.author)
            files = Counter(f for c in cs for f in c.meta.get("files", []))
            lines = [f"{len(cs)} commit(s) by {', '.join(f'{a} ({k})' for a, k in authors.most_common(5)) or 'unknown'}."]
            lines.append("Commits: " + "; ".join(c.title for c in sorted(cs, key=lambda x: x.timestamp)[:8]) + ".")
            if files:
                lines.append("Most-touched files: " + ", ".join(f"{f} ({k})" for f, k in files.most_common(5)) + ".")
            self.upsert("development", f"week-{wk}", f"Development in {wk}", " ".join(lines), [c.id for c in cs],
                        status="active", note="derived from commit history")
            n += 1
        prs = self.s.scalars(select(Record).where(Record.project_id == self.pid, Record.kind == "pr")).all()
        for p in prs:
            merged = p.meta.get("merged")
            st = "merged" if merged else p.meta.get("state", "open")
            self.upsert("development", f"pr-{p.meta.get('number', p.id)}", f"PR #{p.meta.get('number')}: {p.title}",
                        f"{'Merged' if merged else st.capitalize()} pull request by {p.author or 'unknown'}"
                        f" ({p.meta.get('head', '?')} -> {p.meta.get('base', '?')}). {(p.content or '')[:300]}",
                        [p.id], status=st, note="from pull request record")
            n += 1
        return n


class TaskMemory(ProjectMemory):
    """Open work: open issues/PRs and explicit TODO/FIXME markers found in the collected text."""

    def refresh(self) -> int:
        n = 0
        for r in self.s.scalars(select(Record).where(Record.project_id == self.pid, Record.kind.in_(["issue", "pr"]))):
            state = r.meta.get("state", "open")
            done = state == "closed" or r.meta.get("merged")
            self.upsert("task", f"{r.kind}-{r.meta.get('number', r.id)}",
                        f"{'Issue' if r.kind == 'issue' else 'PR'} #{r.meta.get('number')}: {r.title}",
                        f"{(r.content or '')[:350]} (labels: {', '.join(r.meta.get('labels', [])) or 'none'})",
                        [r.id], status="done" if done else "open", note="tracked from GitHub state")
            n += 1
        todos = 0
        for r in self.s.scalars(select(Record).where(Record.project_id == self.pid, Record.source != "docs")):
            for t in r.meta.get("todos", []):
                if todos >= 30:
                    break
                todos += 1
                self.upsert("task", f"todo-{r.id}-{_h(t)[:8]}", f"TODO: {t[:80]}", f"'{t}' (found in {r.kind}: {r.title[:80]})",
                            [r.id], status="open", note="explicit TODO/FIXME marker")
                n += 1
        return n
