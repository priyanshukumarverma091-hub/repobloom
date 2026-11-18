"""Central configuration. All secrets come from environment / .env, never hardcoded."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for one independent AI model role."""
    role: str
    model: str
    max_tokens: int
    price_in: float = 0.0   # USD per 1M prompt tokens
    price_out: float = 0.0  # USD per 1M completion tokens
    provider: str = "openrouter"


ROLE_ENV = {
    "repository_analyst": "REPO_ANALYST",
    "context_analyst": "CONTEXT_ANALYST",
    "intelligence_synthesizer": "SYNTHESIZER",
}


@dataclass(frozen=True)
class Config:
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", "dev-only-change-me"))
    env: str = field(default_factory=lambda: os.getenv("FLASK_ENV", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    access_token: str = field(default_factory=lambda: os.getenv("APP_ACCESS_TOKEN", ""))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))

    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_base_url: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"))
    ai_timeout_seconds: int = field(default_factory=lambda: _int("AI_TIMEOUT_SECONDS", 90))
    ai_max_retries: int = field(default_factory=lambda: _int("AI_MAX_RETRIES", 3))

    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    github_max_commit_details: int = field(default_factory=lambda: _int("GITHUB_MAX_COMMIT_DETAILS", 40))
    discord_bot_token: str = field(default_factory=lambda: os.getenv("DISCORD_BOT_TOKEN", ""))
    discord_anonymize: bool = field(default_factory=lambda: _bool("DISCORD_ANONYMIZE", True))
    docs_user_agent: str = field(
        default_factory=lambda: os.getenv("DOCS_USER_AGENT", "RepoBloomBot/1.0"))

    @property
    def resolved_database_url(self) -> str:
        url = self.database_url.strip()
        if not url:
            return "sqlite:///repobloom.db"
        # Render/Heroku style URLs -> SQLAlchemy + psycopg 3 driver
        for old in ("postgres://", "postgresql://"):
            if url.startswith(old):
                return "postgresql+psycopg://" + url[len(old):]
        return url

    @property
    def models(self) -> dict[str, ModelConfig]:
        out = {}
        for role, prefix in ROLE_ENV.items():
            out[role] = ModelConfig(
                role=role,
                model=os.getenv(f"{prefix}_MODEL", ""),
                max_tokens=_int(f"{prefix}_MAX_TOKENS", 3072 if role == "intelligence_synthesizer" else 2048),
                price_in=_float(f"{prefix}_PRICE_IN", 0.0),
                price_out=_float(f"{prefix}_PRICE_OUT", 0.0),
            )
        return out

    @property
    def ai_ready(self) -> bool:
        return bool(self.openrouter_api_key) and all(m.model for m in self.models.values())

    def public_summary(self) -> dict:
        """Safe-to-expose view: reports whether things are configured, never the secrets."""
        return {
            "env": self.env,
            "database": "postgresql" if self.resolved_database_url.startswith("postgres") else "sqlite",
            "database_configured": bool(self.database_url),
            "access_token_required": bool(self.access_token),
            "openrouter_configured": bool(self.openrouter_api_key),
            "github_configured": bool(self.github_token),
            "discord_configured": bool(self.discord_bot_token),
            "ai_ready": self.ai_ready,
            "models": {k: bool(v.model) for k, v in self.models.items()},
        }


def get_config() -> Config:
    return Config()
