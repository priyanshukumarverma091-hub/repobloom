from config import Config


def test_health_unconfigured(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.get_json()["config"]
    assert body["openrouter_configured"] is False and body["ai_ready"] is False
    assert body["models"] == {"repository_analyst": False, "context_analyst": False, "intelligence_synthesizer": False}
    assert body["database"] == "sqlite"


def test_health_never_leaks_secrets(make_app):
    c = make_app(OPENROUTER_API_KEY="sk-secret-123", GITHUB_TOKEN="ghp_secret", REPO_ANALYST_MODEL="a/b").test_client()
    r = c.get("/health")
    assert b"sk-secret-123" not in r.data and b"ghp_secret" not in r.data
    assert r.get_json()["config"]["models"]["repository_analyst"] is True


def test_models_configured_independently(env):
    env.setenv("REPO_ANALYST_MODEL", "a/one")
    env.setenv("CONTEXT_ANALYST_MODEL", "b/two")
    env.setenv("SYNTHESIZER_MODEL", "c/three")
    m = Config().models
    assert (m["repository_analyst"].model, m["context_analyst"].model, m["intelligence_synthesizer"].model) == ("a/one", "b/two", "c/three")


def test_index_page_and_404(client):
    assert client.get("/").status_code == 200
    assert client.get("/nope").status_code == 404


def test_api_token_enforced(make_app):
    c = make_app(APP_ACCESS_TOKEN="letmein").test_client()
    assert c.get("/api/repositories/").status_code == 401
    assert c.get("/api/repositories/", headers={"X-RepoBloom-Token": "wrong"}).status_code == 401
    assert c.get("/api/repositories/", headers={"X-RepoBloom-Token": "letmein"}).status_code == 200
    assert c.get("/health").status_code == 200


def test_project_validation(client):
    assert client.post("/api/repositories/", json={"name": "x", "github_repo": "not a repo"}).status_code == 400
    assert client.post("/api/repositories/", json={"name": "x", "discord_channel_ids": ["abc"]}).status_code == 400
    assert client.post("/api/repositories/", json={"name": "x", "doc_urls": ["ftp://x"]}).status_code == 400
    r = client.post("/api/repositories/", json={"github_repo": "https://github.com/octo/cat.git"})
    assert r.status_code == 201 and r.get_json()["github_repo"] == "octo/cat" and r.get_json()["name"] == "octo/cat"
    pid = r.get_json()["id"]
    assert client.delete(f"/api/repositories/{pid}").status_code == 200
    assert client.get(f"/api/repositories/{pid}").status_code == 404


def test_database_url_normalised(env):
    env.setenv("DATABASE_URL", "postgres://u:p@h:5432/d")
    assert Config().resolved_database_url == "postgresql+psycopg://u:p@h:5432/d"
    env.setenv("DATABASE_URL", "postgresql://u:p@h/d")
    assert Config().resolved_database_url == "postgresql+psycopg://u:p@h/d"
    env.setenv("DATABASE_URL", "")
    assert Config().resolved_database_url.startswith("sqlite")
