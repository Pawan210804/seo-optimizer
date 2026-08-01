# app.py
# This is the web server. It uses Flask, which is simpler than FastAPI
# for a beginner: no type hints, no pydantic models, just plain
# request.get_json() and a normal Python dictionary back.
#
# Run it with:
#     python app.py
# Then send it requests at http://localhost:8000/analyze

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

from extractor import extract_content, extract_from_url
from rules import run_all_checks
from semantic import check_semantic_coverage
from scoring import score_report
from keywords import suggest_keywords
from gemini_seo import get_seo_recommendations
from openrouter_seo import get_seo_recommendations as get_seo_recommendations_openrouter
from local_recommendations import get_local_recommendations

app = Flask(__name__)

# Without this, a frontend served from a different origin (a local HTML
# file, a different port, etc.) will have its requests blocked by the
# browser's CORS policy, even though the server itself works fine.
CORS(app)


@app.route("/")
def home():
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

    # The semantic check calls Azure over the network, so it's kept
    # separate from the other checks. If Azure isn't configured or the
    # call fails, this still returns a safe "skipped" result instead
    # of raising an error - the rest of the report is not affected.
    checks.append(check_semantic_coverage(content, keyword))

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
            "gemini": get_seo_recommendations(content, keyword),
            "openrouter": get_seo_recommendations_openrouter(content, keyword),
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


if __name__ == "__main__":
    app.run(debug=True, port=8000)
