import asyncio
import json
import os
import subprocess

import httpx

from collectors.base import RateLimited
from collectors.discord_collector import DiscordCollector
from collectors.docs_collector import DocsCollector
from collectors.github_collector import GitHubCollector, collect_local_git


def J(data, status=200, headers=None):
    return httpx.Response(status, json=data, headers=headers or {})


def github_handler(log, limit_on=None):
    def h(req: httpx.Request):
        p = req.url.path
        log.append((p, dict(req.url.params)))
        if limit_on and p.endswith(limit_on):
            return J({"message": "rate"}, 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"})
        if p == "/repos/o/r":
            return J({"id": 1, "private": False, "license": {"spdx_id": "MIT"}, "default_branch": "main", "owner": {"login": "o"},
                      "html_url": "https://github.com/o/r", "description": "d", "pushed_at": "2026-01-02T00:00:00Z"})
        if p == "/repos/o/r/commits":
            return J([{"sha": "a" * 40, "html_url": "u", "author": {"login": "bob"},
                       "commit": {"message": "Fixes #1 crash\n\nbody", "committer": {"date": "2026-01-02T00:00:00Z"}}}])
        if p.startswith("/repos/o/r/commits/"):
            return J({"files": [{"filename": "a.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ x"}]})
        if p == "/repos/o/r/issues":
            return J([{"number": 1, "title": "Crash", "body": "TypeError boom", "state": "closed", "labels": [{"name": "bug"}], "user": {"login": "al"},
                       "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-03T00:00:00Z", "html_url": "u", "comments": 0,
                       "closed_at": "2026-01-03T00:00:00Z", "comments_url": "https://api.github.com/repos/o/r/issues/1/comments"},
                      {"number": 2, "title": "a PR", "pull_request": {}, "updated_at": "2026-01-03T00:00:00Z"}])
        if p == "/repos/o/r/issues/1/events":
            return J([{"event": "closed", "commit_id": "a" * 40}])
        if p == "/repos/o/r/pulls":
            return J([{"number": 5, "title": "PR", "body": "Closes #1", "state": "closed", "merged_at": "x", "user": {"login": "bob"},
                       "created_at": "2026-01-02T00:00:00Z", "updated_at": "2026-01-02T01:00:00Z", "html_url": "u", "head": {"ref": "f"}, "base": {"ref": "main"}, "labels": []}])
        if p == "/repos/o/r/pulls/5/commits":
            return J([{"sha": "a" * 40}])
        if p == "/repos/o/r/pulls/5/reviews":
            return J([{"id": 9, "state": "APPROVED", "body": "ok", "user": {"login": "al"}, "submitted_at": "2026-01-02T02:00:00Z", "html_url": "u"}])
        if p == "/repos/o/r/branches":
            return J([{"name": "main"}, {"name": "dev"}])
        if p == "/repos/o/r/contributors":
            return J([{"login": "bob", "contributions": 3}])
        if p == "/repos/o/r/git/trees/main":
            return J({"sha": "t1", "tree": [{"path": "a.py", "type": "blob"}, {"path": "d", "type": "tree"}]})
        return J({}, 404)
    return h


def run(coro):
    return asyncio.run(coro)


def test_github_full_and_incremental():
    log = []
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(github_handler(log))) as c:
            col = GitHubCollector("tok", client=c)
            r1 = await col.collect("o/r")
            r2 = await col.collect("o/r", r1.cursor)
            return r1, r2
    r1, r2 = run(go())
    kinds = {x.kind for x in r1.records}
    assert {"repo", "commit", "issue", "pr", "review", "branch", "contributor", "file_tree"} <= kinds
    issue = next(x for x in r1.records if x.kind == "issue")
    assert issue.meta["closed_by_commit"] == "a" * 40 and "pull_request" not in str(issue.meta)
    assert r1.records[0].permissions["visibility"] == "public"
    # second sync is incremental: since= params sent, unchanged tree skipped
    assert r1.cursor["commits_since"] == "2026-01-02T00:00:00Z" and r1.cursor["tree_sha"] == "t1"
    commit_calls = [pr for p, pr in log if p == "/repos/o/r/commits"]
    assert commit_calls[1].get("since") == "2026-01-02T00:00:00Z"
    assert not any(x.kind == "file_tree" for x in r2.records)


def test_github_rate_limit_partial_and_errors():
    async def go(handler, repo="o/r"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await GitHubCollector("", client=c).collect(repo)
    r = run(go(github_handler([], limit_on="/issues")))
    assert r.status == "partial" and any(x.kind == "commit" for x in r.records)
    r = run(go(lambda req: J({}, 404)))
    assert r.status == "error" and "not found" in r.message


def test_discord_incremental_and_privacy():
    calls = []
    msgs = [{"id": "11", "content": "hello #1", "author": {"id": "5", "username": "zed"}, "timestamp": "2026-01-01T00:00:00+00:00"},
            {"id": "12", "content": "bot msg", "author": {"id": "6", "username": "bot", "bot": True}, "timestamp": "2026-01-01T00:01:00+00:00"},
            {"id": "13", "content": "", "author": {"id": "5", "username": "zed"}, "timestamp": "2026-01-01T00:02:00+00:00"}]

    def h(req):
        calls.append(dict(req.url.params))
        if req.url.path == "/api/v10/channels/99":
            return J({"name": "dev", "guild_id": "7"})
        if req.url.path == "/api/v10/channels/99/messages":
            return J([] if req.url.params.get("after") == "13" else list(reversed(msgs)))
        return J({}, 403)

    async def go(chs, cur=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await DiscordCollector("tok", True, client=c).collect(chs, cur)
    r = run(go(["99", "55"]))
    assert len(r.records) == 1 and r.records[0].author.startswith("user-") and "zed" not in r.records[0].author
    assert r.records[0].permissions["anonymized"] is True and r.cursor["99"] == "13"
    assert r.status == "partial" and "55" in r.message          # forbidden channel reported, others kept
    r2 = run(go(["99"], r.cursor))
    assert r2.records == [] and calls[-1]["after"] == "13"


def test_docs_robots_and_extraction():
    page = "<html><head><title>API Guide</title><link rel='license' href='/lic'></head><body><main>" + ("<p>Use the retry decorator carefully. </p>" * 30) + "</main></body></html>"

    def h(req):
        u = str(req.url)
        if u.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        if "/private/" in u:
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html", "etag": "e1"})

    async def go(urls, cur=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
            return await DocsCollector("bot", client=c).collect(urls, cur)
    r = run(go(["https://docs.x.io/guide", "https://docs.x.io/private/a", "http://localhost/admin", "http://169.254.169.254/x"]))
    assert len(r.records) == 1 and r.records[0].title == "API Guide" and "retry decorator" in r.records[0].content
    assert r.records[0].permissions["license"].endswith("/lic") and r.cursor["https://docs.x.io/guide"]["etag"] == "e1"
    assert "robots.txt" in r.message and "unsafe" in r.message


def test_local_git_incremental(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="Ann", GIT_AUTHOR_EMAIL="a@x.io", GIT_COMMITTER_NAME="Ann", GIT_COMMITTER_EMAIL="a@x.io")
    sh = lambda *a: subprocess.run(a, cwd=repo, env=env, check=True, capture_output=True)
    sh("git", "init", "-q")
    (repo / "m.py").write_text("print(1)\n")
    sh("git", "add", ".")
    sh("git", "commit", "-qm", "Initial: fixes #3")
    r1 = collect_local_git(str(repo), str(tmp_path / "w"))
    assert r1.status == "ok" and [x.kind for x in r1.records].count("commit") == 1
    assert next(x for x in r1.records if x.kind == "commit").meta["files"] == ["m.py"]
    (repo / "n.py").write_text("x=2\n")
    sh("git", "add", ".")
    sh("git", "commit", "-qm", "second")
    r2 = collect_local_git(str(repo), str(tmp_path / "w"), r1.cursor)
    assert [x.kind for x in r2.records].count("commit") == 1
