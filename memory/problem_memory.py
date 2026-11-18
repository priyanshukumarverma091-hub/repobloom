from memory.project_memory import ProjectMemory
from knowledge_graph.relationship_mapper import resolution_chains


class ProblemMemory(ProjectMemory):
    def refresh(self) -> int:
        n = 0
        for ch in resolution_chains(self.s, self.pid):
            i = ch["issue"]
            links = [i.id] + [r.id for r in ch["resolvers"] + ch["decisions"] + ch["discussions"]]
            parts = [f"{i.title}. Status: {ch['status'].replace('_', ' ')}."]
            if ch["resolvers"]:
                parts.append("Resolved/addressed by: " + "; ".join(f"{r.kind} {r.external_id[:8]} '{r.title[:60]}'" for r in ch["resolvers"][:4]) + ".")
            if ch["files"]:
                parts.append("Files changed: " + ", ".join(ch["files"][:6]) + ".")
            if ch["decisions"]:
                parts.append("Decision(s): " + "; ".join(d.title[:80] for d in ch["decisions"][:2]) + ".")
            if ch["discussions"]:
                parts.append(f"{len(ch['discussions'])} linked discussion(s).")
            self.upsert("problem", f"problem-issue-{i.meta.get('number', i.id)}", f"Bug #{i.meta.get('number')}: {i.title}"[:200],
                        " ".join(parts), links, status="resolved" if ch["status"] == "resolved" else
                        ("open" if ch["status"] == "open" else ch["status"]), note="from knowledge-graph resolution chain")
            n += 1
        return n
