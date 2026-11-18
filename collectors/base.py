from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawRecord:
    source: str                 # github | discord | docs
    kind: str
    external_id: str
    title: str = ""
    content: str = ""
    url: str = ""
    author: str = ""
    timestamp: datetime | str | None = None
    meta: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)


@dataclass
class CollectResult:
    source: str
    records: list[RawRecord] = field(default_factory=list)
    cursor: dict = field(default_factory=dict)
    status: str = "ok"          # ok | partial | error
    message: str = ""
    stats: dict = field(default_factory=dict)


class CollectorError(Exception):
    pass


class RateLimited(CollectorError):
    def __init__(self, retry_after: float, msg: str = "rate limited"):
        super().__init__(msg)
        self.retry_after = retry_after
