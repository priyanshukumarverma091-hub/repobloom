from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import func, select

from database.models import AIUsage, Project
from services import repository_service as rs
from services.demo_data import seed_demo

bp = Blueprint("dashboard", __name__)


def db():
    return current_app.extensions["db"]


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/health")
def health():
    return jsonify(status="ok", config=current_app.config["REPOBLOOM"].public_summary())


@bp.get("/api/overview")
def overview():
    pid = request.args.get("project_id", type=int)
    with db().session() as s:
        projects = [rs.project_dict(s, p) for p in s.scalars(select(Project).order_by(Project.id)).all()]
        data = rs.overview(s, pid) if pid and s.get(Project, pid) else None
    return jsonify(projects=projects, overview=data)


@bp.get("/api/ai/activity")
def ai_activity():
    cfg = current_app.config["REPOBLOOM"]
    with db().session() as s:
        rows = s.scalars(select(AIUsage).order_by(AIUsage.id.desc()).limit(50)).all()
        tot = s.execute(select(AIUsage.role, func.count(), func.sum(AIUsage.prompt_tokens), func.sum(AIUsage.completion_tokens),
                               func.sum(AIUsage.cost_usd), func.sum(AIUsage.ok.cast(__import__("sqlalchemy").Integer))).group_by(AIUsage.role)).all()
    roles = {k: dict(model=m.model or None, configured=bool(m.model), max_tokens=m.max_tokens, provider=m.provider) for k, m in cfg.models.items()}
    for role, calls, pt, ct, cost, ok in tot:
        roles.setdefault(role, {}).update(calls=calls, prompt_tokens=pt or 0, completion_tokens=ct or 0, cost_usd=round(cost or 0, 6),
                                          ok_calls=ok or 0, failed_calls=calls - (ok or 0))
    return jsonify(roles=roles, ai_ready=cfg.ai_ready,
                   totals=dict(calls=sum(r.get("calls", 0) for r in roles.values()),
                               cost_usd=round(sum(r.get("cost_usd", 0) for r in roles.values()), 6),
                               tokens=sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in roles.values())),
                   recent=[dict(role=r.role, model=r.model, task=r.task, prompt_tokens=r.prompt_tokens, completion_tokens=r.completion_tokens,
                                cost_usd=r.cost_usd, latency_ms=r.latency_ms, ok=r.ok, error=r.error,
                                at=r.created_at.isoformat() if r.created_at else None) for r in rows])


@bp.get("/api/settings")
def settings():
    cfg = current_app.config["REPOBLOOM"]
    return jsonify(config=cfg.public_summary(), models={k: dict(model=v.model, max_tokens=v.max_tokens) for k, v in cfg.models.items()},
                   discord_anonymize=cfg.discord_anonymize)


@bp.post("/api/demo")
def demo():
    pid = seed_demo(db())
    return jsonify(project_id=pid), 201
