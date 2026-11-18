"""Normalize timestamps / fields into the unified record shape; plus a local text embedding."""
import hashlib
import math
import re
from datetime import datetime, timezone

from database.models import EMBED_DIM

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def parse_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def embed_text(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Signed feature-hashing bag-of-words embedding (unigrams + bigrams), L2 normalised.

    Lexical, deterministic and free. Swap for a neural embedding model later without schema
    changes as long as the dimension stays EMBED_DIM.
    """
    toks = [t.lower() for t in _WORD.findall(text[:6000])]
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    vec = [0.0] * dim
    for f in feats:
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [round(v / norm, 5) for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
