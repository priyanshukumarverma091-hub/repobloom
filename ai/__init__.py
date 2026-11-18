from ai.ai_provider import OpenRouterProvider, UsageEvent, UsageTracker
from ai.model_router import ModelRouter
from ai.parallel_executor import ParallelExecutor
from database.models import AIUsage


def build_ai(config, db, provider=None):
    """Wire provider -> router -> executor. `provider` can be injected (tests / BloomCore)."""
    def sink(ev: UsageEvent):
        with db.session() as s:
            s.add(AIUsage(project_id=ev.project_id, role=ev.role, model=ev.model, task=ev.task,
                          prompt_tokens=ev.prompt_tokens, completion_tokens=ev.completion_tokens,
                          cost_usd=ev.cost_usd, latency_ms=ev.latency_ms, ok=ev.ok, error=ev.error))

    providers = {}
    if provider is not None:
        providers[provider.name] = provider
    elif config.openrouter_api_key:
        providers["openrouter"] = OpenRouterProvider(config.openrouter_api_key, config.openrouter_base_url,
                                                     config.ai_timeout_seconds, config.ai_max_retries)
    # Future: providers["bloomcore"] = BloomCoreProvider(...)  and set ModelConfig.provider="bloomcore"
    router = ModelRouter(config, providers, UsageTracker(sink=sink))
    return router, ParallelExecutor(router)
