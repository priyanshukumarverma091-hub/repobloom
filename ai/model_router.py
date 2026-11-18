"""Centralised routing: which roles run for which task, and which provider serves each role."""
import logging
from dataclasses import dataclass

from ai.ai_provider import BaseProvider, ProviderError, ProviderResponse, UsageEvent, UsageTracker
from config import Config, ModelConfig

log = logging.getLogger(__name__)

REPO, CTX, SYN = "repository_analyst", "context_analyst", "intelligence_synthesizer"


@dataclass(frozen=True)
class Plan:
    analysts: tuple[str, ...]
    synthesize: bool = False


# Do not call all three models unnecessarily: single-domain tasks use one analyst only.
TASK_PLANS: dict[str, Plan] = {
    "repository": Plan((REPO,)),
    "architecture": Plan((REPO,)),
    "activity": Plan((REPO,)),
    "bugs": Plan((CTX,)),
    "decisions": Plan((CTX,)),
    "tasks": Plan((REPO, CTX), synthesize=True),
    "context_recovery": Plan((REPO, CTX), synthesize=True),
}


class ModelRouter:
    def __init__(self, config: Config, providers: dict[str, BaseProvider], tracker: UsageTracker | None = None):
        self.config = config
        self.providers = providers
        self.tracker = tracker or UsageTracker()

    def model_config(self, role: str) -> ModelConfig:
        return self.config.models[role]

    def is_ready(self, role: str) -> bool:
        cfg = self.config.models.get(role)
        return bool(cfg and cfg.model and cfg.provider in self.providers)

    def plan(self, task: str) -> Plan:
        if task not in TASK_PLANS:
            raise ValueError(f"unknown task {task!r}")
        return TASK_PLANS[task]

    async def call(self, role: str, messages: list[dict], task: str, project_id: int | None = None) -> ProviderResponse:
        cfg = self.model_config(role)
        provider = self.providers.get(cfg.provider)
        ev = UsageEvent(role, cfg.model, task, project_id)
        try:
            if provider is None or not cfg.model:
                raise ProviderError(f"role '{role}' is not configured")
            resp = await provider.complete(cfg, messages)
            ev.model = resp.model
            ev.prompt_tokens, ev.completion_tokens = resp.prompt_tokens, resp.completion_tokens
            ev.cost_usd, ev.latency_ms = resp.cost_usd, resp.latency_ms
            return resp
        except Exception as e:
            ev.ok, ev.error = False, str(e)[:480]
            raise
        finally:
            self.tracker.record(ev)
