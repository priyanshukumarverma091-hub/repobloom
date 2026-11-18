"""Validation + cleaning + sanitization of raw collected data."""
import re
import unicodedata

from collectors.base import RawRecord

MAX_CONTENT = 20000
MAX_TITLE = 300

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\b[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27,}\b"), "[REDACTED_DISCORD_TOKEN]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]{20,}=*"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
     r"\1=[REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
]

INJECTION_PATTERNS = re.compile(
    r"(ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)|"
    r"disregard (all |any )?(the )?(previous|prior|above)|"
    r"you are now|new instructions:|system prompt|</?untrusted|"
    r"reveal (your|the) (prompt|instructions)|act as (an?|the) )", re.I)

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAG = re.compile(r"<[^>\n]{1,200}>")


def redact(text: str) -> str:
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text


def clean_text(text: str, strip_html: bool = False) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _CTRL.sub("", text)
    if strip_html:
        text = _TAG.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return redact(text).strip()


def detect_injection(text: str) -> bool:
    return bool(INJECTION_PATTERNS.search(text or ""))


class ValidationError(ValueError):
    pass


def validate(raw: RawRecord) -> None:
    if raw.source not in ("github", "discord", "docs"):
        raise ValidationError(f"unknown source {raw.source!r}")
    if not raw.kind or not str(raw.external_id):
        raise ValidationError("missing kind/external_id")
    if not (raw.title or raw.content):
        raise ValidationError("empty record")


def clean(raw: RawRecord) -> RawRecord:
    """Validate then return a sanitized copy; records suspicious content in meta."""
    validate(raw)
    strip_html = raw.source == "docs"
    content = clean_text(raw.content, strip_html=strip_html)[:MAX_CONTENT]
    title = clean_text(raw.title)[:MAX_TITLE]
    meta = dict(raw.meta or {})
    if detect_injection(content) or detect_injection(title):
        meta["injection_suspected"] = True
    return RawRecord(raw.source, raw.kind, str(raw.external_id), title, content, raw.url or "",
                     clean_text(raw.author)[:200], raw.timestamp, meta, dict(raw.permissions or {}))
