import re

ISSUE_REF = re.compile(r"(?<![\w/])#(\d{1,6})\b")
CLOSES_REF = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+(?:[\w.-]+/[\w.-]+)?#(\d{1,6})\b", re.I)
SHA_REF = re.compile(r"\b([0-9a-f]{7,40})\b")
PATH_REF = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,6}|[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|php|cs|cpp|c|h|md|json|ya?ml|toml|sql|html|css|sh))\b")
ERROR_REF = re.compile(r"\b([A-Z][A-Za-z]+(?:Error|Exception))\b")
URL_REF = re.compile(r"https?://[^\s)>\]]+")
TODO_REF = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.{0,120})")


def extract(title: str, content: str, extra: dict | None = None) -> dict:
    text = f"{title}\n{content}"
    shas = [s for s in SHA_REF.findall(text) if re.search(r"\d", s) and re.search(r"[a-f]", s)]
    meta = {
        "issue_refs": sorted({int(n) for n in ISSUE_REF.findall(text)})[:30],
        "closes_refs": sorted({int(n) for n in CLOSES_REF.findall(text)})[:30],
        "sha_refs": sorted(set(shas))[:20],
        "file_refs": sorted(set(PATH_REF.findall(text)))[:40],
        "errors": sorted(set(ERROR_REF.findall(text)))[:10],
        "urls": sorted(set(URL_REF.findall(text)))[:15],
        "todos": [f"{a} {b.strip()}".strip() for a, b in TODO_REF.findall(text)][:10],
    }
    meta = {k: v for k, v in meta.items() if v}
    if extra:
        meta.update(extra)
    return meta
