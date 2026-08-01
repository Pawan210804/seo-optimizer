# SEO Optimizer

A Flask API that analyzes a webpage (by URL or raw HTML) and returns a full SEO report: rule-based technical checks, keyword suggestions grounded in the page's own content, a semantic on-topic score, and AI-generated recommendations from multiple independent sources.

## Features

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
seo-dashboard.html           Static frontend
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

## Deployment (Render)

This repo includes a pinned `runtime.txt` (Python 3.12.7) and pinned `requirements.txt`. Both are important: `spacy`, `sentence-transformers`, and their dependency chain (`textstat` → `nltk` → `regex`) are C-extension-heavy and can fail to build or import cleanly on very new, unpinned Python versions. Keep these pinned unless you've verified compatibility with a newer version yourself.

Start command:

```
gunicorn app:app
```

## Notes

- SSL verification is only skipped as a flagged fallback when a target site's certificate chain is broken — never as a blanket default — and the report notes when this happened (`ssl_unverified`).
- Every AI/network-dependent feature is designed to fail independently: a missing key or a failed call for one source never breaks the rest of the report.
