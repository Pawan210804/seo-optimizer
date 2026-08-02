# app.py
# This is the web server. It uses Flask, which is simpler than FastAPI
# for a beginner: no type hints, no pydantic models, just plain
# request.get_json() and a normal Python dictionary back.
#
# Run it with:
#     python app.py
# Then send it requests at http://localhost:8000/analyze

import time
import threading
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests

from extractor import extract_content, extract_from_url
from rules import run_all_checks
import semantic
from semantic import check_semantic_coverage
from scoring import score_report
from keywords import suggest_keywords
from gemini_seo import get_seo_recommendations
from openrouter_seo import get_seo_recommendations as get_seo_recommendations_openrouter
from ai_scoring import build_ai_assessment_check
from local_recommendations import get_local_recommendations
from site_crawler import crawl_site, DEFAULT_MAX_PAGES, MAX_PAGES_HARD_LIMIT

app = Flask(__name__)

# Caps the size of any incoming request body. Without this, someone
# could POST a multi-gigabyte "html" field and tie up memory/CPU on
# every downstream parser (BeautifulSoup, spaCy, textstat) for a single
# request - this rejects oversized bodies before any of that runs.
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2MB

# Reasonable upper bound on how much raw HTML/URL text we'll ever try to
# parse from the JSON body itself (separate from MAX_CONTENT_LENGTH,
# which covers the whole request including JSON overhead).
MAX_HTML_CHARACTERS = 2 * 1024 * 1024
MAX_URL_CHARACTERS = 2048
MAX_KEYWORD_CHARACTERS = 200


# ---------------------------------------------------------------
# Minimal in-memory rate limiting. This is intentionally simple (no
# Redis, no extra dependency) - it's a fixed-window counter per client
# IP, per endpoint, reset every WINDOW_SECONDS. Good enough to blunt
# accidental abuse or a naive scraping loop hitting the network/CPU
# heavy endpoints (URL fetch, AI calls, whole-site crawl) from a single
# source; it is NOT a substitute for a real rate limiter/WAF in front
# of a production deployment with many users.
# ---------------------------------------------------------------
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMITS = {
    "/analyze": 20,
    "/analyze-site": 5,
}
_request_log = defaultdict(deque)
_rate_limit_lock = threading.Lock()


def _client_ip():
    # Respects a single upstream proxy hop (e.g. a platform's load
    # balancer) via X-Forwarded-For, falling back to the direct peer.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_limited(endpoint):
    limit = RATE_LIMITS.get(endpoint)
    if limit is None:
        return False
    key = (endpoint, _client_ip())
    now = time.time()
    with _rate_limit_lock:
        timestamps = _request_log[key]
        while timestamps and now - timestamps[0] > RATE_LIMIT_WINDOW_SECONDS:
            timestamps.popleft()
        if len(timestamps) >= limit:
            return True
        timestamps.append(now)
        return False


@app.before_request
def _enforce_rate_limit():
    if _rate_limited(request.path):
        return jsonify({
            "error": "Rate limit exceeded - too many requests to " + request.path
                      + ". Please wait a minute and try again."
        }), 429


@app.after_request
def _add_security_headers(response):
    # Baseline hardening headers. The dashboard is a trusted static file
    # served by this same app, so these don't break it, but they stop
    # this API's JSON responses from being framed/sniffed/embedded by an
    # unrelated third-party page.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# Pre-load the sentence-transformers model (used by the semantic
# coverage check whenever a keyword is supplied) in the background as
# soon as the app starts, instead of leaving it to load lazily on
# whoever's first keyworded request happens to land after a deploy or
# a cold start. Runs in a thread so it never delays the app from
# binding its port and coming up.
threading.Thread(target=semantic.warm_up, daemon=True).start()

# Without this, a frontend served from a different origin (a local HTML
# file, a different port, etc.) will have its requests blocked by the
# browser's CORS policy, even though the server itself works fine.
CORS(app)


@app.route("/")
def home():
    # Serves the dashboard itself, so visiting the deployed URL in a
    # browser shows the UI instead of a bare JSON status blob.
    return send_from_directory(".", "seo-dashboard.html")


@app.route("/api/status")
def api_status():
    # The old "/" JSON health check, moved here so it's still available
    # for uptime monitors / quick checks without colliding with the
    # dashboard route above.
    return jsonify({"status": "ok", "message": "SEO analysis API is running. POST to /analyze to use it."})


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()

    if data is None:
        return jsonify({"error": "Please send JSON with 'url' or 'html'."}), 400

    url = data.get("url")
    html = data.get("html")
    keyword = data.get("keyword")

    if not url and not html:
        return jsonify({"error": "Provide either 'url' or 'html'."}), 400

    if url and (not isinstance(url, str) or len(url) > MAX_URL_CHARACTERS):
        return jsonify({"error": "'url' is invalid or too long (max " + str(MAX_URL_CHARACTERS) + " characters)."}), 400
    if html and (not isinstance(html, str) or len(html) > MAX_HTML_CHARACTERS):
        return jsonify({"error": "'html' is too large (max " + str(MAX_HTML_CHARACTERS) + " characters)."}), 400
    if keyword and (not isinstance(keyword, str) or len(keyword) > MAX_KEYWORD_CHARACTERS):
        return jsonify({"error": "'keyword' is too long (max " + str(MAX_KEYWORD_CHARACTERS) + " characters)."}), 400

    try:
        if url:
            content = extract_from_url(url)
        else:
            content = extract_content(html, None)
    except requests.RequestException as error:
        return jsonify({"error": "Could not fetch URL: " + str(error)}), 422
    except Exception as error:
        # Covers malformed HTML or anything else extract_content trips on,
        # so the caller gets a clean error instead of a raw stack trace.
        return jsonify({"error": "Could not parse the page: " + str(error)}), 422

    checks = run_all_checks(content, keyword)

    # The semantic check (Azure/spaCy/similarity), the Gemini call, and
    # the OpenRouter call are all independent, network/CPU-bound work
    # that don't depend on each other's results. Run them concurrently
    # in a small thread pool instead of one after another - each of
    # these can individually take several seconds, and running them in
    # sequence meant every request paid the SUM of all three delays.
    # Running them in parallel means it only pays whichever one is
    # slowest, which is the single biggest lever for cutting response
    # time on this endpoint.
    with ThreadPoolExecutor(max_workers=3) as pool:
        semantic_future = pool.submit(check_semantic_coverage, content, keyword)
        gemini_future = pool.submit(get_seo_recommendations, content, keyword)
        openrouter_future = pool.submit(get_seo_recommendations_openrouter, content, keyword)

        # The semantic check's own result becomes one of the rule
        # checks, same as before - just fetched concurrently now
        # rather than blocking the other two calls while it runs.
        checks.append(semantic_future.result())
        gemini_result = gemini_future.result()
        openrouter_result = openrouter_future.result()

    # Turns the numeric score each AI source graded the page (its
    # "SCORE:" line) into one more check, so it flows through
    # score_report() below exactly like every rule-based check - see
    # ai_scoring.py for how it degrades gracefully when neither AI
    # source returns a usable score.
    checks.append(build_ai_assessment_check(gemini_result, openrouter_result))

    report = score_report(checks)

    # Related keyword suggestions - phrases already recurring in the
    # page's own text that the user could weave in more deliberately.
    # Grounded in real content, never invented.
    report["keyword_suggestions"] = suggest_keywords(content, keyword)

    # AI-generated + rule-based recommendations, from three independent
    # sources. Same philosophy as the semantic check: if a key is missing
    # or a call fails, that ONE source returns a clean "unavailable"
    # result instead of raising - it never affects the other sources or
    # the rest of the report.
    #   - "gemini":     Google's Gemini API (gemini_seo.py)
    #   - "openrouter": any model available via OpenRouter (openrouter_seo.py)
    #   - "local":      always-on, offline recommendations built from the
    #                   rule checks above + spaCy/n-gram keyword patterns
    #                   (local_recommendations.py) - never depends on a
    #                   network call, so there's always at least one
    #                   source of recommendations even with zero API keys.
    report["ai_recommendations"] = {
        "sources": {
            "gemini": gemini_result,
            "openrouter": openrouter_result,
            "local": get_local_recommendations(content, checks, report["keyword_suggestions"], keyword),
        }
    }

    # Add some basic page info to the report too
    report["page"] = {
        "url": content["url"],
        "title": content["title"],
        "meta_description": content["meta_description"],
        "word_count": content["word_count"],
        "h1_count": len(content["headings"]["h1"]),
        "h2_count": len(content["headings"]["h2"]),
        "h1_list": content["headings"]["h1"],
        "h2_list": content["headings"]["h2"],
        "keyword": keyword,
        "ssl_unverified": content.get("ssl_unverified", False),
    }

    return jsonify(report)


@app.route("/analyze-site", methods=["POST"])
def analyze_site():
    # Whole-website version of /analyze: crawls same-domain pages
    # starting from `url`, runs the full single-page pipeline on each
    # one, and returns both the per-page results and a site-wide
    # rollup (average scores, most widespread issues, AI suggestions
    # grounded in the single weakest page). See site_crawler.py for the
    # crawl strategy and why AI calls are capped to one page per scan.
    data = request.get_json()

    if data is None or not data.get("url"):
        return jsonify({"error": "Please send JSON with a 'url' to start crawling from."}), 400

    url = data.get("url")
    keyword = data.get("keyword")
    max_pages = data.get("max_pages", DEFAULT_MAX_PAGES)

    if not isinstance(url, str) or len(url) > MAX_URL_CHARACTERS:
        return jsonify({"error": "'url' is invalid or too long (max " + str(MAX_URL_CHARACTERS) + " characters)."}), 400
    if keyword and (not isinstance(keyword, str) or len(keyword) > MAX_KEYWORD_CHARACTERS):
        return jsonify({"error": "'keyword' is too long (max " + str(MAX_KEYWORD_CHARACTERS) + " characters)."}), 400

    try:
        max_pages = int(max_pages)
    except (TypeError, ValueError):
        max_pages = DEFAULT_MAX_PAGES

    if max_pages > MAX_PAGES_HARD_LIMIT:
        max_pages = MAX_PAGES_HARD_LIMIT

    try:
        result = crawl_site(url, keyword=keyword, max_pages=max_pages)
    except requests.RequestException as error:
        return jsonify({"error": "Could not fetch the starting URL: " + str(error)}), 422
    except Exception as error:
        return jsonify({"error": "Site crawl failed: " + str(error)}), 422

    if result["pages_crawled"] == 0:
        return jsonify({
            "error": "Could not analyze any pages starting from that URL.",
            "details": result["errors"],
        }), 422

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=8000)
