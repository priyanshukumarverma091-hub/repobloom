"""Graph storage abstraction. SQLGraphStore keeps edges in PostgreSQL/SQLite; a Neo4j (etc.)
store can implement GraphStore later without touching the Intelligence Engine."""
from abc import ABC, abstractmethod

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from database.models import Record, Relationship


class GraphStore(ABC):
    @abstractmethod
    def replace_edges(self, project_id: int, edges: list[dict]) -> int: ...
    @abstractmethod
    def edges(self, project_id: int) -> list[dict]: ...
    @abstractmethod
    def neighbors(self, project_id: int, key: str) -> list[dict]: ...


class SQLGraphStore(GraphStore):
    def __init__(self, session: Session):
        self.s = session

    def replace_edges(self, project_id, edges):
        self.s.execute(delete(Relationship).where(Relationship.project_id == project_id))
        seen = set()
        n = 0
        for e in edges:
            k = (e["src"], e["dst"], e["rel"])
            if k in seen or e["src"] == e["dst"]:
                continue
            seen.add(k)
            self.s.add(Relationship(project_id=project_id, src_key=e["src"], dst_key=e["dst"],
                                    rel_type=e["rel"], origin=e.get("origin", "verified"),
                                    confidence=e.get("confidence", 1.0), evidence=e.get("evidence", "")[:500]))
            n += 1
        self.s.flush()
        return n

    def edges(self, project_id):
        rows = self.s.scalars(select(Relationship).where(Relationship.project_id == project_id)).all()
        return [dict(src=r.src_key, dst=r.dst_key, rel=r.rel_type, origin=r.origin,
                     confidence=r.confidence, evidence=r.evidence) for r in rows]

    def neighbors(self, project_id, key):
        rows = self.s.scalars(select(Relationship).where(
            Relationship.project_id == project_id,
            (Relationship.src_key == key) | (Relationship.dst_key == key))).all()
        return [dict(src=r.src_key, dst=r.dst_key, rel=r.rel_type, origin=r.origin,
                     confidence=r.confidence, evidence=r.evidence) for r in rows]


NODE_TYPE = {"issue": "issue", "pr": "pull request", "commit": "commit", "message": "discussion",
             "doc": "documentation", "review": "review", "branch": "branch", "file_tree": "structure"}


def build_graph(session: Session, project_id: int, max_nodes: int = 400) -> dict:
    """Nodes + edges for visualisation, ranked by connectivity."""
    store = SQLGraphStore(session)
    edges = store.edges(project_id)
    degree: dict[str, int] = {}
    for e in edges:
        degree[e["src"]] = degree.get(e["src"], 0) + 1
        degree[e["dst"]] = degree.get(e["dst"], 0) + 1
    keep = set(sorted(degree, key=lambda k: -degree[k])[:max_nodes])
    rec_ids = [int(k.split(":")[1]) for k in keep if k.startswith("record:")]
    recs = {r.id: r for r in session.scalars(select(Record).where(Record.id.in_(rec_ids))).all()} if rec_ids else {}
    nodes = []
    for k in keep:
        if k.startswith("record:"):
            r = recs.get(int(k.split(":")[1]))
            if not r:
                continue
            nodes.append(dict(id=k, label=(r.title or r.kind)[:60], type=NODE_TYPE.get(r.kind, r.kind),
                              category=r.category, source=r.source, url=r.url, degree=degree[k],
                              timestamp=r.timestamp.isoformat() if r.timestamp else None))
        else:
            nodes.append(dict(id=k, label=k.split(":", 1)[1], type="file", category="code",
                              source="github", url="", degree=degree[k], timestamp=None))
    ids = {n["id"] for n in nodes}
    return {"nodes": nodes, "edges": [e for e in edges if e["src"] in ids and e["dst"] in ids]}
