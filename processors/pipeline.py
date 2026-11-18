"""Unified processing pipeline:
Collection -> Validation -> Cleaning -> Duplicate detection -> Classification ->
Metadata extraction -> Normalization -> (embedding) -> Database storage.
Relationship mapping runs afterwards in the ingestion service.
"""
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from collectors.base import RawRecord
from database.models import Record
from processors import cleaner
from processors.classifier import classify
from processors.deduplicator import BatchDeduplicator, content_hash
from processors.metadata_extractor import extract
from processors.normalizer import embed_text, parse_ts

log = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    received: int = 0
    invalid: int = 0
    duplicates: int = 0
    unchanged: int = 0
    inserted: int = 0
    updated: int = 0
    errors: list = field(default_factory=list)

    def as_dict(self):
        d = self.__dict__.copy()
        d["errors"] = self.errors[:5]
        return d


def process_batch(session: Session, project_id: int, raws: list[RawRecord]) -> PipelineStats:
    st = PipelineStats()
    dedup = BatchDeduplicator()
    existing: dict[tuple[str, str], Record] = {}
    if raws:
        sources = {r.source for r in raws}
        rows = session.scalars(select(Record).where(Record.project_id == project_id,
                                                    Record.source.in_(sources))).all()
        existing = {(r.source, r.external_id): r for r in rows}

    for raw in raws:
        st.received += 1
        try:
            rec = cleaner.clean(raw)                                   # validation + cleaning
        except cleaner.ValidationError as e:
            st.invalid += 1
            st.errors.append(str(e))
            continue
        h = content_hash(rec.source, rec.kind, rec.title, rec.content)
        old = existing.get((rec.source, rec.external_id))
        if old is not None and old.content_hash == h:                  # already stored, unchanged
            st.unchanged += 1
            continue
        if dedup.is_duplicate(h, rec.kind, rec.content):               # duplicate detection
            st.duplicates += 1
            continue
        labels = rec.meta.get("labels") or []
        category, conf = classify(rec.kind, rec.title, rec.content, labels)   # classification
        meta = extract(rec.title, rec.content, rec.meta)                      # metadata extraction
        ts = parse_ts(rec.timestamp)                                          # normalization
        emb = embed_text(f"{rec.title}\n{rec.content}")
        if old is None:
            row = Record(project_id=project_id, source=rec.source, kind=rec.kind,
                         external_id=rec.external_id, title=rec.title, content=rec.content,
                         url=rec.url, author=rec.author, timestamp=ts, category=category,
                         category_confidence=conf, meta=meta, permissions=rec.permissions,
                         content_hash=h, embedding=emb)
            session.add(row)
            existing[(rec.source, rec.external_id)] = row
            st.inserted += 1
        else:
            old.title, old.content, old.url, old.author = rec.title, rec.content, rec.url, rec.author
            old.timestamp, old.category, old.category_confidence = ts or old.timestamp, category, conf
            old.meta, old.permissions, old.content_hash, old.embedding = meta, rec.permissions, h, emb
            st.updated += 1
    session.flush()
    return st
