# RepoBloom

AI-powered developer intelligence and context recovery. RepoBloom collects what happened in a software
project (GitHub, Discord, documentation), connects it, remembers it, and answers *what happened, why,
and what is left* with evidence pointing back to the original sources.

Future custom model: **BloomCore** is not trained yet. The AI layer is provider-based, so BloomCore becomes
one more provider class with no change to the Intelligence Engine.

## Run it in 2 minutes (no keys needed)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                                          # http://127.0.0.1:5000
```
Open the app, click **Load demo project**, and explore every screen. It uses local SQLite until you set
`DATABASE_URL`. Without AI keys, every report still shows *computed* verified facts, and says clearly that AI is off.

## Turn on the real thing

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/repobloom` (pgvector is enabled automatically) |
| `OPENROUTER_API_KEY` | OpenRouter key |
| `REPO_ANALYST_MODEL`, `CONTEXT_ANALYST_MODEL`, `SYNTHESIZER_MODEL` | any OpenRouter model id, one per role |
| `*_PRICE_IN` / `*_PRICE_OUT` | USD per 1M tokens, only used for cost estimates when OpenRouter does not report cost |
| `GITHUB_TOKEN` | needed for private repos and higher rate limits |
| `DISCORD_BOT_TOKEN` | Discord bot (see below) |
| `APP_ACCESS_TOKEN` | if set, every `/api` call needs header `X-RepoBloom-Token` (enter it in Settings) |

Then: **Connected repositories** > add `owner/name` > **Data collection** > *Sync all sources*.

### Discord setup
1. Create a bot in the Discord Developer Portal, enable the **Message Content Intent**.
2. Invite it to your server with *Read Messages* and *Read Message History*.
3. Add only the channel IDs you are authorized to analyze. Authors are pseudonymized (`DISCORD_ANONYMIZE=true`).

## How it works

```
Collect -> Validate -> Clean (secrets redacted) -> Duplicate detection -> Classify
        -> Metadata -> Normalize -> Embed -> Store -> Relationship mapping -> Memory
```
* **Collectors**: GitHub REST (async httpx; commits, branches, PRs, reviews, issues, contributors, tree) with incremental cursors,
  GitPython for local/public repos, Discord Bot API (per-channel cursors, rate-limit aware), docs via Trafilatura + BeautifulSoup
  (robots.txt, noindex, conditional requests, license capture, SSRF guard).
* **Every record keeps**: source, timestamp, project, category, original URL, permission/licence metadata.
* **Knowledge graph** (PostgreSQL, behind a `GraphStore` interface for a future graph DB): `Bug issue <- discussion <- decision`,
  `commit/PR -resolves-> issue`, `commit -modifies-> file`. Explicit references are *verified*; same-file/time-window links are *inferred*.
* **Project memory**: architecture, development, decision, problem, task. Identical restatements only add provenance; changed statements
  become new versions, the old one is kept, and risky changes are flagged `needs review`.
* **Intelligence**: repository, architecture, developer activity, bugs, decisions, task recovery, context recovery. Every statement is
  labelled *verified fact*, *AI inference*, *recommendation* or *unknown*.

### The three models
`ai/model_router.py` decides who runs. Single-domain tasks call one analyst; `context_recovery` and `tasks` run the Repository
and Context analysts **in parallel** (`asyncio.gather`) and call the Synthesizer only if both succeeded. Failures degrade gracefully
(partial results, errors shown). Calls retry with backoff on 429/5xx/timeouts, token usage and cost are stored (see *AI model activity*).

### Anti-fabrication and prompt-injection defenses
* A model "fact" must cite evidence refs (`R12`) that were actually supplied; otherwise it is demoted to a low-confidence inference.
* External text is fenced as untrusted data, closing tags are neutralized, suspicious content is flagged, and secrets/emails are redacted before storage.

## Deploy on Render
Push to GitHub, create a **Blueprint** from `render.yaml` (web service + Postgres). Fill the `sync: false` variables.
`APP_ACCESS_TOKEN` is generated for you; paste it in Settings. Long AI calls need the 180 s gunicorn timeout already set.

## Tests
```bash
pytest          # 31 tests: collectors (mocked HTTP + real git), pipeline, AI layer, memory, graph, API
```

## Layout
`ai/` models + routing, `collectors/`, `processors/`, `intelligence/`, `memory/`, `knowledge_graph/`, `database/`, `services/`, `routes/`, `templates/` + `static/` (dashboard), `tests/`.

## Honest limits
* Embeddings are a local lexical hashing model (free, deterministic, 256-dim). Swap in a neural embedder later; the schema stays.
* Syncs fetch up to ~500 items per resource per run and continue incrementally on the next run.
* Discord message text requires the privileged Message Content intent, approved by the server owner.
* Classification is rule-based; decision-like statements without explicit wording are marked as keyword matches, not decisions.
