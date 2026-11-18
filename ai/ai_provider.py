"""Provider abstraction. OpenRouter today; BloomCore (or any provider) later = one more subclass
registered in ModelRouter, with zero changes in the Intelligence Engine."""
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from config import ModelConfig

log = logging.getLogger(__name__)


@dataclass
class ProviderResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class ProviderError(Exception):
    def __init__(self, msg: str, retryable: bool = False):
        super().__init__(msg)
        self.retryable = retryable


class RateLimitError(ProviderError):
    def __init__(self, msg: str, retry_after: float = 5.0):
        super().__init__(msg, retryable=True)
        self.retry_after = retry_after


class BaseProvider(ABC):
    name = "base"

    @abstractmethod
    async def complete(self, cfg: ModelConfig, messages: list[dict], *, json_mode: bool = True) -> ProviderResponse: ...


def estimate_cost(cfg: ModelConfig, prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens * cfg.price_in + completion_tokens * cfg.price_out) / 1_000_000


class OpenRouterProvider(BaseProvider):
    name = "openrouter"

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1", timeout: float = 90,
                 max_retries: int = 3, client: httpx.AsyncClient | None = None):
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.timeout, self.max_retries, self._client = timeout, max_retries, client

    async def complete(self, cfg, messages, *, json_mode=True) -> ProviderResponse:
        if not self.api_key:
            raise ProviderError("OPENROUTER_API_KEY not configured")
        if not cfg.model:
            raise ProviderError(f"model for role '{cfg.role}' not configured")
        body = {"model": cfg.model, "messages": messages, "max_tokens": cfg.max_tokens,
                "temperature": 0.2, "usage": {"include": True}}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "HTTP-Referer": "https://repobloom.app", "X-Title": "RepoBloom"}
        own = self._client is None
        client = self._client or httpx.AsyncClient()
        last: Exception | None = None
        try:
            for attempt in range(self.max_retries + 1):
                t0 = time.perf_counter()
                try:
                    r = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers,
                                          timeout=self.timeout)
                    if r.status_code == 429:
                        ra = min(float(r.headers.get("retry-after", 2 ** attempt)), 30)
                        raise RateLimitError("rate limited by OpenRouter", ra)
                    if r.status_code in (400, 422) and "response_format" in body and "response_format" in r.text:
                        body.pop("response_format")          # model lacks JSON mode: retry without it
                        continue
                    if r.status_code >= 500:
                        raise ProviderError(f"OpenRouter {r.status_code}", retryable=True)
                    if r.status_code in (401, 403):
                        raise ProviderError("OpenRouter rejected the API key")
                    if r.status_code == 402:
                        raise ProviderError("OpenRouter: insufficient credits")
                    if r.status_code >= 400:
                        raise ProviderError(f"OpenRouter {r.status_code}: {r.text[:200]}")
                    data = r.json()
                    if data.get("error"):
                        raise ProviderError(f"OpenRouter error: {str(data['error'])[:200]}",
                                            retryable=True)
                    text = (data["choices"][0].get("message") or {}).get("content") or ""
                    if not text.strip():
                        raise ProviderError("empty completion", retryable=True)
                    u = data.get("usage") or {}
                    pt, ct = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
                    cost = u.get("cost")
                    cost = float(cost) if cost is not None else estimate_cost(cfg, pt, ct)
                    return ProviderResponse(text, data.get("model", cfg.model), pt, ct, cost,
                                            int((time.perf_counter() - t0) * 1000))
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last = ProviderError(f"network/timeout: {type(e).__name__}", retryable=True)
                except RateLimitError as e:
                    last = e
                except ProviderError as e:
                    if not e.retryable:
                        raise
                    last = e
                except (KeyError, IndexError, ValueError) as e:
                    last = ProviderError(f"malformed response: {e}", retryable=True)
                if attempt < self.max_retries:
                    delay = last.retry_after if isinstance(last, RateLimitError) else 2 ** attempt
                    log.warning("AI call retry %s/%s in %.1fs (%s)", attempt + 1, self.max_retries, delay, last)
                    await asyncio.sleep(delay)
            raise last or ProviderError("AI request failed")
        finally:
            if own:
                await client.aclose()


@dataclass
class UsageEvent:
    role: str
    model: str
    task: str
    project_id: int | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    ok: bool = True
    error: str = ""


@dataclass
class UsageTracker:
    """In-memory totals + optional persistent sink (DB)."""
    sink: object = None
    events: list = field(default_factory=list)

    def record(self, ev: UsageEvent) -> None:
        self.events.append(ev)
        self.events = self.events[-500:]
        if self.sink:
            try:
                self.sink(ev)
            except Exception:  # tracking must never break analysis
                log.exception("usage sink failed")

    def totals(self) -> dict:
        t = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        for e in self.events:
            t["calls"] += 1
            t["prompt_tokens"] += e.prompt_tokens
            t["completion_tokens"] += e.completion_tokens
            t["cost_usd"] += e.cost_usd
        return t
