# Research agent

A separate, opt-in subsystem that mines AI-trading content from social, video,
and the web, then synthesizes it into structured findings you can act on. It's
the "what should I even build?" companion to the trading bots themselves.

> **Heads up.** This is a *research* agent, not a trading agent. Findings live
> in the same SQLite DB but never reach the order pipeline. You graduate an idea
> from a `ResearchFinding` to a `Strategy` by hand — see "Going from finding to
> strategy" below.

## Quick start

```bash
# 1. Install the optional research extras (heavy — pulls PydanticAI, yt-dlp, etc.)
pip install -e ".[research]"

# 2. Add at minimum GEMINI_API_KEY + TAVILY_API_KEY to .env
#    (Reddit + GitHub creds are optional but recommended.)
cp .env.example .env  # if you don't already have one

# 3. Confirm which adapters are wired:
trading-bot research sources

# 4. Run a research pass:
trading-bot research run "intraday momentum on crypto using ML signals"

# 5. View the findings:
trading-bot research show <query_id>

# Or via HTTP:
#   GET  /api/research/sources
#   POST /api/research/queries     {"topic": "..."}
#   GET  /api/research/queries/<id>
```

Findings persist forever — `ResearchDocument`s are deduplicated globally by
(source, external_id), so popular content is only fetched once across runs.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  CLI / HTTP                                                       │
│        │                                                          │
│        ▼                                                          │
│  src/research/pipeline.py::run_research(topic)                    │
│                                                                   │
│   ┌─────────────────┐    ┌──────────────────┐    ┌──────────────┐ │
│   │ Planner agent   │ →  │ Researcher agent │ →  │ Synthesizer  │ │
│   │ Gemini Pro      │    │ Gemini Flash     │    │ agent (Pro)  │ │
│   │ ResearchPlan    │    │ tool-using       │    │ FindingsBundle│
│   └─────────────────┘    └────────┬─────────┘    └──────────────┘ │
│                                   │                               │
│                                   ▼                               │
│                          source adapter tools:                    │
│                          search_reddit, search_youtube,           │
│                          search_arxiv, search_github,             │
│                          search_hackernews, search_web,           │
│                          (search_x / tiktok / instagram if Apify) │
│                                                                   │
│  Persistence: SQLite via SQLAlchemy                               │
│   ResearchQuery     — one per topic, lifecycle (running→done)     │
│   ResearchDocument  — every fetched piece of content (deduped)    │
│   ResearchFinding   — distilled insights with citations           │
└──────────────────────────────────────────────────────────────────┘
```

### Why three agents

This is the canonical "deep research" pattern that Anthropic, OpenAI, and Google
all converged on. Each agent has a small, validated output schema, a single job,
and a different cost profile:

| Agent | Model | Cost | Output | Why this layer |
|---|---|---|---|---|
| Planner | `gemini-2.5-pro` | premium | `ResearchPlan` (4-8 sub-queries) | One careful, expensive call decomposes "fuzzy topic" into search-friendly angles. |
| Researcher | `gemini-2.5-flash` | cheap | tool-call trace + collected docs | Many cheap calls — the LLM picks which adapter tool to invoke for each sub-query. |
| Synthesizer | `gemini-2.5-pro` | premium | `FindingsBundle` (distilled insights + citations) | One careful, expensive call turns ~50 raw docs into ~10 structured findings. |

Switch models in `.env`: `RESEARCH_MODEL` and `RESEARCH_FAST_MODEL`.

### Why PydanticAI

- **Type-safe outputs.** Every agent's response is validated against a Pydantic
  schema. LLMs hallucinate — schema validation makes that loud and fixable.
- **Native Gemini.** No extra adapter layer; `google-gla:<model>` Just Works.
- **Tools = functions.** The researcher's `search_reddit`, `search_arxiv`, etc.
  are just async Python functions that the LLM picks dynamically.
- **Same library for all three agents** → consistent observability, single
  upgrade path, less code to maintain.

The alternative would be LangGraph (heavier, more flexible) or hand-rolled
prompts on the Gemini SDK (more code, no schema validation). PydanticAI is the
sweet spot for this scope.

## Source adapters

Same `adapter → cache → consumer` shape as `docs/data-sources.md`, but async and
producing pre-cleaned `DocumentRow` objects ready for the synthesizer.

| Adapter | Module | Auth | Cost | Notes |
|---|---|---|---|---|
| `reddit` | `sources/reddit.py` | client_id + secret | free | Multi-sub search via async PRAW; pulls top-level comments. |
| `youtube` | `sources/youtube.py` | none (or YT Data key) | free | yt-dlp search → youtube-transcript-api. Skips videos with no captions. |
| `hackernews` | `sources/hackernews.py` | none | free | Algolia HN search + top comment thread. |
| `arxiv` | `sources/arxiv.py` | none | free | Filtered to q-fin.* + cs.LG + stat.ML categories. |
| `github` | `sources/github.py` | optional PAT | free | Repo search; reads README. Token raises rate limits. |
| `web` | `sources/web.py` | Tavily key | $0–$50/mo | The general-purpose web search. 1k free queries/mo on Tavily's free tier. |
| `x` | `sources/social_apify.py` | Apify token | ~$49/mo | Defer — direct X API basic is $100+/mo, scraping is fragile. |
| `tiktok` | `sources/social_apify.py` | Apify token | ~$49/mo | Captions only — low content density vs YouTube. |
| `instagram` | `sources/social_apify.py` | Apify token | ~$49/mo | Hashtag-based; captions only. |

All adapters degrade to `[]` without credentials (same convention as
`src/data/news.py`). `available_sources()` lists only the ones that will
actually run.

### Adding a new source

```python
# src/research/sources/my_source.py
from src.research.sources.base import DocumentRow, SourceAdapter, register

@register
class MyAdapter(SourceAdapter):
    id = "mysource"
    name = "My Source"

    def is_available(self) -> bool:
        return bool(get_settings().my_api_key)

    async def search(self, query: str, limit: int = 10) -> list[DocumentRow]:
        # Hit your API, map to DocumentRow. Return [] on any failure.
        ...
```

Then add a one-line import in `src/research/sources/__init__.py` to trigger
registration. The researcher will pick it up automatically — no agent code to
edit.

## Going from a finding to a strategy

The research agent **never** writes a strategy on your behalf. The graduation
path is deliberate:

1. Run research → look at the `actionable=True` findings with `confidence > 0.7`.
2. Pick one. Read its citations (`trading-bot research show <id>` shows them).
3. **Backtest the idea on real data** before writing any production code.
4. If it has any edge: write the bot following `docs/data-sources.md`'s recipe.
5. Walk-forward optimize (`trading-bot optimize ...`) — refuse if `overfit_gap > 1.0`.
6. Paper-trade. Use `trading-bot graduate` only after the gate passes.

The findings table is your idea backlog, not your trading desk.

## Cost control

Token usage is dominated by the synthesizer (it sees ~50 docs × 2k chars ≈ 200k
tokens of prompt). Knobs:

- **Lower `RESEARCH_MODEL`** to `gemini-2.5-flash` for both planner and
  synthesizer. ~10x cheaper, ~20% lower-quality findings.
- **Cap docs per sub-query** in the researcher prompt (currently the LLM aims
  for 30-60 total — you can constrain it harder).
- **Reuse runs.** Two queries on similar topics share their `ResearchDocument`
  rows automatically (deduped by source + external_id), but each pays its own
  synthesis tokens. If you query the same topic twice in a week, you're paying
  the synthesis tokens twice — by design (you'll get a different perspective).

For free monitoring of token spend + agent traces, set `LOGFIRE_TOKEN`. The
pipeline auto-instruments PydanticAI when the env var is present.

## Limitations / things this does NOT do

- **No live X/Twitter without Apify.** The free X API tier was killed in 2023.
- **No backtest hookup.** Findings are text. You backtest them by hand using
  the existing `src/backtest/` harness.
- **No vector search yet.** `ResearchDocument.content` is plaintext only. If
  you need semantic search across runs, the obvious next step is to add a
  `Document.embedding` column populated by the Gemini embedding model and a
  `vec_search()` helper. Skipped in v1 — premature without a real query load.
- **No de-dup of findings across runs.** Two runs on the same topic produce two
  parallel sets of findings, even when they're near-duplicates. Add a "merge
  findings" agent if this matters.

## Testing

```bash
pytest tests/test_research.py
```

The tests exercise:
- Adapter no-creds degradation (every adapter returns `[]` with no auth).
- DocumentRow → ResearchDocument idempotent upsert.
- Citation index → DB id remap survives dedup.
- Researcher tool wiring matches the available adapter set.
- FindingsBundle schema round-trips.

We deliberately don't make real LLM/network calls in tests — the pipeline-level
integration test is left for the user to run manually with `trading-bot research run`.
