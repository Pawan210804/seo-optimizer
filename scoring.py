# scoring.py
# Takes the list of individual check results (from rules.py) and rolls
# them up into:
#   1) a score for each category (Technical SEO, Keyword Usage, etc.)
#   2) one final overall score out of 100
#
# Written with plain for-loops so every step is visible.

# How much each category is worth in the final score. These four
# numbers add up to 1.0 (100%).
CATEGORY_WEIGHTS = {
    "Technical SEO": 0.35,
    "Keyword Usage": 0.30,
    "Readability": 0.15,
    "Content Depth": 0.20,
}

# Which category each individual check belongs to.
CHECK_TO_CATEGORY = {
    "Title tag": "Technical SEO",
    "Meta description": "Technical SEO",
    "Heading structure": "Technical SEO",
    "Image alt text": "Technical SEO",
    "Links": "Technical SEO",
    "Keyword placement": "Keyword Usage",
    "Keyword density": "Keyword Usage",
    "Readability": "Readability",
    "Content length": "Content Depth",
    "Semantic coverage": "Content Depth",
}


def score_report(checks):
    # Step 1: collect all the scores that belong to each category
    category_totals = {}
    for category_name in CATEGORY_WEIGHTS:
        category_totals[category_name] = []

    for check in checks:
        check_name = check["name"]
        if check_name in CHECK_TO_CATEGORY:
            category_name = CHECK_TO_CATEGORY[check_name]
            category_totals[category_name].append(check["score"])

    # Step 2: average the scores in each category
    category_scores = {}
    for category_name in category_totals:
        scores_in_category = category_totals[category_name]
        if len(scores_in_category) > 0:
            total = 0
            for one_score in scores_in_category:
                total = total + one_score
            average = total / len(scores_in_category)
            category_scores[category_name] = round(average, 1)
        else:
            category_scores[category_name] = None

    # Step 3: combine the category scores into one overall score,
    # using the weights above
    overall_total = 0
    weight_used = 0
    for category_name in CATEGORY_WEIGHTS:
        weight = CATEGORY_WEIGHTS[category_name]
        score_for_category = category_scores[category_name]
        if score_for_category is not None:
            overall_total = overall_total + (score_for_category * weight)
            weight_used = weight_used + weight

    if weight_used > 0:
        overall_score = round(overall_total / weight_used, 1)
    else:
        overall_score = 0.0

    report = {
        "overall_score": overall_score,
        "category_scores": category_scores,
        "checks": checks,
    }
    return report
