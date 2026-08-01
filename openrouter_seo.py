# openrouter_seo.py
# Uses OpenRouter (https://openrouter.ai) - a single API that can route to
# many different underlying models (OpenAI, Anthropic, Meta, Google, free
# community models, etc.) - to generate a short list of AI-written SEO
# recommendations for a page, given its extracted content and an optional
# focus keyword.
#
# This is a second, independent AI source alongside gemini_seo.py. The two
# never depend on each other and either one can be missing/broken without
# affecting the other, or the rest of the app.
#
# Follows the exact same graceful-degradation pattern as gemini_seo.py and
# semantic.py:
# - The API key is read from an environment variable, never hardcoded.
# - If the key is missing, or the call fails for any reason (bad key,
#   network issue, quota/credits exhausted), this does NOT crash the app -
#   it just returns an "unavailable" result and says why, and the rest of
#   the report still works normally.
# - Setup problems are logged to the server console with an [openrouter]
#   prefix so they're easy to find, same as [gemini] / [semantic].
#
# OpenRouter exposes an OpenAI-compatible REST API, so this uses plain
# `requests` (already a dependency) instead of pulling in a new SDK.

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()


def _log(message):
    print("[openrouter] " + message, file=sys.stderr)


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Any model slug from https://openrouter.ai/models will work here.
# Defaults to a small, inexpensive model; override with OPENROUTER_MODEL
# in .env if you'd rather use something else (including free-tier models,
# which usually have a ":free" suffix on OpenRouter).
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

STATUS = "not attempted yet"

_api_key = None
_key_load_failed = False


def _get_api_key():
    # Caches the "do we even have a key" check, instead of re-checking
    # os.environ on every single request.
    global _api_key, _key_load_failed, STATUS

    if _api_key is not None:
        return _api_key
    if _key_load_failed:
        return None

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        STATUS = "not configured - set OPENROUTER_API_KEY in .env"
        _key_load_failed = True
        return None

    _api_key = key
    STATUS = "ok"
    return _api_key


def _limit_text(text, max_characters):
    # Same simple truncation used in gemini_seo.py / semantic.py - keeps
    # the prompt a reasonable size instead of sending the whole page.
    if len(text) <= max_characters:
        return text
    return text[:max_characters].rsplit(" ", 1)[0] + "..."


def _build_prompt(content, keyword):
    title = content.get("title", "") or "(no title tag)"
    meta_description = content.get("meta_description", "") or "(no meta description)"
    h1_list = content.get("headings", {}).get("h1", [])
    h2_list = content.get("headings", {}).get("h2", [])
    word_count = content.get("word_count", 0)
    body_excerpt = _limit_text(content.get("body_text", ""), 3000)

    keyword_line = ("Focus keyword: " + keyword) if keyword else "Focus keyword: (none supplied)"

    prompt = (
        "You are an SEO editor reviewing a single web page. Based only on the "
        "content below, respond in exactly this format and nothing else:\n\n"
        "TITLE: a rewritten <title> tag, 50-60 characters, that improves on the current one "
        "(keep it if it's already good)\n"
        "META: a rewritten meta description, 150-160 characters, that improves on the current one "
        "(keep it if it's already good)\n"
        "RECOMMENDATIONS:\n"
        "- 4 to 6 short, specific, actionable recommendations to improve search performance and "
        "content quality, one per line, no numbering or bullets beyond a leading '- '\n\n"
        "Every recommendation and rewrite must be concrete and grounded in what's actually present "
        "or missing in this content - do not invent facts about the page.\n\n"
        "Title: " + title + "\n"
        "Meta description: " + meta_description + "\n"
        + keyword_line + "\n"
        "Word count: " + str(word_count) + "\n"
        "H1 headings: " + (", ".join(h1_list) if h1_list else "(none)") + "\n"
        "H2 headings: " + (", ".join(h2_list) if h2_list else "(none)") + "\n\n"
        "Body content excerpt:\n" + body_excerpt
    )
    return prompt


def _clean_bullet_line(line):
    # Strips stray numbering or bullet characters the model adds despite
    # being asked not to. Identical to gemini_seo.py so both sources
    # produce the same clean shape regardless of the underlying model.
    cleaned = line.strip()
    for prefix in ("- ", "* ", "\u2022 "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    stripped_num = ""
    i = 0
    while i < len(cleaned) and cleaned[i].isdigit():
        stripped_num += cleaned[i]
        i = i + 1
    if stripped_num != "" and i < len(cleaned) and cleaned[i] in ".)":
        cleaned = cleaned[i + 1:].strip()
    return cleaned


def _parse_structured_response(raw_text):
    # Parses the "TITLE: / META: / RECOMMENDATIONS:" format requested in
    # the prompt into {"suggested_title", "suggested_meta_description",
    # "recommendations"}. Falls back to treating the whole response as a
    # plain recommendation list if the model doesn't follow the format,
    # so a malformed structure never means "no recommendations at all".
    lines = raw_text.strip().split("\n")

    suggested_title = None
    suggested_meta_description = None
    recommendations = []
    in_recommendations = False

    for line in lines:
        stripped = line.strip()
        if stripped == "":
            continue
        upper = stripped.upper()
        if upper.startswith("TITLE:"):
            suggested_title = stripped.split(":", 1)[1].strip()
            in_recommendations = False
            continue
        if upper.startswith("META:"):
            suggested_meta_description = stripped.split(":", 1)[1].strip()
            in_recommendations = False
            continue
        if upper.startswith("RECOMMENDATIONS:"):
            in_recommendations = True
            continue
        if in_recommendations:
            cleaned = _clean_bullet_line(stripped)
            if cleaned != "":
                recommendations.append(cleaned)

    if len(recommendations) == 0 and suggested_title is None and suggested_meta_description is None:
        for line in lines:
            cleaned = _clean_bullet_line(line)
            if cleaned != "":
                recommendations.append(cleaned)

    return {
        "suggested_title": suggested_title,
        "suggested_meta_description": suggested_meta_description,
        "recommendations": recommendations,
    }


def get_seo_recommendations(content, keyword=None):
    # Returns the same shape as gemini_seo.get_seo_recommendations():
    #   {
    #     "available": True/False,
    #     "recommendations": ["...", "...", ...],
    #     "message": "...",
    #   }
    api_key = _get_api_key()
    if api_key is None:
        return {
            "available": False,
            "recommendations": [],
            "message": "AI recommendations unavailable: " + STATUS,
        }

    body_text = content.get("body_text", "")
    if body_text.strip() == "":
        return {
            "available": False,
            "recommendations": [],
            "message": "AI recommendations unavailable: no body content to analyze.",
        }

    prompt = _build_prompt(content, keyword)

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        # These two headers are optional but recommended by OpenRouter -
        # they show up in your OpenRouter dashboard/rankings and don't
        # need to point anywhere real for a local dev tool.
        "HTTP-Referer": os.environ.get("OPENROUTER_SITE_URL", "http://localhost:8000"),
        "X-Title": os.environ.get("OPENROUTER_SITE_NAME", "SEO Analyzer"),
    }
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    try:
        response = requests.post(OPENROUTER_API_URL, headers=headers, json=body, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        # Covers timeouts, connection errors, bad status codes (401 for a
        # wrong key, 402 for no credits, 429 for rate limits, etc.)
        _log("OpenRouter call failed: " + str(error))
        return {
            "available": False,
            "recommendations": [],
            "message": "AI recommendations unavailable: call failed - " + str(error),
        }

    try:
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        _log("OpenRouter response was malformed: " + str(error))
        return {
            "available": False,
            "recommendations": [],
            "message": "AI recommendations unavailable: unexpected response from OpenRouter - " + str(error),
        }

    if not raw_text or raw_text.strip() == "":
        return {
            "available": False,
            "recommendations": [],
            "message": "AI recommendations unavailable: the model returned an empty response.",
        }

    parsed = _parse_structured_response(raw_text)
    recommendations = parsed["recommendations"]
    if len(recommendations) == 0 and parsed["suggested_title"] is None and parsed["suggested_meta_description"] is None:
        return {
            "available": False,
            "recommendations": [],
            "message": "AI recommendations unavailable: could not parse a response.",
        }

    return {
        "available": True,
        "recommendations": recommendations,
        "suggested_title": parsed["suggested_title"],
        "suggested_meta_description": parsed["suggested_meta_description"],
        "message": "Generated " + str(len(recommendations)) + " recommendation(s) using " + OPENROUTER_MODEL + " (via OpenRouter).",
    }


# ---------------------------------------------------------------
# This block only runs when you execute this file directly.
# ---------------------------------------------------------------
if __name__ == "__main__":
    fake_content = {
        "title": "Best Home Coffee Brewing Methods",
        "meta_description": "Learn the best home coffee brewing methods for beginners.",
        "headings": {"h1": ["Best Home Coffee Brewing Methods"], "h2": ["Pour-Over", "Cold Brew"]},
        "word_count": 180,
        "body_text": ("Pour-over brewing gives full control over water temperature and time. "
                       "Cold brew coffee is a different method entirely, and takes much longer "
                       "to prepare, usually steeping for twelve to eighteen hours."),
    }
    result = get_seo_recommendations(fake_content, keyword="home coffee brewing")
    print(result)
