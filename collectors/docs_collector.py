"""Documentation collector: Trafilatura (+ BeautifulSoup fallback). Honors robots.txt, noindex,
conditional requests and stores only bounded excerpts with source URL + licence info."""
import asyncio
import logging
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from collectors.base import CollectResult, RawRecord

log = logging.getLogger(__name__)
MAX_EXCERPT = 8000


def _is_safe_url(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host:
        return False
    blocked = ("localhost", "127.", "0.0.0.0", "10.", "192.168.", "169.254.", "172.16.", "172.17.", "172.18.",
               "172.19.", "172.2", "172.30.", "172.31.", "[::1]")
    return not (host == "localhost" or host.startswith(blocked[1:]) or host.endswith(".internal"))


def extract_page(html: str, url: str) -> dict:
    import trafilatura
    text = trafilatura.extract(html, include_comments=False, include_tables=True, favor_precision=True, url=url) or ""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else "") or url
    if len(text) < 200:  # trafilatura found little: fall back to BeautifulSoup
        for t in soup(["script", "style", "nav", "footer", "header", "aside"]):
            t.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = " ".join(main.get_text(" ", strip=True).split())
    lic = None
    link = soup.find("link", rel=lambda v: v and "license" in v)
    if link and link.get("href"):
        lic = urljoin(url, link["href"])
    for name in ("license", "dc.rights", "copyright"):
        m = soup.find("meta", attrs={"name": name})
        if m and m.get("content"):
            lic = lic or m["content"]
    robots_meta = (soup.find("meta", attrs={"name": "robots"}) or {}).get("content", "") if soup else ""
    return {"title": title[:300], "text": text, "license": lic, "robots_meta": robots_meta.lower()}


class DocsCollector:
    def __init__(self, user_agent: str = "RepoBloomBot/1.0", client: httpx.AsyncClient | None = None):
        self.ua, self._client = user_agent, client

    async def _allowed(self, client, url) -> bool:
        p = urlparse(url)
        try:
            r = await client.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=10, headers={"User-Agent": self.ua})
            if r.status_code >= 400:
                return True  # no robots.txt -> allowed
            rp = RobotFileParser()
            rp.parse(r.text.splitlines())
            return rp.can_fetch(self.ua, url)
        except httpx.HTTPError:
            return True

    async def collect(self, urls: list[str], cursor: dict | None = None) -> CollectResult:
        cursor = dict(cursor or {})
        res = CollectResult(source="docs", cursor=cursor)
        own = self._client is None
        client = self._client or httpx.AsyncClient(follow_redirects=True)
        notes = []
        try:
            for url in urls:
                if not _is_safe_url(url):
                    notes.append(f"skipped (unsafe/internal URL): {url}")
                    continue
                try:
                    if not await self._allowed(client, url):
                        notes.append(f"blocked by robots.txt: {url}")
                        continue
                    h = {"User-Agent": self.ua}
                    prev = cursor.get(url, {})
                    if prev.get("etag"):
                        h["If-None-Match"] = prev["etag"]
                    if prev.get("last_modified"):
                        h["If-Modified-Since"] = prev["last_modified"]
                    r = await client.get(url, headers=h, timeout=30)
                    if r.status_code == 304:
                        continue
                    if r.status_code >= 400:
                        notes.append(f"{url}: HTTP {r.status_code}")
                        continue
                    if "noindex" in r.headers.get("x-robots-tag", "").lower():
                        notes.append(f"skipped (X-Robots-Tag noindex): {url}")
                        continue
                    ctype = r.headers.get("content-type", "")
                    if "html" not in ctype and "text" not in ctype:
                        notes.append(f"skipped (content-type {ctype}): {url}")
                        continue
                    page = await asyncio.to_thread(extract_page, r.text, url)
                    if "noindex" in page["robots_meta"] or "noai" in page["robots_meta"]:
                        notes.append(f"skipped (robots meta): {url}")
                        continue
                    if not page["text"]:
                        notes.append(f"no extractable text: {url}")
                        continue
                    host = urlparse(url).netloc
                    res.records.append(RawRecord(
                        "docs", "doc", url, page["title"], page["text"][:MAX_EXCERPT], url, host, None,
                        {"host": host, "truncated": len(page["text"]) > MAX_EXCERPT},
                        {"license": page["license"] or "unspecified", "copyright": "excerpt stored for internal analysis; "
                         "attribution via source URL", "robots_respected": True}))
                    cursor[url] = {"etag": r.headers.get("etag"), "last_modified": r.headers.get("last-modified")}
                except httpx.HTTPError as e:
                    notes.append(f"{url}: network error {type(e).__name__}")
        finally:
            if own:
                await client.aclose()
        res.cursor = cursor
        res.message = "; ".join(notes)
        if notes and not res.records and urls:
            res.status = "partial"
        res.stats = {"records": len(res.records), "skipped": len(notes)}
        return res
