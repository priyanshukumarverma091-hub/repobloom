"""GitHub collector: REST API (async httpx) with incremental sync + GitPython local mode."""
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from collectors.base import CollectorError, CollectResult, RateLimited, RawRecord

log = logging.getLogger(__name__)
API = "https://api.github.com"
MAX_PAGES = 5          # per endpoint per sync (500 items); next sync continues incrementally


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GitHubCollector:
    def __init__(self, token: str = "", max_commit_details: int = 40,
                 client: httpx.AsyncClient | None = None, base_url: str = API):
        self.token = token
        self.max_commit_details = max_commit_details
        self._client = client
        self.base = base_url.rstrip("/")

    # ---------- HTTP ----------
    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "RepoBloom"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None):
        url = path if path.startswith("http") else f"{self.base}{path}"
        for attempt in range(3):
            try:
                r = await client.get(url, params=params, headers=self._headers(), timeout=30)
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise CollectorError(f"network error: {e}") from e
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code in (403, 429) and (r.headers.get("x-ratelimit-remaining") == "0" or r.status_code == 429):
                reset = r.headers.get("x-ratelimit-reset")
                wait = float(r.headers.get("retry-after") or (max(0, int(reset) - datetime.now().timestamp()) if reset else 60))
                raise RateLimited(wait, "GitHub rate limit reached")
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                raise CollectorError("repository not found or token lacks access")
            if r.status_code == 401:
                raise CollectorError("GitHub token invalid")
            if r.status_code >= 400:
                raise CollectorError(f"GitHub API {r.status_code}: {r.text[:200]}")
            return r
        raise CollectorError("GitHub API unavailable")

    async def _paged(self, client, path, params=None, max_pages=MAX_PAGES):
        params = dict(params or {}, per_page=100)
        url, pages = f"{self.base}{path}", 0
        while url and pages < max_pages:
            r = await self._get(client, url, params if pages == 0 else None)
            data = r.json()
            if not isinstance(data, list):
                break
            for item in data:
                yield item
            url = r.links.get("next", {}).get("url")
            pages += 1

    # ---------- main entry ----------
    async def collect(self, repo: str, cursor: dict | None = None) -> CollectResult:
        cursor = dict(cursor or {})
        res = CollectResult(source="github", cursor=cursor)
        own = self._client is None
        client = self._client or httpx.AsyncClient()
        started = _now()
        try:
            info = (await self._get(client, f"/repos/{repo}")).json()
            private = bool(info.get("private"))
            perms = {"visibility": "private" if private else "public",
                     "license": (info.get("license") or {}).get("spdx_id"),
                     "restricted": private}
            default_branch = info.get("default_branch", "main")
            res.records.append(RawRecord("github", "repo", f"repo-{info.get('id', repo)}", repo,
                                         info.get("description") or "", info.get("html_url", ""),
                                         (info.get("owner") or {}).get("login", ""), info.get("pushed_at"),
                                         {"default_branch": default_branch, "language": info.get("language"),
                                          "topics": info.get("topics", []), "stars": info.get("stargazers_count")}, perms))
            steps = [self._commits, self._issues, self._pulls, self._branches, self._contributors, self._tree]
            for step in steps:
                try:
                    await step(client, repo, default_branch, perms, cursor, res)
                except RateLimited as e:
                    res.status, res.message = "partial", f"{e} - retry in ~{int(e.retry_after)}s; progress saved"
                    break
            res.cursor = cursor
            cursor["last_sync_started"] = started
        except RateLimited as e:
            res.status, res.message = "partial", f"{e} - retry in ~{int(e.retry_after)}s"
        except CollectorError as e:
            res.status, res.message = "error", str(e)
        finally:
            if own:
                await client.aclose()
        res.stats = {"records": len(res.records)}
        return res

    # ---------- resources ----------
    async def _commits(self, client, repo, branch, perms, cursor, res):
        params = {"since": cursor["commits_since"]} if cursor.get("commits_since") else {}
        newest = cursor.get("commits_since")
        details = 0
        async for c in self._paged(client, f"/repos/{repo}/commits", params):
            sha = c["sha"]
            info = c.get("commit", {})
            msg = info.get("message", "")
            when = (info.get("committer") or info.get("author") or {}).get("date")
            files, patch_txt = [], ""
            if details < self.max_commit_details:
                details += 1
                try:
                    d = (await self._get(client, f"/repos/{repo}/commits/{sha}")).json()
                    for f in d.get("files", [])[:60]:
                        files.append(f["filename"])
                        patch_txt += f"\n{f['status']} {f['filename']} (+{f['additions']}/-{f['deletions']})"
                        if f.get("patch"):
                            patch_txt += "\n" + f["patch"][:500]
                except CollectorError:
                    pass
            author = (c.get("author") or {}).get("login") or (info.get("author") or {}).get("name", "")
            res.records.append(RawRecord("github", "commit", sha, msg.splitlines()[0][:200] if msg else sha[:8],
                                         msg + ("\n\nChanged files:" + patch_txt if patch_txt else ""),
                                         c.get("html_url", ""), author, when,
                                         {"files": files, "sha": sha}, perms))
            if when and (not newest or when > newest):
                newest = when
        if newest:
            cursor["commits_since"] = newest

    async def _issues(self, client, repo, branch, perms, cursor, res):
        params = {"state": "all", "sort": "updated", "direction": "asc"}
        if cursor.get("issues_since"):
            params["since"] = cursor["issues_since"]
        newest = cursor.get("issues_since")
        comment_budget = 25
        async for i in self._paged(client, f"/repos/{repo}/issues", params):
            if "pull_request" in i:
                continue
            labels = [l["name"] for l in i.get("labels", [])]
            body = i.get("body") or ""
            if comment_budget and i.get("comments", 0) > 0:
                comment_budget -= 1
                try:
                    cm = (await self._get(client, i["comments_url"], {"per_page": 20})).json()
                    body += "\n\n--- Comments ---\n" + "\n".join(f"{(x.get('user') or {}).get('login')}: {x.get('body', '')[:600]}" for x in cm)
                except CollectorError:
                    pass
            meta = {"number": i["number"], "state": i["state"], "labels": labels,
                    "closed_at": i.get("closed_at"), "comments": i.get("comments", 0)}
            if i["state"] == "closed" and i.get("closed_at"):
                try:
                    ev = (await self._get(client, f"/repos/{repo}/issues/{i['number']}/events", {"per_page": 50})).json()
                    for e in ev:
                        if e.get("event") == "closed" and e.get("commit_id"):
                            meta["closed_by_commit"] = e["commit_id"]
                except CollectorError:
                    pass
            res.records.append(RawRecord("github", "issue", f"issue-{i['number']}", i["title"], body,
                                         i.get("html_url", ""), (i.get("user") or {}).get("login", ""),
                                         i.get("created_at"), meta, perms))
            u = i.get("updated_at")
            if u and (not newest or u > newest):
                newest = u
        if newest:
            cursor["issues_since"] = newest

    async def _pulls(self, client, repo, branch, perms, cursor, res):
        since = cursor.get("pulls_since")
        newest = since
        reviews_left = 20
        async for p in self._paged(client, f"/repos/{repo}/pulls", {"state": "all", "sort": "updated", "direction": "desc"}):
            u = p.get("updated_at")
            if since and u and u <= since:
                break
            shas = []
            try:
                shas = [c["sha"] for c in (await self._get(client, f"/repos/{repo}/pulls/{p['number']}/commits", {"per_page": 100})).json()]
            except CollectorError:
                pass
            res.records.append(RawRecord("github", "pr", f"pr-{p['number']}", p["title"], p.get("body") or "",
                                         p.get("html_url", ""), (p.get("user") or {}).get("login", ""),
                                         p.get("created_at"),
                                         {"number": p["number"], "state": p["state"], "merged": bool(p.get("merged_at")),
                                          "merged_at": p.get("merged_at"), "head": (p.get("head") or {}).get("ref"),
                                          "base": (p.get("base") or {}).get("ref"), "commit_shas": shas,
                                          "labels": [l["name"] for l in p.get("labels", [])]}, perms))
            if reviews_left:
                reviews_left -= 1
                try:
                    for rv in (await self._get(client, f"/repos/{repo}/pulls/{p['number']}/reviews", {"per_page": 30})).json():
                        if not (rv.get("body") or "").strip() and rv.get("state") == "COMMENTED":
                            continue
                        res.records.append(RawRecord("github", "review", f"review-{rv['id']}",
                                                     f"Review of PR #{p['number']} ({rv.get('state')})", rv.get("body") or rv.get("state", ""),
                                                     rv.get("html_url", ""), (rv.get("user") or {}).get("login", ""),
                                                     rv.get("submitted_at"), {"pr_number": p["number"], "state": rv.get("state")}, perms))
                except CollectorError:
                    pass
            if u and (not newest or u > newest):
                newest = u
        if newest:
            cursor["pulls_since"] = newest

    async def _branches(self, client, repo, branch, perms, cursor, res):
        names = []
        async for b in self._paged(client, f"/repos/{repo}/branches", max_pages=2):
            names.append(b["name"])
        key = ",".join(sorted(names))
        if names and cursor.get("branches") != key:
            res.records.append(RawRecord("github", "branch", f"branches-{repo}", f"{len(names)} branches",
                                         "Branches: " + ", ".join(sorted(names)), f"https://github.com/{repo}/branches",
                                         "", _now(), {"names": names}, perms))
            cursor["branches"] = key

    async def _contributors(self, client, repo, branch, perms, cursor, res):
        rows = []
        async for c in self._paged(client, f"/repos/{repo}/contributors", max_pages=1):
            rows.append((c.get("login"), c.get("contributions", 0)))
        if rows:
            res.records.append(RawRecord("github", "contributor", f"contributors-{repo}", f"{len(rows)} contributors",
                                         "\n".join(f"{n}: {c} contributions" for n, c in rows),
                                         f"https://github.com/{repo}/graphs/contributors", "", _now(),
                                         {"contributors": dict(rows)}, perms))

    async def _tree(self, client, repo, branch, perms, cursor, res):
        r = (await self._get(client, f"/repos/{repo}/git/trees/{branch}", {"recursive": "1"})).json()
        sha = r.get("sha")
        if sha and sha == cursor.get("tree_sha"):
            return
        paths = [t["path"] for t in r.get("tree", []) if t.get("type") == "blob"]
        res.records.append(RawRecord("github", "file_tree", f"tree-{repo}", f"Repository structure ({len(paths)} files)",
                                     "\n".join(paths[:1500]), f"https://github.com/{repo}/tree/{branch}", "", _now(),
                                     {"paths": paths[:3000], "truncated": bool(r.get("truncated"))}, perms))
        cursor["tree_sha"] = sha


# ---------- GitPython (no API quota; public/local repos) ----------
def collect_local_git(path_or_url: str, workdir: str, cursor: dict | None = None, max_commits: int = 500) -> CollectResult:
    """Read commit history + file structure using GitPython. Incremental via last seen SHA."""
    import os
    import tempfile
    from git import Repo
    cursor = dict(cursor or {})
    res = CollectResult(source="github", cursor=cursor)
    try:
        if os.path.isdir(path_or_url):
            repo = Repo(path_or_url)
        else:
            os.makedirs(workdir, exist_ok=True)
            dest = os.path.join(workdir, "".join(c if c.isalnum() else "_" for c in path_or_url)[-80:])
            repo = Repo(dest) if os.path.isdir(os.path.join(dest, ".git")) else Repo.clone_from(path_or_url, dest)
            if repo.remotes:
                repo.remotes.origin.fetch()
        perms = {"visibility": "unknown", "license": None, "restricted": False}
        last = cursor.get("last_sha")
        rev = f"{last}..HEAD" if last and any(True for _ in repo.iter_commits("HEAD", max_count=1)) else "HEAD"
        try:
            commits = list(repo.iter_commits(rev, max_count=max_commits))
        except Exception:
            commits = list(repo.iter_commits("HEAD", max_count=max_commits))
        for c in commits:
            files = list(c.stats.files.keys())[:60]
            stat = "\n".join(f"{f} (+{v['insertions']}/-{v['deletions']})" for f, v in list(c.stats.files.items())[:60])
            res.records.append(RawRecord(
                "github", "commit", c.hexsha, c.message.splitlines()[0][:200] if c.message else c.hexsha[:8],
                c.message + ("\n\nChanged files:\n" + stat if stat else ""), "", c.author.name,
                c.committed_datetime.isoformat(), {"files": files, "sha": c.hexsha}, perms))
        if commits:
            cursor["last_sha"] = commits[0].hexsha
        paths = [b.path for b in repo.head.commit.tree.traverse() if b.type == "blob"]
        res.records.append(RawRecord("github", "file_tree", f"tree-{os.path.basename(str(repo.working_dir))}",
                                     f"Repository structure ({len(paths)} files)", "\n".join(paths[:1500]), "", "",
                                     _now(), {"paths": paths[:3000]}, perms))
        res.stats = {"commits": len(commits), "files": len(paths)}
    except Exception as e:  # GitPython raises many types
        res.status, res.message = "error", f"git error: {e}"
    return res
