"""Demo project so the platform can be explored without any tokens. Records are clearly marked demo=True
and go through the exact same pipeline as real data."""
from datetime import datetime, timedelta, timezone

from collectors.base import RawRecord
from database.models import Project
from processors.pipeline import process_batch

D = lambda days, h=10: (datetime.now(timezone.utc) - timedelta(days=days)).replace(hour=h, minute=0, second=0, microsecond=0)
PERM = {"visibility": "demo", "license": "n/a", "restricted": False}
DPERM = {"visibility": "authorized_channel", "anonymized": True, "restricted": True}


def demo_records() -> list[RawRecord]:
    def gh(kind, ext, title, content, who, ts, meta=None):
        return RawRecord("github", kind, ext, title, content, f"https://github.com/demo/payments-api/{kind}/{ext}", who, ts,
                         dict(meta or {}, demo=True), PERM)

    def dc(ext, content, who, ts):
        return RawRecord("discord", "message", ext, f"#backend: {content[:70]}", content,
                         f"https://discord.com/channels/1/2/{ext}", who, ts, {"channel": "backend", "demo": True}, DPERM)
    paths = ["app.py", "config.py", "requirements.txt", "Dockerfile", "payments/__init__.py", "payments/retry.py", "payments/gateway.py",
             "payments/webhooks.py", "api/routes.py", "api/schemas.py", "db/models.py", "db/session.py", "docs/README.md",
             "frontend/index.html", "frontend/app.js", ".github/workflows/ci.yml", "tests/test_gateway.py", "tests/test_retry.py"]
    return [
        gh("file_tree", "tree", "Repository structure (18 files)", "\n".join(paths), "", D(1), {"paths": paths}),
        gh("issue", "issue-12", "Duplicate charges when gateway times out",
           "Customers are charged twice when the payment gateway times out and our retry logic re-sends the request. "
           "Traceback: GatewayTimeoutError in payments/gateway.py. We need an idempotency key. TODO: add idempotency header.",
           "asha", D(30), {"number": 12, "state": "closed", "labels": ["bug", "payments"], "closed_at": D(24).isoformat()}),
        dc("d1", "Seeing GatewayTimeoutError a lot in prod, same customer billed twice, see #12", "user-a1b2", D(29)),
        dc("d2", "Probably retry.py re-sending without a key. Stripe supports idempotency keys, can we use those?", "user-c3d4", D(29, 11)),
        dc("d3", "We decided to add idempotency keys per order id and keep retries at 3 with exponential backoff, instead of removing retries. "
                 "Going with that for #12.", "user-a1b2", D(28)),
        gh("commit", "9f2c1ab7e5d34c10aa11bb22cc33dd44ee55ff66", "Add idempotency key to gateway requests",
           "Fixes #12. Generate idempotency key from order id and send it on every gateway call; retries reuse the key.\n\nChanged files:",
           "ravi", D(25), {"files": ["payments/gateway.py", "payments/retry.py", "tests/test_gateway.py"]}),
        gh("pr", "pr-14", "Idempotent payments", "Closes #12. Implements the idempotency approach agreed in Discord. Adds tests.", "ravi", D(26),
           {"number": 14, "state": "closed", "merged": True, "head": "fix/idempotency", "base": "main",
            "commit_shas": ["9f2c1ab7e5d34c10aa11bb22cc33dd44ee55ff66"], "labels": []}),
        gh("review", "review-1", "Review of PR #14 (APPROVED)", "LGTM, nice use of the order id as key. Please document in README.", "asha", D(25, 9), {"pr_number": 14}),
        gh("commit", "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d", "Document idempotency in README", "Docs for idempotent payment retries.", "asha", D(24),
           {"files": ["docs/README.md"]}),
        gh("issue", "issue-17", "Webhook signature validation fails intermittently",
           "Webhook endpoint returns 401 for valid events sometimes. Suspect clock skew in payments/webhooks.py. SignatureVerificationError seen twice.",
           "mina", D(12), {"number": 17, "state": "open", "labels": ["bug"]}),
        dc("d4", "Anyone looked at the webhook 401s (#17)? Might be the tolerance window in payments/webhooks.py", "user-e5f6", D(11)),
        gh("issue", "issue-19", "Add refunds API endpoint", "We need a POST /refunds endpoint. Needs design for partial refunds. Next step: spec.",
           "ravi", D(9), {"number": 19, "state": "open", "labels": ["enhancement"]}),
        gh("issue", "issue-21", "Move CI to GitHub Actions caching", "Builds take 9 minutes. Cache pip dependencies.", "asha", D(8),
           {"number": 21, "state": "open", "labels": []}),
        gh("commit", "aa11bb22cc33dd44ee55ff66aa11bb22cc33dd44", "Refactor gateway client into class", "Cleanup of payments/gateway.py, no behaviour change.", "ravi", D(7),
           {"files": ["payments/gateway.py"]}),
        gh("pr", "pr-22", "WIP: refunds endpoint", "Starts #19. Partial refunds not done yet. TODO: validation of amounts.", "ravi", D(3),
           {"number": 22, "state": "open", "merged": False, "head": "feat/refunds", "base": "main", "commit_shas": [], "labels": []}),
        RawRecord("docs", "doc", "https://docs.example.com/payments/idempotency", "Idempotent requests - Example Payments Docs",
                  "Idempotent requests let you retry safely. Send an Idempotency-Key header; the server returns the original result for repeated keys. Keys expire after 24 hours.",
                  "https://docs.example.com/payments/idempotency", "docs.example.com", None, {"demo": True},
                  {"license": "CC-BY-4.0 (demo)", "robots_respected": True}),
    ]


def seed_demo(db) -> int:
    from services.ingestion_service import rebuild_derived
    with db.session() as s:
        p = Project(name="Demo: payments-api", github_repo=None, is_demo=True, discord_channel_ids=[], doc_urls=[])
        s.add(p)
        s.flush()
        process_batch(s, p.id, demo_records())
        rebuild_derived(s, p.id)
        return p.id
