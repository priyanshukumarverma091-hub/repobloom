import hashlib
import re


def content_hash(source: str, kind: str, title: str, content: str) -> str:
    norm = re.sub(r"\s+", " ", f"{source}|{kind}|{title}|{content}".lower()).strip()
    return hashlib.sha256(norm.encode()).hexdigest()


def _shingles(text: str, n: int = 5) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    if len(words) <= n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class BatchDeduplicator:
    """Exact duplicates (hash) for everything; near-duplicates (shingle Jaccard) for long docs."""

    def __init__(self, near_threshold: float = 0.92):
        self.hashes: set[str] = set()
        self.docs: list[set] = []
        self.near_threshold = near_threshold
        self.dropped = 0

    def is_duplicate(self, h: str, kind: str, content: str) -> bool:
        if h in self.hashes:
            self.dropped += 1
            return True
        if kind == "doc" and len(content) > 500:
            sh = _shingles(content)
            if any(jaccard(sh, d) >= self.near_threshold for d in self.docs):
                self.dropped += 1
                return True
            self.docs.append(sh)
        self.hashes.add(h)
        return False
