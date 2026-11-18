"""Project + record access helpers and hybrid (vector + keyword) search."""
import json
import re

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from database.models import Memory, Project, Record, Relationship, SyncState
from processors.normalizer import cosine, embed_text


class ValidationFailed(ValueError):
    pass


_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def validate_project_input(data: dict) -> dict:
    name = str(data.get("name") or "").strip()
    repo = str(data.get("github_repo") or "").strip() or None
    if repo:
        repo = re.sub(r"^https?://github\.com/", "", repo).strip("/")
        repo = re.sub(r"\.git$", "", repo)
        if not _REPO.match(repo):
            raise ValidationFailed("github_repo must look like owner/name")
    if not name:
        name = repo or ""
    if not (1 <= len(name) <= 200):
        raise ValidationFailed("name is required (max 200 chars)")
    channels = [str(c).strip() for c in (data.get("discord_channel_ids") or []) if str(c).strip()]
    if any(not c.isdigit() or len(c) > 25 for c in channels):
        raise ValidationFailed("discord channel ids must be numeric")
    urls = [str(u).strip() for u in (data.get("doc_urls") or []) if str(u).strip()]
    if any(not re.match(r"^https?://[^\s]+$", u) for u in urls) or len(urls) > 30:
        raise ValidationFailed("doc_urls must be http(s) URLs (max 30)")
    local = str(data.get("local_git_path") or "").strip() or None
    return dict(name=name, github_repo=repo, discord_channel_ids=channels[:20], doc_urls=urls, local_git_path=local)


def project_dict(s: Session, p: Project) -> dict:
    counts = dict(s.execute(select(Record.source, func.count()).where(Record.project_id == p.id).group_by(Record.source)).all())
    return dict(id=p.id, name=p.name, github_repo=p.github_repo, discord_channel_ids=p.discord_channel_ids,
                doc_urls=p.doc_urls, is_demo=p.is_demo, local_git_path=p.local_git_path,
                created_at=p.created_at.isoformat() if p.created_at else None,
                record_counts=counts, total_records=sum(counts.values()))


def record_dict(r: Record, full: bool = False) -> dict:
    d = dict(id=r.id, ref=f"R{r.id}", source=r.source, kind=r.kind, title=r.title, url=r.url, author=r.author,
             timestamp=r.timestamp.isoformat() if r.timestamp else None, category=r.category,
             category_confidence=r.category_confidence, permissions=r.permissions,
             snippet=(r.content or "")[:240])
    if full:
        d.update(content=r.content, meta=r.meta)
    return d


def overview(s: Session, pid: int) -> dict:
    by_kind = dict(s.execute(select(Record.kind, func.count()).where(Record.project_id == pid).group_by(Record.kind)).all())
    by_cat = dict(s.execute(select(Record.category, func.count()).where(Record.project_id == pid).group_by(Record.category)).all())
    mem = dict(s.execute(select(Memory.category, func.count()).where(Memory.project_id == pid, Memory.superseded_by.is_(None)).group_by(Memory.category)).all())
    rel = s.scalar(select(func.count()).select_from(Relationship).where(Relationship.project_id == pid)) or 0
    recent = s.scalars(select(Record).where(Record.project_id == pid, Record.timestamp.is_not(None))
                       .order_by(Record.timestamp.desc()).limit(8)).all()
    syncs = s.scalars(select(SyncState).where(SyncState.project_id == pid)).all()
    return dict(by_kind=by_kind, by_category=by_cat, memories=mem, relationships=rel,
                recent=[record_dict(r) for r in recent],
                sync=[dict(source=x.source, status=x.status, last_run=x.last_run.isoformat() if x.last_run else None) for x in syncs])


def search_records(s: Session, pid: int, query: str, k: int = 15, kinds: set | None = None) -> list[tuple[float, Record]]:
    qv = embed_text(query)
    words = {w for w in re.findall(r"[a-z0-9_]{3,}", query.lower())}
    cands = None
    if s.bind is not None and s.bind.dialect.name == "postgresql":
        try:
            q = select(Record).where(Record.project_id == pid, Record.embedding.is_not(None))
            if kinds:
                q = q.where(Record.kind.in_(kinds))
            q = q.order_by(text("embedding <=> CAST(:qv AS vector)")).params(qv=json.dumps(qv)).limit(200)
            cands = list(s.scalars(q).all())
        except Exception:  # pgvector not usable -> python fallback
            s.rollback()
    if cands is None:
        q = select(Record).where(Record.project_id == pid)
        if kinds:
            q = q.where(Record.kind.in_(kinds))
        cands = list(s.scalars(q.order_by(Record.timestamp.desc().nullslast()).limit(5000)).all())
    scored = []
    for r in cands:
        sc = cosine(qv, r.embedding) if r.embedding else 0.0
        hay = f"{r.title} {r.content}".lower()
        sc += min(0.4, 0.08 * sum(1 for w in words if w in hay))
        scored.append((sc, r))
    scored.sort(key=lambda x: -x[0])
    return [x for x in scored[:k] if x[0] > 0.05]
