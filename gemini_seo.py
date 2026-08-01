# gemini_seo.py
# Uses Google's Gemini API (via the google-genai SDK) to generate a short
# list of AI-written SEO recommendations for a page, given its extracted
# content and an optional focus keyword.
#
# Follows the same graceful-degradation pattern as semantic.py:
# - The API key is read from an environment variable, never hardcoded.
# - If the key is missing, or the call fails for any reason (bad key,
#   network issue, quota limit), this does NOT crash the app - it just
#   returns an "unavailable" result and says why, and the rest of the
#   report still works normally.
# - Setup problems are logged to the server console with a [gemini]
#   prefix so they're easy to find, same as [semantic].

import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _log(message):
    print("[gemini] " + message, file=sys.stderr)


GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

STATUS = "not attempted yet"

_client = None
_client_load_failed = False


def _get_client():
    # Loads the genai client once and caches it, instead of re-creating
    # it (and re-validating the key) on every single request.
    global _client, _client_load_failed, STATUS

    if _client is not None:
        return _client
    if _client_load_failed:
        return None

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        STATUS = "not configured - set GEMINI_API_KEY in .env"
        _client_load_failed = True
        return None

    try:
        from google import genai
    except Exception as error:
        STATUS = "google-genai is not installed - run: pip install google-genai"
        _log("google-genai is not installed or failed to import: " + str(error))
        _client_load_failed = True
        return None

    try:
        _client = genai.Client(api_key=api_key)
        STATUS = "ok"
        return _client
    except Exception as error:
        STATUS = "failed to create client: " + str(error)
        _log("Gemini client could not be created: " + str(error))
        _client_load_failed = True
        return None


def _limit_text(text, max_characters):
    # Keeps the prompt a reasonable size instead of sending the whole
    # page - a simple truncation, same spirit as limit_text_length()
    # in semantic.py.
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
    # being asked not to.
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
    # plain recommendation list if the model doesn't follow the format
    # (e.g. an older/smaller model that ignores formatting instructions),
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

    # Fallback: the model ignored the TITLE:/META:/RECOMMENDATIONS:
    # format entirely - treat every non-empty line as a recommendation
    # instead of silently returning nothing.
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
    # Returns:
    #   {
    #     "available": True/False,
    #     "recommendations": ["...", "...", ...],
    #     "message": "...",
    #   }
    # "available" is False whenever the recommendations could not be
    # generated for any reason - missing key, network error, empty
    # response - so the caller can render a clean "unavailable" state
    # instead of crashing or showing a blank list.
    client = _get_client()
    if client is None:
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

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
    except Exception as error:
        _log("Gemini call failed: " + str(error))
        return {
            "available": False,
            "recommendations": [],
            "message": "AI recommendations unavailable: call failed - " + str(error),
        }

    raw_text = getattr(response, "text", None)
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
        "message": "Generated " + str(len(recommendations)) + " recommendation(s) using " + GEMINI_MODEL + ".",
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
