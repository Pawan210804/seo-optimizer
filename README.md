---
title: SEO Optimizer
emoji: 📈
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# SEO Optimizer

A Flask API that analyzes a webpage (by URL or raw HTML) and returns a full SEO report: rule-based technical checks, keyword suggestions grounded in the page's own content, a semantic on-topic score, and AI-generated recommendations from multiple independent sources.

- **AI-graded score, blended into the overall score.** `gemini_seo.py` and `openrouter_seo.py` now also return a strict `SCORE: 0-100` line (prompted to be a harsh, realistic grader — most pages should land 40-75, not 90+). `ai_scoring.py` averages whichever source(s) are available into one more check, "AI Assessment", which `scoring.py` folds into `overall_score` as its own weighted category (20%) — the other four categories were scaled down proportionally (not just diluted unevenly) to make room. If neither AI source is configured/available, "AI Assessment" is excluded from the score exactly like "Keyword Usage" is when no keyword is supplied — never counted as a free pass. `/analyze-site` does not add this per page (same reason it doesn't call Gemini/OpenRouter per page — cost).

## What changed in this update

- **Honest scoring — no more free 100s.** Previously, checks that need a focus keyword (keyword placement, keyword density, semantic coverage) silently scored 100/"passed" when no keyword was supplied, and their whole category was worth 30% of the overall score — meaning a page with real problems could still show a strong overall score just by never being given a keyword. Those checks now score `null` ("not applicable") when a keyword is missing, `scoring.py` excludes `null` checks from their category average instead of crediting them, and the weights of the remaining categories are renormalized. The API response gets a `scoring_note` field explaining exactly what was excluded and why whenever this happens, and the dashboard shows it as a banner + marks those rows "N/A" instead of a false pass or fail.
- **Harder, more realistic technical checks.** Image alt-text coverage now needs 100% (not 80%) for full marks. Six new checks were added, all in the "Technical SEO" category so it takes real, complete on-page work to score well there: **Indexability** (catches a `noindex` meta tag — the single most important technical SEO signal, since a noindexed page can't rank at all, regardless of every other score), **Canonical tag**, **Mobile friendliness** (viewport meta tag), **Social sharing tags** (Open Graph), and **Structured data** (JSON-LD/schema.org).
- **Security hardening.**
  - **SSRF protection** (`extractor.py`, `assert_safe_to_fetch`): both `/analyze` and `/analyze-site` fetch a URL supplied by the caller. Every fetch now validates that the URL is plain `http`/`https` and resolves to a public IP — not loopback (`127.0.0.1`), private ranges (`10.x`, `172.16-31.x`, `192.168.x`), or link-local addresses (`169.254.x.x`, where cloud metadata endpoints like `169.254.169.254` live). Redirects are followed one hop at a time with the same check re-run on every hop, so a URL that passes the check and then redirects internally can't slip through.
  - **Response size cap**: page downloads are capped at 5MB (streamed, not trusted from `Content-Length`) to stop a huge or slow-drip response from exhausting memory.
  - **Request size cap + input validation** (`app.py`): the whole request body is capped at 2MB, and `url`/`html`/`keyword` each have their own length limits, checked before any parsing happens.
  - **Basic rate limiting** (`app.py`): a simple per-IP, per-endpoint limiter on `/analyze` (20/min) and `/analyze-site` (5/min) — in-memory, good enough to blunt accidental abuse; swap for a real limiter (e.g. Redis-backed) if you're deploying this for many concurrent users.
  - **Security response headers**: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` added to every response.
- **Bigger, prioritized AI recommendations.** `gemini_seo.py` and `openrouter_seo.py` now ask for 6-10 recommendations (was 4-6), each tagged `[HIGH]`/`[MEDIUM]`/`[LOW]` and ordered highest-priority first, and explicitly asked to cover technical/structured-data/mobile gaps, not just keyword tweaks. `local_recommendations.py` now surfaces up to 10 (was 6) and adds an explicit note whenever part of the score was skipped for lack of a keyword.

## Features

- **Whole-site analysis** (`site_crawler.py`, `POST /analyze-site`) — give it one starting URL and it crawls same-domain pages (breadth-first, capped at `max_pages`), runs the full single-page pipeline on each one, and rolls the results into one site report: average score, a per-page score chart, and a "site-wide issues" table that shows which specific problems (e.g. missing meta descriptions) recur across the most pages, each with its own fix. Gemini/OpenRouter are called once per crawl (grounded in whichever page scored lowest) rather than once per page, to keep a multi-page scan fast and cheap — every page still gets the free rule-based, semantic, and local-recommendation checks.
- **Rule-based SEO checks** (`rules.py`) — title length & keyword usage, meta description length & keyword usage, heading structure (H1/H2), keyword placement in the opening paragraph, keyword density, image alt-text coverage, internal/external link counts, readability (Flesch Reading Ease), and content length.
- **Weighted scoring** (`scoring.py`) — rolls up individual checks into four category scores (Technical SEO, Keyword Usage, Readability, Content Depth) and one overall score out of 100.
- **Keyword suggestions** (`keywords.py`) — surfaces phrases that already recur in the page's own text (via spaCy noun chunks/entities, or a manual n-gram fallback if spaCy isn't installed). Never invents keywords.
- **Semantic coverage check** (`semantic.py`) — scores how well the content stays on-topic for the focus keyword, combining up to three independent signals: Azure AI Language key-phrase extraction, local spaCy key phrases, and sentence-transformers embedding similarity. Any signal that isn't configured or fails is skipped gracefully; the check only fully skips if none are available.
- **AI recommendations from three independent sources** (each degrades gracefully if unavailable, and none affects the others):
  - `gemini_seo.py` — Google Gemini API
  - `openrouter_seo.py` — any model available via [OpenRouter](https://openrouter.ai)
  - `local_recommendations.py` — always-on, offline recommendations derived from the rule checks and keyword suggestions above (no API key required)
- **Content extraction** (`extractor.py`) — pulls title, meta description, headings, body text, image alt coverage, and internal/external links from raw HTML or a live URL. Core string/search logic (word splitting, domain parsing, substring search) is written as explicit loops rather than regex, for transparency.
- **Simple dashboard** (`seo-dashboard.html`) — a static frontend for calling the API and viewing results.

## Project structure

```
app.py                    Flask app / API entry point
extractor.py               HTML content extraction
rules.py                    Rule-based SEO checks
scoring.py                  Category + overall scoring
keywords.py                 On-page keyword suggestions
semantic.py                 Semantic/topic-coverage scoring
gemini_seo.py                Gemini-powered recommendations
openrouter_seo.py            OpenRouter-powered recommendations
local_recommendations.py     Offline, rule-derived recommendations
site_crawler.py               Whole-site crawl + per-page analysis + rollup
seo-dashboard.html           Static frontend (single page + whole-site tabs)
requirements.txt             Pinned Python dependencies
runtime.txt                  Pinned Python version (for Render)
```

## Getting started

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Optional (improves keyword suggestions and semantic coverage):

```bash
python -m spacy download en_core_web_sm
```

### 2. Configure environment variables

Create a `.env` file in the project root (never commit this):

```
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash

OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=openai/gpt-4o-mini

AZURE_LANGUAGE_ENDPOINT=your_endpoint_here
AZURE_LANGUAGE_KEY=your_key_here
```

All of these are optional. Every feature that depends on an external API degrades to a clean "unavailable" result — with a reason — rather than crashing, if a key is missing or a call fails.

### 3. Run the server

```bash
python app.py
```

The API is available at `http://localhost:8000`.

## API

### `GET /`

Health check.

### `POST /analyze`

Request body (JSON), one of `url` or `html` required:

```json
{
  "url": "https://example.com/some-page",
  "keyword": "home coffee brewing"
}
```

or

```json
{
  "html": "<html>...</html>",
  "keyword": "home coffee brewing"
}
```

Response: a JSON report containing `overall_score`, `category_scores`, per-check results, `keyword_suggestions`, `ai_recommendations` (from `gemini`, `openrouter`, and `local` sources), and basic page info.

### `POST /analyze-site`

Crawls same-domain pages starting from `url` and analyzes each one. Request body:

```json
{
  "url": "https://example.com/",
  "keyword": "home coffee brewing",
  "max_pages": 15
}
```

`keyword` and `max_pages` are optional (`max_pages` defaults to 15, hard-capped at 40 per request). Response: `site_overall_score`, `site_category_scores`, `pages` (per-page scores, sorted weakest-first), `top_site_issues` (issues ranked by how many pages they affect, each with its own fix), `worst_page`, `ai_recommendations` (Gemini + OpenRouter, generated once, grounded in the single weakest page), and `errors` (any pages that failed to fetch/parse and were skipped).

## Deployment (Render)

This repo includes a pinned `runtime.txt` (Python 3.12.7) and pinned `requirements.txt`. Both are important: `spacy`, `sentence-transformers`, and their dependency chain (`textstat` → `nltk` → `regex`) are C-extension-heavy and can fail to build or import cleanly on very new, unpinned Python versions. Keep these pinned unless you've verified compatibility with a newer version yourself.

Start command:

```
gunicorn app:app
```

## Notes

- SSL verification is only skipped as a flagged fallback when a target site's certificate chain is broken — never as a blanket default — and the report notes when this happened (`ssl_unverified`).
- Every AI/network-dependent feature is designed to fail independently: a missing key or a failed call for one source never breaks the rest of the report.
- Every check in `rules.py` marks itself `passed` at score >= 70. The dashboard's color-coded chips now use that same 70/50 cutoff everywhere (bar chart, category dots, table chips, funnel) — previously the frontend used a different 80/50 cutoff than the backend, so a check scoring 70-79 could show a green "Pass" label sitting on an amber chip. Fixed by making `PASS_THRESHOLD`/`FAIL_THRESHOLD` in `seo-dashboard.html` the single source of truth for both color and label.
