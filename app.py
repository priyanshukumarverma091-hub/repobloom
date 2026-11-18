"""RepoBloom Flask application factory."""
import hmac
import logging

from flask import Flask, jsonify, request
from sqlalchemy import select

from config import Config, get_config
from database.connection import Database
from database.models import SyncState
from routes.dashboard import bp as dashboard_bp
from routes.intelligence import bp as intelligence_bp
from routes.repository import bp as repository_bp
from services.analysis_service import AnalysisService


def create_app(config: Config | None = None, provider=None) -> Flask:
    cfg = config or get_config()
    app = Flask(__name__)
    app.config.update(SECRET_KEY=cfg.secret_key, REPOBLOOM=cfg, MAX_CONTENT_LENGTH=1_000_000, JSON_SORT_KEYS=False)
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO))

    db = Database(cfg.resolved_database_url)
    db.init_schema()
    with db.session() as s:      # a crash mid-sync must not leave a project 'running' forever
        for st in s.scalars(select(SyncState).where(SyncState.status == "running")):
            st.status, st.message = "error", "interrupted by restart"
    app.extensions["db"] = db
    app.extensions["analysis"] = AnalysisService(cfg, db, provider)

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(repository_bp, url_prefix="/api/repositories")
    app.register_blueprint(intelligence_bp, url_prefix="/api/intelligence")

    @app.before_request
    def require_token():
        if cfg.access_token and request.path.startswith("/api/"):
            given = request.headers.get("X-RepoBloom-Token", "")
            if not hmac.compare_digest(given.encode(), cfg.access_token.encode()):
                return jsonify(error="unauthorized", hint="send X-RepoBloom-Token (set it in Settings)"), 401

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if not request.path.startswith("/api/"):
            resp.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                    "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        return resp

    @app.errorhandler(404)
    def not_found(_):
        return jsonify(error="not_found"), 404

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="payload_too_large"), 413

    @app.errorhandler(500)
    def server_error(_):
        return jsonify(error="internal_error"), 500

    return app


if __name__ == "__main__":
    a = create_app()
    a.run(debug=a.config["REPOBLOOM"].env == "development")
