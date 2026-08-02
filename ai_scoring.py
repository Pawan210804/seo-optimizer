# ai_scoring.py
# Turns the numeric "SCORE:" line each AI source (gemini_seo.py,
# openrouter_seo.py) now returns into a single check-shaped result that
# can be dropped straight into the same `checks` list rules.py builds -
# scoring.py then folds it into the overall score like any other check,
# under its own "AI Assessment" category.
#
# Same graceful-degradation philosophy as the rest of the app:
#   - If neither source returned a usable score (no API key configured,
#     a call failed, or the model didn't follow the requested format),
#     this returns score=None / applicable=False - NOT a free pass and
#     NOT a zero - so scoring.py excludes "AI Assessment" from the
#     overall score entirely and renormalizes the other categories,
#     exactly like it already does for keyword checks when no keyword
#     is supplied.
#   - If only one source is available, its score is used alone.
#   - If both are available, they're averaged - two independent models
#     grading the same page is a steadier signal than either alone.

NO_CHANGE_NEEDED = "No changes needed - this is already in good shape."


def build_ai_assessment_check(gemini_result, openrouter_result):
    scores = []
    sources = []

    if gemini_result and gemini_result.get("available") and gemini_result.get("seo_score") is not None:
        scores.append(gemini_result["seo_score"])
        sources.append("Gemini")

    if openrouter_result and openrouter_result.get("available") and openrouter_result.get("seo_score") is not None:
        scores.append(openrouter_result["seo_score"])
        sources.append("OpenRouter")

    if len(scores) == 0:
        return {
            "name": "AI Assessment", "score": None, "passed": None, "applicable": False,
            "message": "No AI-graded score available - neither Gemini nor OpenRouter returned a usable "
                       "score (missing API key, failed call, or an unparseable response).",
            "fix": "Configure GEMINI_API_KEY or OPENROUTER_API_KEY in .env to include an AI-graded "
                   "quality score in the overall score.",
        }

    total = 0
    for s in scores:
        total = total + s
    average_score = round(total / len(scores), 1)

    sources_text = " & ".join(sources)
    passed = average_score >= 70

    message = ("AI-graded overall SEO quality from " + sources_text + ": " + str(average_score) + "/100"
               + (" (averaged across " + str(len(scores)) + " models)." if len(scores) > 1 else "."))

    if passed:
        fix = NO_CHANGE_NEEDED
    else:
        fix = ("The AI grader(s) rated this page's overall quality below a passing bar - see the "
               "prioritized recommendations under \"AI recommendations\" above for the specific issues "
               "they flagged.")

    return {"name": "AI Assessment", "score": average_score, "passed": passed, "message": message, "fix": fix}


# ---------------------------------------------------------------
# This block only runs when you execute this file directly.
# ---------------------------------------------------------------
if __name__ == "__main__":
    fake_gemini = {"available": True, "seo_score": 62, "recommendations": []}
    fake_openrouter = {"available": True, "seo_score": 58, "recommendations": []}
    print(build_ai_assessment_check(fake_gemini, fake_openrouter))
    print(build_ai_assessment_check({"available": False}, {"available": False}))
