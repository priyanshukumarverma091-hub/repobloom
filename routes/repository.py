from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func, or_, select

from database.models import Project, Record, SyncState
from services import repository_service as rs
from services.ingestion_service import is_running, rebuild_derived, start_sync_thread

bp = Blueprint("repository", __name__)


def db():
    return current_app.extensions["db"]


@bp.get("/")
def list_projects():
    with db().session() as s:
        return jsonify(repositories=[rs.project_dict(s, p) for p in s.scalars(select(Project).order_by(Project.id)).all()])


@bp.post("/")
def create_project():
    try:
        data = rs.validate_project_input(request.get_json(silent=True) or {})
    except rs.ValidationFailed as e:
        return jsonify(error=str(e)), 400
    with db().session() as s:
        p = Project(**data)
        s.add(p)
        s.flush()
        return jsonify(rs.project_dict(s, p)), 201


@bp.get("/<int:pid>")
def get_project(pid):
    with db().session() as s:
        p = s.get(Project, pid)
        return (jsonify(rs.project_dict(s, p)) if p else (jsonify(error="not_found"), 404))


@bp.patch("/<int:pid>")
def update_project(pid):
    body = request.get_json(silent=True) or {}
    with db().session() as s:
        p = s.get(Project, pid)
        if not p:
            return jsonify(error="not_found"), 404
        merged = dict(name=p.name, github_repo=p.github_repo, discord_channel_ids=p.discord_channel_ids,
                      doc_urls=p.doc_urls, local_git_path=p.local_git_path)
        merged.update({k: v for k, v in body.items() if k in merged})
        try:
            data = rs.validate_project_input(merged)
        except rs.ValidationFailed as e:
            return jsonify(error=str(e)), 400
        for k, v in data.items():
            setattr(p, k, v)
        return jsonify(rs.project_dict(s, p))


@bp.delete("/<int:pid>")
def delete_project(pid):
    if is_running(db(), pid):
        return jsonify(error="sync running"), 409
    with db().session() as s:
        p = s.get(Project, pid)
        if not p:
            return jsonify(error="not_found"), 404
        from sqlalchemy import delete
        from database.models import AnalysisResult, Memory, Relationship
        for m in (Record, Memory, Relationship, SyncState, AnalysisResult):
            s.execute(delete(m).where(m.project_id == pid))
        s.delete(p)
    return jsonify(deleted=pid)


@bp.post("/<int:pid>/sync")
def sync(pid):
    body = request.get_json(silent=True) or {}
    sources = [x for x in (body.get("sources") or []) if x in ("github", "discord", "docs")] or None
    with db().session() as s:
        p = s.get(Project, pid)
        if not p:
            return jsonify(error="not_found"), 404
        if p.is_demo:
            return jsonify(error="demo project has no live sources"), 400
    started = start_sync_thread(db(), current_app.config["REPOBLOOM"], pid, sources)
    return (jsonify(status="started"), 202) if started else (jsonify(error="sync already running"), 409)


@bp.get("/<int:pid>/status")
def status(pid):
    with db().session() as s:
        if not s.get(Project, pid):
            return jsonify(error="not_found"), 404
        rows = s.scalars(select(SyncState).where(SyncState.project_id == pid)).all()
        return jsonify(running=any(r.status == "running" for r in rows),
                       sources=[dict(source=r.source, status=r.status, message=r.message, stats=r.stats,
                                     incremental_cursor=bool(r.cursor), last_run=r.last_run.isoformat() if r.last_run else None) for r in rows])


@bp.get("/<int:pid>/records")
def records(pid):
    a = request.args
    limit = min(a.get("limit", 50, type=int), 200)
    with db().session() as s:
        q = select(Record).where(Record.project_id == pid)
        for col, key in ((Record.source, "source"), (Record.kind, "kind"), (Record.category, "category")):
            if a.get(key):
                q = q.where(col == a[key])
        if a.get("q"):
            like = f"%{a['q'][:100]}%"
            q = q.where(or_(Record.title.ilike(like), Record.content.ilike(like)))
        total = s.scalar(select(func.count()).select_from(q.subquery()))
        rows = s.scalars(q.order_by(Record.timestamp.desc().nullslast(), Record.id.desc()).limit(limit).offset(a.get("offset", 0, type=int))).all()
        return jsonify(total=total, records=[rs.record_dict(r) for r in rows])


@bp.get("/<int:pid>/records/<int:rid>")
def record(pid, rid):
    with db().session() as s:
        r = s.get(Record, rid)
        return (jsonify(rs.record_dict(r, full=True)) if r and r.project_id == pid else (jsonify(error="not_found"), 404))


@bp.post("/<int:pid>/reprocess")
def reprocess(pid):
    with db().session() as s:
        if not s.get(Project, pid):
            return jsonify(error="not_found"), 404
        return jsonify(rebuild_derived(s, pid))
