# site_crawler.py
# Runs the existing single-page analysis (extractor + rules + semantic +
# keywords + scoring) across an entire website instead of just one page,
# then rolls the results up into one site-wide report.
#
# How it decides which pages to visit:
#   - Starts at the URL the user gave us.
#   - On each page, pulls every same-domain link out of the HTML
#     (extractor.extract_internal_links) and queues up the ones we
#     haven't seen yet.
#   - This is a breadth-first crawl (page-by-page "layers" out from the
#     start page) capped at `max_pages`, so a huge site never turns into
#     an unbounded crawl - it just analyzes the most reachable pages
#     first, which are usually also the most important ones (linked from
#     the homepage / main nav).
#
# Cost/time control for the AI sources:
#   - Every page gets the full rule-based + semantic + keyword + LOCAL
#     recommendation treatment (all free, all offline-safe).
#   - Gemini and OpenRouter are NOT called once per page - on a 20-page
#     site that would be 40 extra API calls per site scan. Instead they
#     are called ONCE, on whichever single page scored the lowest, since
#     that's the page where an AI rewrite is most likely to matter. This
#     is stated explicitly in the response so it's never a silent
#     limitation.
#   - Because of that, per-page scores in `pages` do NOT include the
#     "AI Assessment" category that /analyze adds when Gemini/OpenRouter
#     are available (see app.py + ai_scoring.py) - it's simply excluded
#     from each page's score, the same way a missing keyword excludes
#     "Keyword Usage". Only the single site-wide `ai_recommendations`
#     block (grounded in the worst page) reflects an AI opinion at all.

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from extractor import fetch_html, extract_content, extract_internal_links
from rules import run_all_checks
from semantic import check_semantic_coverage
from scoring import score_report
from keywords import suggest_keywords
from local_recommendations import get_local_recommendations
from gemini_seo import get_seo_recommendations as get_gemini_recommendations
from openrouter_seo import get_seo_recommendations as get_openrouter_recommendations


def _log(message):
    print("[site_crawler] " + message, file=sys.stderr)


DEFAULT_MAX_PAGES = 15
MAX_PAGES_HARD_LIMIT = 40  # even if a caller asks for more, never crawl past this in one request
CRAWL_WORKERS = 5


def _analyze_one_page(url, keyword):
    # Fetches + analyzes exactly one page, reusing the same pipeline as
    # the single-page /analyze endpoint. Returns (page_report, links) so
    # the crawler can both record the result and discover new URLs from
    # the same HTML fetch - no double-downloading a page just to look at
    # its links separately.
    html, ssl_unverified = fetch_html(url)
    content = extract_content(html, url)
    content["ssl_unverified"] = ssl_unverified

    checks = run_all_checks(content, keyword)
    checks.append(check_semantic_coverage(content, keyword))
    report = score_report(checks)

    keyword_suggestions = suggest_keywords(content, keyword)
    report["keyword_suggestions"] = keyword_suggestions
    report["local_recommendations"] = get_local_recommendations(content, checks, keyword_suggestions, keyword)

    report["page"] = {
        "url": url,
        "title": content["title"],
        "meta_description": content["meta_description"],
        "word_count": content["word_count"],
        "h1_count": len(content["headings"]["h1"]),
        "h2_count": len(content["headings"]["h2"]),
    }

    links = extract_internal_links(html, url)
    return report, links


def _normalize_start_url(url):
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url


def _aggregate_category_scores(page_reports):
    # Averages each category across every successfully-analyzed page,
    # same "sum then divide" approach as scoring.py, just one level up.
    totals = {}
    counts = {}
    for page in page_reports:
        for category, score in page["category_scores"].items():
            if score is None:
                continue
            totals[category] = totals.get(category, 0) + score
            counts[category] = counts.get(category, 0) + 1

    averaged = {}
    for category in totals:
        averaged[category] = round(totals[category] / counts[category], 1)
    return averaged


def _aggregate_top_issues(page_reports, max_issues=8):
    # Finds which specific checks are failing across the MOST pages
    # site-wide (e.g. "12 of 15 pages are missing alt text") - this is
    # usually more actionable than per-page detail, since it points at
    # a systemic template/pattern problem rather than a one-off.
    issue_data = {}  # check name -> {"count": int, "fix": str, "score_sum": float}

    for page in page_reports:
        for check in page["checks"]:
            # Not-applicable checks (score/passed is None - usually: no
            # keyword supplied for that page) are neither a pass nor a
            # real site-wide issue - skip them rather than let a None
            # score break the "worst pages" math below.
            if check["score"] is None:
                continue
            if check["passed"]:
                continue
            name = check["name"]
            if name not in issue_data:
                issue_data[name] = {"count": 0, "fix": check["fix"], "score_sum": 0}
            issue_data[name]["count"] += 1
            issue_data[name]["score_sum"] += check["score"]

    issues = []
    for name, data in issue_data.items():
        issues.append({
            "check": name,
            "pages_affected": data["count"],
            "pages_affected_pct": round((data["count"] / len(page_reports)) * 100) if page_reports else 0,
            "average_score": round(data["score_sum"] / data["count"], 1),
            "fix": data["fix"],
        })

    # Manual sort, worst/most-widespread first - consistent with the
    # loop-based sorts used elsewhere in this project.
    n = len(issues)
    i = 0
    while i < n:
        j = 0
        while j < n - i - 1:
            if issues[j]["pages_affected"] < issues[j + 1]["pages_affected"]:
                issues[j], issues[j + 1] = issues[j + 1], issues[j]
            j += 1
        i += 1

    return issues[:max_issues]


def crawl_site(start_url, keyword=None, max_pages=DEFAULT_MAX_PAGES):
    # Returns:
    #   {
    #     "start_url": ...,
    #     "pages_crawled": int,
    #     "pages_requested": int,
    #     "site_overall_score": float,
    #     "site_category_scores": {...},
    #     "pages": [ {url, title, overall_score, category_scores, word_count, top_issue_names}, ... ],
    #     "top_site_issues": [ {check, pages_affected, pages_affected_pct, average_score, fix}, ... ],
    #     "worst_page": {...} | None,
    #     "ai_recommendations": {"sources": {"gemini": ..., "openrouter": ...}, "based_on_page": url} | None,
    #     "errors": [ {url, error}, ... ],
    #   }
    if max_pages > MAX_PAGES_HARD_LIMIT:
        max_pages = MAX_PAGES_HARD_LIMIT
    if max_pages < 1:
        max_pages = 1

    start_url = _normalize_start_url(start_url)

    visited = set()
    queued = set([start_url])
    queue = [start_url]

    page_reports = []
    errors = []

    lock = threading.Lock()

    while queue and len(visited) < max_pages:
        # Pull the next batch to fetch concurrently - up to CRAWL_WORKERS
        # at a time, and never more than we still have budget for.
        remaining_budget = max_pages - len(visited)
        batch = queue[:min(len(queue), CRAWL_WORKERS, remaining_budget)]
        queue = queue[len(batch):]

        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            future_to_url = {pool.submit(_analyze_one_page, url, keyword): url for url in batch}
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                with lock:
                    visited.add(url)
                try:
                    report, links = future.result()
                except Exception as error:
                    _log("failed to analyze " + url + ": " + str(error))
                    with lock:
                        errors.append({"url": url, "error": str(error)})
                    continue

                with lock:
                    page_reports.append(report)
                    for link in links:
                        if link not in visited and link not in queued and len(queued) < max_pages * 3:
                            # cap the queue too, not just visited count - keeps
                            # memory bounded on sites with huge nav menus
                            queued.add(link)
                            queue.append(link)

    if len(page_reports) == 0:
        return {
            "start_url": start_url,
            "pages_crawled": 0,
            "pages_requested": max_pages,
            "site_overall_score": 0,
            "site_category_scores": {},
            "pages": [],
            "top_site_issues": [],
            "worst_page": None,
            "ai_recommendations": None,
            "errors": errors,
        }

    total_score = 0
    for page in page_reports:
        total_score += page["overall_score"]
    site_overall_score = round(total_score / len(page_reports), 1)

    site_category_scores = _aggregate_category_scores(page_reports)
    top_site_issues = _aggregate_top_issues(page_reports)

    page_summaries = []
    worst_page_report = page_reports[0]
    for page in page_reports:
        failing_names = [c["name"] for c in page["checks"] if not c["passed"]]
        page_summaries.append({
            "url": page["page"]["url"],
            "title": page["page"]["title"],
            "overall_score": page["overall_score"],
            "category_scores": page["category_scores"],
            "word_count": page["page"]["word_count"],
            "failing_checks": failing_names,
        })
        if page["overall_score"] < worst_page_report["overall_score"]:
            worst_page_report = page

    # Manual sort of page_summaries, weakest page first, so the caller
    # can show "which pages need attention" without re-sorting itself.
    n = len(page_summaries)
    i = 0
    while i < n:
        j = 0
        while j < n - i - 1:
            if page_summaries[j]["overall_score"] > page_summaries[j + 1]["overall_score"]:
                page_summaries[j], page_summaries[j + 1] = page_summaries[j + 1], page_summaries[j]
            j += 1
        i += 1

    # AI recommendations: one call each to Gemini and OpenRouter, based
    # on whichever page scored lowest site-wide (see module docstring for
    # why we don't do this per-page). Both already degrade gracefully to
    # an "unavailable" result on their own if no key is configured.
    worst_content_stub = {
        "title": worst_page_report["page"]["title"],
        "meta_description": worst_page_report["page"]["meta_description"],
        "headings": {"h1": [], "h2": []},
        "word_count": worst_page_report["page"]["word_count"],
        "body_text": "",
    }
    # We didn't keep the full body_text on the lightweight page summary
    # above (to keep the crawl's memory footprint down across many
    # pages) - re-fetch just the one worst page's content for the AI
    # prompt, which needs the real body text to ground its suggestions.
    ai_recommendations = None
    try:
        html, _ssl = fetch_html(worst_page_report["page"]["url"])
        worst_full_content = extract_content(html, worst_page_report["page"]["url"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            gemini_future = pool.submit(get_gemini_recommendations, worst_full_content, keyword)
            openrouter_future = pool.submit(get_openrouter_recommendations, worst_full_content, keyword)
            ai_recommendations = {
                "based_on_page": worst_page_report["page"]["url"],
                "sources": {
                    "gemini": gemini_future.result(),
                    "openrouter": openrouter_future.result(),
                },
            }
    except Exception as error:
        _log("could not generate site-wide AI recommendations: " + str(error))
        ai_recommendations = {
            "based_on_page": worst_page_report["page"]["url"],
            "sources": {
                "gemini": {"available": False, "recommendations": [], "message": "Unavailable: " + str(error)},
                "openrouter": {"available": False, "recommendations": [], "message": "Unavailable: " + str(error)},
            },
        }

    return {
        "start_url": start_url,
        "pages_crawled": len(page_reports),
        "pages_requested": max_pages,
        "site_overall_score": site_overall_score,
        "site_category_scores": site_category_scores,
        "pages": page_summaries,
        "top_site_issues": top_site_issues,
        "worst_page": {
            "url": worst_page_report["page"]["url"],
            "title": worst_page_report["page"]["title"],
            "overall_score": worst_page_report["overall_score"],
        },
        "ai_recommendations": ai_recommendations,
        "errors": errors,
    }


# ---------------------------------------------------------------
# This block only runs when you execute this file directly.
# ---------------------------------------------------------------
if __name__ == "__main__":
    import json
    result = crawl_site("https://example.com/", keyword=None, max_pages=5)
    print(json.dumps({k: v for k, v in result.items() if k != "ai_recommendations"}, indent=2))
