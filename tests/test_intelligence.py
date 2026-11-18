import asyncio

from collectors.base import CollectResult, RawRecord
from database.models import Memory, Project
from memory.project_memory import ProjectMemory
from services import ingestion_service as ing
from services.demo_data import demo_records, seed_demo


def test_demo_end_to_end_no_ai(client):
    pid = client.post("/api/demo").get_json()["project_id"]
    ov = client.get(f"/api/overview?project_id={pid}").get_json()["overview"]
    assert ov["by_kind"]["commit"] == 3 and ov["relationships"] > 5
    bugs = client.post(f"/api/intelligence/{pid}/analyze/bugs", json={}).get_json()
    st = {b["number"]: b["status"] for b in bugs["extra"]["bugs"]}
    assert st == {12: "resolved", 17: "open"}
    b12 = next(b for b in bugs["extra"]["bugs"] if b["number"] == 12)
    assert b12["decisions"] == 1 and "payments/gateway.py" in b12["files"] and len(b12["resolved_by"]) == 2
    tasks = client.post(f"/api/intelligence/{pid}/analyze/tasks", json={}).get_json()
    titles = " ".join(t["title"] for t in tasks["extra"]["tasks"])
    assert "Webhook" in titles and "refunds" in titles.lower()
    arch = client.post(f"/api/intelligence/{pid}/analyze/architecture", json={}).get_json()
    assert any("Python" in f["statement"] for f in arch["verified_facts"])
    rec = client.post(f"/api/intelligence/{pid}/analyze/context_recovery", json={"question": "duplicate charges gateway timeout"}).get_json()
    assert rec["extra"]["timeline"][0]["direct"] in (True, False) and any("Duplicate charges" in t["title"] for t in rec["extra"]["timeline"])
    for r in rec["verified_facts"]:
        assert r["basis"] == "computed"
    assert client.post(f"/api/intelligence/{pid}/analyze/context_recovery", json={"question": " "}).status_code == 400
    assert client.post(f"/api/intelligence/{pid}/analyze/bogus", json={}).status_code == 400


def test_graph_chain_and_search(client):
    pid = client.post("/api/demo").get_json()["project_id"]
    g = client.get(f"/api/intelligence/{pid}/graph").get_json()
    rels = {e["rel"] for e in g["edges"]}
    assert {"resolves", "decided_for", "discusses", "modifies", "part_of"} <= rels
    assert any(n["type"] == "file" and n["label"] == "payments/gateway.py" for n in g["nodes"])
    hits = client.get(f"/api/intelligence/{pid}/search?q=idempotency key").get_json()["results"]
    assert hits and "idempot" in (hits[0]["title"] + hits[0]["snippet"]).lower()


def test_memory_versioning_and_provenance(env, make_app):
    app = make_app()
    db = app.extensions["db"]
    with db.session() as s:
        p = Project(name="m")
        s.add(p)
        s.flush()
        pm = ProjectMemory(s, p.id)
        m1, how = pm.upsert("decision", "k", "Use Postgres", "We use Postgres.", [1], origin="verified")
        assert how == "created"
        _, how = pm.upsert("decision", "k", "Use Postgres", "we  use postgres.", [1])
        assert how == "unchanged"                                   # same content -> no duplicate
        _, how = pm.upsert("decision", "k", "Use Postgres", "we  use postgres.", [2])
        assert how == "reconfirmed"                                 # provenance extended, still one row
        m2, how = pm.upsert("decision", "k", "Use Postgres", "We switched to MySQL.", [3], origin="inferred")
        assert how == "updated" and m2.version == 2 and m2.needs_review     # contradiction flagged, old kept
        hist = pm.history(m2.id)
        assert [h.version for h in hist] == [1, 2] and hist[0].superseded_by == m2.id
        assert len(pm.list_memories(include_superseded=True)) == 2 and len(pm.list_memories()) == 1
        _, _ = pm.upsert("task", "t", "Fix", "x", [1], status="done")
        m, _ = pm.upsert("task", "t", "Fix", "x", [1], status="open")
        assert m.needs_review                                       # reopened terminal item is flagged


def test_ingestion_sync_incremental_cursor(make_app, monkeypatch):
    app = make_app()
    db, cfg = app.extensions["db"], app.config["REPOBLOOM"]
    seen = []

    async def fake_collect(config, project, source, cursor):
        seen.append(dict(cursor))
        recs = demo_records() if not cursor else []
        return CollectResult("github", recs, {"commits_since": "2026-01-01T00:00:00Z"}, "ok", "", {"records": len(recs)})
    monkeypatch.setattr(ing, "_collect", fake_collect)
    c = app.test_client()
    pid = c.post("/api/repositories/", json={"github_repo": "o/r"}).get_json()["id"]
    r1 = ing.sync_project(db, cfg, pid)
    assert r1["github"]["status"] == "ok" and r1["github"]["stats"]["inserted"] > 10 and r1["derived"]["relationships"] > 5
    r2 = ing.sync_project(db, cfg, pid)
    assert seen[1] == {"commits_since": "2026-01-01T00:00:00Z"}     # cursor handed to the next run
    st = c.get(f"/api/repositories/{pid}/status").get_json()
    assert st["sources"][0]["status"] == "ok" and st["sources"][0]["incremental_cursor"] is True
    assert c.get(f"/api/repositories/{pid}/records?kind=issue").get_json()["total"] == 4
    # failed collector must not advance the cursor
    async def failing(config, project, source, cursor):
        return CollectResult("github", [], {"commits_since": "BROKEN"}, "error", "token invalid")
    monkeypatch.setattr(ing, "_collect", failing)
    ing.sync_project(db, cfg, pid)
    from database.models import SyncState
    with db.session() as s:
        assert s.query(SyncState).one().cursor["commits_since"] == "2026-01-01T00:00:00Z"
    assert c.post(f"/api/repositories/{seed_demo(db)}/sync").status_code == 400      # demo has no live sources
