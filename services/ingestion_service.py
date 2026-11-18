"""Orchestrates collect -> process -> relationship mapping -> memory refresh, with incremental cursors."""
import asyncio
import logging
import os
import threading
from datetime import datetime, timezone

from sqlalchemy import select

from collectors.base import CollectResult
from collectors.discord_collector import DiscordCollector
from collectors.docs_collector import DocsCollector
from collectors.github_collector import GitHubCollector, collect_local_git
from database.models import Project, SyncState
from knowledge_graph.relationship_mapper import map_relationships
from memory.architecture_memory import ArchitectureMemory
from memory.decision_memory import DecisionMemory
from memory.problem_memory import ProblemMemory
from memory.project_memory import DevelopmentMemory, TaskMemory
from processors.pipeline import process_batch

log = logging.getLogger(__name__)
_locks: dict[int, threading.Lock] = {}


def rebuild_derived(session, project_id: int) -> dict:
    """Relationship graph first (problem memory depends on it), then every memory category."""
    out = {"relationships": map_relationships(session, project_id)}
    out["architecture"] = ArchitectureMemory(session, project_id).refresh()
    out["development"] = DevelopmentMemory(session, project_id).refresh()
    out["decision"] = DecisionMemory(session, project_id).refresh()
    out["problem"] = ProblemMemory(session, project_id).refresh()
    out["task"] = TaskMemory(session, project_id).refresh()
    return out


def _state(session, pid, source) -> SyncState:
    st = session.scalars(select(SyncState).where(SyncState.project_id == pid, SyncState.source == source)).first()
    if not st:
        st = SyncState(project_id=pid, source=source)
        session.add(st)
        session.flush()
    return st


def is_running(db, pid: int) -> bool:
    with db.session() as s:
        return any(x.status == "running" for x in s.scalars(select(SyncState).where(SyncState.project_id == pid)))


async def _collect(config, project: Project, source: str, cursor: dict) -> CollectResult:
    if source == "github":
        if project.local_git_path and not project.github_repo:
            return await asyncio.to_thread(collect_local_git, project.local_git_path, os.path.join(os.getcwd(), "instance", "repos"), cursor)
        if not project.github_repo:
            return CollectResult("github", status="error", message="no GitHub repository configured")
        return await GitHubCollector(config.github_token, config.github_max_commit_details).collect(project.github_repo, cursor)
    if source == "discord":
        if not project.discord_channel_ids:
            return CollectResult("discord", status="error", message="no Discord channels configured")
        return await DiscordCollector(config.discord_bot_token, config.discord_anonymize).collect(project.discord_channel_ids, cursor)
    if source == "docs":
        if not project.doc_urls:
            return CollectResult("docs", status="error", message="no documentation URLs configured")
        return await DocsCollector(config.docs_user_agent).collect(project.doc_urls, cursor)
    return CollectResult(source, status="error", message=f"unknown source {source}")


def sync_project(db, config, project_id: int, sources: list[str] | None = None) -> dict:
    """Blocking sync (run in a worker thread). Returns per-source results."""
    lock = _locks.setdefault(project_id, threading.Lock())
    if not lock.acquire(blocking=False):
        return {"error": "sync already running"}
    results = {}
    try:
        with db.session() as s:
            p = s.get(Project, project_id)
            if not p:
                return {"error": "project not found"}
            sources = sources or [x for x, on in (("github", bool(p.github_repo or p.local_git_path)),
                                                  ("discord", bool(p.discord_channel_ids)), ("docs", bool(p.doc_urls))) if on]
            for src in sources:
                st = _state(s, project_id, src)
                st.status, st.message = "running", ""
            s.commit()
            proj = s.get(Project, project_id)
            s.expunge(proj)
        for src in sources:
            with db.session() as s:
                cursor = dict(_state(s, project_id, src).cursor or {})
            try:
                res = asyncio.run(_collect(config, proj, src, cursor))
            except Exception as e:  # collector bugs must not kill the whole sync
                log.exception("collector %s crashed", src)
                res = CollectResult(src, status="error", message=f"{type(e).__name__}: {e}")
            with db.session() as s:
                stats = process_batch(s, project_id, res.records).as_dict() if res.records else {}
                st = _state(s, project_id, src)
                if res.status != "error":
                    st.cursor = res.cursor          # advance cursor only on (partial) success
                st.status, st.message = res.status, res.message[:1000]
                st.stats = {**res.stats, **stats}
                st.last_run = datetime.now(timezone.utc)
                results[src] = dict(status=res.status, message=res.message, stats=st.stats)
        with db.session() as s:
            results["derived"] = rebuild_derived(s, project_id)
        return results
    finally:
        lock.release()


def start_sync_thread(db, config, project_id: int, sources=None) -> bool:
    if is_running(db, project_id):
        return False
    t = threading.Thread(target=lambda: sync_project(db, config, project_id, sources), daemon=True, name=f"sync-{project_id}")
    t.start()
    return True
