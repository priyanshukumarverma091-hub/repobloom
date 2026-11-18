from flask import Blueprint, current_app, jsonify, request

from database.models import Project
from knowledge_graph.graph_builder import build_graph
from memory.project_memory import CATEGORIES, ProjectMemory, memory_to_dict
from services import repository_service as rs
from services.analysis_service import KINDS

bp = Blueprint("intelligence", __name__)


def db():
    return current_app.extensions["db"]


def _exists(s, pid):
    return s.get(Project, pid) is not None


@bp.get("/")
def kinds():
    return jsonify(kinds=sorted(KINDS) + ["context_recovery"], memory_categories=list(CATEGORIES))


@bp.post("/<int:pid>/analyze/<kind>")
def analyze(pid, kind):
    body = request.get_json(silent=True) or {}
    with db().session() as s:
        if not _exists(s, pid):
            return jsonify(error="not_found"), 404
    if kind not in KINDS and kind != "context_recovery":
        return jsonify(error="unknown analysis kind"), 400
    q = str(body.get("question") or "")[:500]
    if kind == "context_recovery" and len(q.strip()) < 3:
        return jsonify(error="question required"), 400
    try:
        rep = current_app.extensions["analysis"].run(pid, kind, q, use_ai=body.get("ai", True) is not False)
    except Exception:
        current_app.logger.exception("analysis failed")
        return jsonify(error="analysis failed; see server logs"), 500
    return jsonify(rep)


@bp.get("/<int:pid>/latest/<kind>")
def latest(pid, kind):
    rep = current_app.extensions["analysis"].latest(pid, kind)
    return jsonify(rep) if rep else (jsonify(error="none"), 404)


@bp.get("/<int:pid>/memory")
def memory(pid):
    cat, q = request.args.get("category"), request.args.get("q", "").strip()
    with db().session() as s:
        if not _exists(s, pid):
            return jsonify(error="not_found"), 404
        pm = ProjectMemory(s, pid)
        if q:
            items = [dict(memory_to_dict(m), score=round(sc, 3)) for sc, m in pm.search(q[:300], 20) if not cat or m.category == cat]
        else:
            items = [memory_to_dict(m) for m in pm.list_memories(cat if cat in CATEGORIES else None)]
        return jsonify(stats=pm.stats(), memories=items)


@bp.get("/<int:pid>/memory/<int:mid>/history")
def memory_history(pid, mid):
    with db().session() as s:
        return jsonify(versions=[memory_to_dict(m) for m in ProjectMemory(s, pid).history(mid)])


@bp.get("/<int:pid>/graph")
def graph(pid):
    with db().session() as s:
        if not _exists(s, pid):
            return jsonify(error="not_found"), 404
        return jsonify(build_graph(s, pid))


@bp.get("/<int:pid>/search")
def search(pid):
    q = request.args.get("q", "").strip()[:300]
    if len(q) < 2:
        return jsonify(error="query too short"), 400
    with db().session() as s:
        return jsonify(results=[dict(rs.record_dict(r), score=round(sc, 3)) for sc, r in rs.search_records(s, pid, q, 20)])
