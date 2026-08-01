# local_recommendations.py
# A third "recommendations" source, alongside gemini_seo.py and
# openrouter_seo.py - but this one calls no external API at all, so it
# is never subject to a missing key, a network error, or a quota limit.
# It always returns something.
#
# It works by:
#   1. Turning the weakest rule-based checks (from rules.py) into plain-
#      English recommendation sentences, worst-scoring first.
#   2. Pointing out 1-2 recurring phrases from suggest_keywords()
#      (keywords.py) that the page could weave in more deliberately -
#      those phrases come from spaCy noun-chunk/entity extraction when
#      spaCy is installed, or the manual n-gram fallback when it isn't.
#
# Nothing here is generated or invented - every sentence is derived
# directly from data already computed elsewhere in the app, so this
# source is 100% grounded and 100% available, by construction.

NO_CHANGE_NEEDED = "No changes needed - this is already in good shape."


def _checks_to_recommendations(checks, max_items):
    # Takes the weakest checks (lowest score first) and turns their
    # "fix" field into standalone recommendation sentences. Checks that
    # already passed with "no change needed" are skipped - they're not
    # useful as a recommendation.
    candidates = []
    for check in checks:
        fix = check.get("fix", "")
        if fix == "" or fix == NO_CHANGE_NEEDED:
            continue
        candidates.append(check)

    # Simple manual sort (ascending score = weakest first), consistent
    # with the "visible algorithm" style used elsewhere in this project.
    n = len(candidates)
    i = 0
    while i < n:
        j = 0
        while j < n - i - 1:
            if candidates[j]["score"] > candidates[j + 1]["score"]:
                candidates[j], candidates[j + 1] = candidates[j + 1], candidates[j]
            j = j + 1
        i = i + 1

    recommendations = []
    for check in candidates[:max_items]:
        recommendations.append(check["name"] + ": " + check["fix"])
    return recommendations


def _keyword_suggestion_recommendation(keyword_suggestions, keyword):
    # Turns the top 1-2 recurring phrases from suggest_keywords() into
    # one recommendation sentence, so the local source also surfaces a
    # content/topic-coverage idea, not just technical fixes.
    if not keyword_suggestions:
        return None

    top_phrases = [s["phrase"] for s in keyword_suggestions[:2]]
    if len(top_phrases) == 0:
        return None

    phrase_text = " and ".join(top_phrases)
    if keyword:
        return ("The page already returns to \"" + phrase_text + "\" - consider working these "
                "phrases in more deliberately as supporting subtopics around \"" + keyword + "\".")
    return ("The page already returns to \"" + phrase_text + "\" - these recurring phrases could "
            "become subheadings or supporting subtopics.")


def _find_check(checks, name):
    for check in checks:
        if check.get("name") == name:
            return check
    return None


def _split_sentences(text):
    # Manual sentence split (no regex, matching the project's style):
    # walks the text and breaks after ". ", "! ", or "? ".
    sentences = []
    current = ""
    i = 0
    while i < len(text):
        current += text[i]
        if text[i] in ".!?" and (i + 1 >= len(text) or text[i + 1] == " "):
            sentences.append(current.strip())
            current = ""
        i += 1
    if current.strip() != "":
        sentences.append(current.strip())
    return sentences


def _suggest_title(content, keyword, checks):
    # Only offer a suggestion when the title check actually failed -
    # otherwise the current title is already fine, and this source never
    # invents a "better" version of something that isn't broken.
    title_check = _find_check(checks, "Title tag")
    if title_check is None or title_check.get("passed"):
        return None

    current = content.get("title", "").strip()
    h1_list = content.get("headings", {}).get("h1", [])
    base = current if current else (h1_list[0] if h1_list else "")
    if base == "":
        return None

    if keyword and keyword.lower() not in base.lower():
        candidate = base + " | " + keyword.title()
    else:
        candidate = base

    return candidate.strip()


def _suggest_meta_description(content, keyword, checks):
    # Same principle: only suggest a replacement when the current one
    # actually fails the check, and build it entirely out of sentences
    # that are already on the page - never invented copy.
    meta_check = _find_check(checks, "Meta description")
    if meta_check is None or meta_check.get("passed"):
        return None

    body_text = content.get("body_text", "")
    sentences = _split_sentences(body_text)
    if len(sentences) == 0:
        return None

    candidate = ""
    for sentence in sentences:
        if candidate == "":
            candidate = sentence
        elif len(candidate) + 1 + len(sentence) <= 160:
            candidate = candidate + " " + sentence
        else:
            break

    if keyword and keyword.lower() not in candidate.lower():
        # Prepend the keyword rather than invent new sentences, trimming
        # if needed to stay near the 150-160 character target.
        candidate = keyword.title() + ": " + candidate
        if len(candidate) > 160:
            candidate = candidate[:157].rsplit(" ", 1)[0] + "..."

    return candidate.strip() if candidate.strip() != "" else None


def get_local_recommendations(content, checks, keyword_suggestions=None, keyword=None, max_recommendations=6):
    # Returns the same shape as gemini_seo.get_seo_recommendations() /
    # openrouter_seo.get_seo_recommendations(), so all three sources can
    # be handled identically by the caller:
    #   {
    #     "available": True/False,
    #     "recommendations": ["...", "...", ...],
    #     "message": "...",
    #   }
    if not checks:
        return {
            "available": False,
            "recommendations": [],
            "message": "Local recommendations unavailable: no checks were run.",
        }

    slots_for_keyword_line = 1 if keyword_suggestions else 0
    recommendations = _checks_to_recommendations(checks, max_recommendations - slots_for_keyword_line)

    keyword_line = _keyword_suggestion_recommendation(keyword_suggestions, keyword)
    if keyword_line:
        recommendations.append(keyword_line)

    suggested_title = _suggest_title(content, keyword, checks)
    suggested_meta_description = _suggest_meta_description(content, keyword, checks)

    if len(recommendations) == 0 and suggested_title is None and suggested_meta_description is None:
        return {
            "available": True,
            "recommendations": [],
            "message": "Every rule-based check already passed - nothing further to recommend right now.",
        }

    return {
        "available": True,
        "recommendations": recommendations,
        "suggested_title": suggested_title,
        "suggested_meta_description": suggested_meta_description,
        "message": "Generated " + str(len(recommendations)) + " recommendation(s) from the rule checks and keyword patterns (offline, no API used).",
    }


# ---------------------------------------------------------------
# This block only runs when you execute this file directly.
# ---------------------------------------------------------------
if __name__ == "__main__":
    fake_checks = [
        {"name": "Title tag", "score": 40, "fix": "The title is quite short - rewrite it as a fuller, descriptive sentence around 50-60 characters."},
        {"name": "Links", "score": 100, "fix": NO_CHANGE_NEEDED},
        {"name": "Image alt text", "score": 60, "fix": "Add descriptive alt text to the 2 image(s) currently missing it."},
    ]
    fake_suggestions = [{"phrase": "pour over coffee", "count": 4}, {"phrase": "cold brew coffee", "count": 3}]
    print(get_local_recommendations({}, fake_checks, fake_suggestions, keyword="home coffee brewing"))
