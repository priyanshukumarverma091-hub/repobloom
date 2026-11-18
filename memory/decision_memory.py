import re

from sqlalchemy import select

from database.models import Record
from memory.project_memory import ProjectMemory

STRONG = re.compile(r"we (decided|agreed)|\bADR\b|going with|let'?s go with|we('ll| will) (use|go with|adopt)", re.I)


class DecisionMemory(ProjectMemory):
    def refresh(self) -> int:
        n = 0
        rows = self.s.scalars(select(Record).where(Record.project_id == self.pid, Record.category == "decision",
                                                   Record.kind.in_(["message", "issue", "pr", "review"]))).all()
        for r in rows:
            strong = bool(STRONG.search(f"{r.title} {r.content}"))
            refs = r.meta.get("issue_refs", [])
            self.upsert("decision", f"decision-{r.id}", r.title[:120] or "Decision",
                        f"{r.content[:450]}" + (f" (relates to #{', #'.join(map(str, refs))})" if refs else ""),
                        [r.id], origin="verified" if strong else "inferred",
                        note="explicit decision wording" if strong else "classified as decision by keywords; confirm manually")
            n += 1
        return n
