import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

ENV_KEYS = ("OPENROUTER_API_KEY", "GITHUB_TOKEN", "DISCORD_BOT_TOKEN", "DATABASE_URL", "REPO_ANALYST_MODEL",
            "CONTEXT_ANALYST_MODEL", "SYNTHESIZER_MODEL", "APP_ACCESS_TOKEN")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    return monkeypatch


@pytest.fixture
def make_app(env):
    from app import create_app
    from config import Config

    def _make(provider=None, **extra):
        for k, v in extra.items():
            env.setenv(k, v)
        return create_app(Config(), provider=provider)
    return _make


@pytest.fixture
def client(make_app):
    return make_app().test_client()
