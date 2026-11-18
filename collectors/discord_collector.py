"""Discord collector: Bot API, only channels the bot is explicitly configured for.

Respects permissions (bot must be invited and have Read Message History), skips bot authors,
supports pseudonymising authors, and syncs incrementally using the last message id per channel.
NOTE: reading message text requires the *Message Content* privileged intent in the Developer Portal.
"""
import asyncio
import hashlib
import logging

import httpx

from collectors.base import CollectorError, CollectResult, RateLimited, RawRecord

log = logging.getLogger(__name__)
API = "https://discord.com/api/v10"
MAX_PAGES_PER_CHANNEL = 10


class DiscordCollector:
    def __init__(self, token: str, anonymize: bool = True, client: httpx.AsyncClient | None = None,
                 base_url: str = API):
        self.token, self.anonymize, self._client, self.base = token, anonymize, client, base_url.rstrip("/")

    def _who(self, author: dict) -> str:
        name = author.get("global_name") or author.get("username") or "unknown"
        if not self.anonymize:
            return name
        return "user-" + hashlib.sha256(str(author.get("id", name)).encode()).hexdigest()[:8]

    async def _get(self, client, path, params=None):
        for attempt in range(4):
            try:
                r = await client.get(f"{self.base}{path}", params=params, timeout=30,
                                     headers={"Authorization": f"Bot {self.token}", "User-Agent": "RepoBloom (bot, 1.0)"})
            except httpx.HTTPError as e:
                if attempt == 3:
                    raise CollectorError(f"network error: {e}") from e
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                wait = float(r.json().get("retry_after", r.headers.get("retry-after", 1)))
                if wait > 30:
                    raise RateLimited(wait, "Discord rate limit")
                await asyncio.sleep(wait)
                continue
            if r.status_code == 401:
                raise CollectorError("Discord bot token invalid")
            if r.status_code in (403, 404):
                raise CollectorError(f"no access to {path} (bot not in server/channel or missing permissions)")
            if r.status_code >= 500:
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise CollectorError(f"Discord API {r.status_code}")
            return r.json()
        raise CollectorError("Discord API unavailable")

    async def collect(self, channel_ids: list[str], cursor: dict | None = None) -> CollectResult:
        cursor = dict(cursor or {})
        res = CollectResult(source="discord", cursor=cursor)
        if not self.token:
            res.status, res.message = "error", "DISCORD_BOT_TOKEN not configured"
            return res
        own = self._client is None
        client = self._client or httpx.AsyncClient()
        errors = []
        try:
            for ch in channel_ids:
                ch = str(ch).strip()
                if not ch.isdigit():
                    errors.append(f"{ch}: invalid channel id")
                    continue
                try:
                    info = await self._get(client, f"/channels/{ch}")
                    chan_name = info.get("name", ch)
                    guild = info.get("guild_id", "@me")
                    after = cursor.get(ch)
                    newest = after
                    for _ in range(MAX_PAGES_PER_CHANNEL):
                        params = {"limit": 100}
                        if after:
                            params["after"] = after
                        batch = await self._get(client, f"/channels/{ch}/messages", params)
                        if not batch:
                            break
                        batch.sort(key=lambda m: int(m["id"]))
                        for m in batch:
                            if (m.get("author") or {}).get("bot") or not (m.get("content") or "").strip():
                                continue
                            res.records.append(RawRecord(
                                "discord", "message", m["id"], f"#{chan_name}: {m['content'][:80]}", m["content"],
                                f"https://discord.com/channels/{guild}/{ch}/{m['id']}", self._who(m["author"]),
                                m.get("timestamp"),
                                {"channel_id": ch, "channel": chan_name, "reply_to": (m.get("message_reference") or {}).get("message_id")},
                                {"visibility": "authorized_channel", "anonymized": self.anonymize, "restricted": True}))
                        after = newest = batch[-1]["id"]
                        if len(batch) < 100:
                            break
                    if newest:
                        cursor[ch] = newest
                except RateLimited as e:
                    res.status = "partial"
                    errors.append(str(e))
                    break
                except CollectorError as e:
                    errors.append(f"{ch}: {e}")
        finally:
            if own:
                await client.aclose()
        if errors:
            res.message = "; ".join(errors)
            if res.status == "ok":
                res.status = "partial" if res.records else "error"
        res.stats = {"records": len(res.records), "channels": len(channel_ids)}
        return res
