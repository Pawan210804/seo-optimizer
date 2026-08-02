# rules.py
# Each function here checks ONE thing about the page (title length,
# keyword usage, readability, etc.) and returns a simple dictionary
# with a score out of 100, whether it passed, a message explaining
# why, and a "fix" - a short, concrete instruction for what to
# actually go and change. Plain if/else logic - nothing fancy.

import textstat
from extractor import naive_string_search, split_into_words

NO_CHANGE_NEEDED = "No changes needed - this is already in good shape."


def check_title(content, keyword=None):
    title = content["title"]
    length = len(title)

    if title == "":
        return {"name": "Title tag", "score": 0, "passed": False,
                "message": "No title tag found.",
                "fix": "Add a <title> tag to the page's <head>, 50-60 characters long"
                       + (", including the focus keyword '" + keyword + "'." if keyword else "."),
                "current": ""}

    if length >= 50 and length <= 60:
        score = 100
        message = "Title length is " + str(length) + " characters - in the ideal range (50-60)."
        fix = NO_CHANGE_NEEDED
    elif (length >= 40 and length < 50) or (length > 60 and length <= 70):
        score = 70
        message = "Title length is " + str(length) + " characters - a bit outside the ideal 50-60 range."
        if length < 50:
            fix = "Add a few more descriptive words to the title to bring it closer to 50-60 characters."
        else:
            fix = "Trim the title down closer to 50-60 characters so it doesn't get cut off in search results."
    else:
        score = 40
        message = "Title length is " + str(length) + " characters - too far from the ideal 50-60 range."
        if length < 40:
            fix = "The title is quite short - rewrite it as a fuller, descriptive sentence around 50-60 characters."
        else:
            fix = "The title is too long - shorten it to around 50-60 characters so it isn't truncated in search results."

    if keyword:
        if not naive_string_search(title.lower(), keyword.lower()):
            score = score - 30
            if score < 0:
                score = 0
            message = message + " Focus keyword '" + keyword + "' was not found in the title."
            fix = "Work the focus keyword '" + keyword + "' into the title, ideally near the beginning. " + fix

    passed = score >= 70
    return {"name": "Title tag", "score": score, "passed": passed, "message": message, "fix": fix, "current": title}


def check_meta_description(content, keyword=None):
    meta_description = content["meta_description"]
    length = len(meta_description)

    if meta_description == "":
        return {"name": "Meta description", "score": 0, "passed": False,
                "message": "No meta description found.",
                "fix": "Add a <meta name=\"description\"> tag with a 150-160 character summary of the page"
                       + (", including the focus keyword '" + keyword + "'." if keyword else "."),
                "current": ""}

    if length >= 150 and length <= 160:
        score = 100
        message = "Meta description is " + str(length) + " characters - ideal length."
        fix = NO_CHANGE_NEEDED
    elif (length >= 120 and length < 150) or (length > 160 and length <= 180):
        score = 70
        message = "Meta description is " + str(length) + " characters - close to ideal, could be tightened."
        if length < 150:
            fix = "Expand the meta description slightly to fill out closer to 150-160 characters."
        else:
            fix = "Tighten the meta description down closer to 150-160 characters."
    else:
        score = 40
        message = "Meta description is " + str(length) + " characters - outside the ideal 150-160 range."
        if length < 120:
            fix = "The meta description is too short - rewrite it as one or two full sentences (150-160 characters) that summarize the page."
        else:
            fix = "The meta description is too long and will be truncated in search results - cut it down to 150-160 characters."

    if keyword:
        if not naive_string_search(meta_description.lower(), keyword.lower()):
            score = score - 20
            if score < 0:
                score = 0
            message = message + " Focus keyword '" + keyword + "' was not found in the meta description."
            fix = "Work the focus keyword '" + keyword + "' into the meta description. " + fix

    passed = score >= 70
    return {"name": "Meta description", "score": score, "passed": passed, "message": message, "fix": fix, "current": meta_description}


def check_heading_structure(content):
    h1_list = content["headings"]["h1"]
    h2_list = content["headings"]["h2"]
    h1_count = len(h1_list)
    h2_count = len(h2_list)

    current_headings = "H1: " + (", ".join(h1_list) if h1_list else "(none)") + " | H2: " + (", ".join(h2_list) if h2_list else "(none)")

    if h1_count == 0:
        return {"name": "Heading structure", "score": 20, "passed": False,
                "message": "No H1 found - every page should have exactly one.",
                "fix": "Add exactly one <h1> tag that clearly states the page's main topic.",
                "current": current_headings}

    if h1_count > 1:
        message = "Found " + str(h1_count) + " H1 tags - there should be exactly one per page."
        return {"name": "Heading structure", "score": 50, "passed": False, "message": message,
                "fix": "Keep only one <h1> tag on the page and convert the rest to <h2> subheadings.",
                "current": current_headings}

    if h2_count == 0:
        message = "Exactly one H1 found, but no H2 subheadings - long content usually needs them."
        return {"name": "Heading structure", "score": 60, "passed": False, "message": message,
                "fix": "Break the body content up with a few <h2> subheadings, one per major section.",
                "current": current_headings}

    message = "Good structure: one H1 and " + str(h2_count) + " H2 subheading(s)."
    return {"name": "Heading structure", "score": 100, "passed": True, "message": message, "fix": NO_CHANGE_NEEDED,
            "current": current_headings}


def check_keyword_in_first_paragraph(content, keyword):
    if not keyword:
        # Previously this returned a free score of 100, which meant the
        # "Keyword Usage" category could hit a perfect score just by
        # never supplying a keyword at all - the opposite of a real SEO
        # audit. This is now marked not-applicable instead: scoring.py
        # excludes it from the category average entirely (rather than
        # crediting it), so skipping keyword targeting can no longer
        # inflate the score.
        return {"name": "Keyword placement", "score": None, "passed": None, "applicable": False,
                "message": "No focus keyword supplied - keyword placement cannot be scored.",
                "fix": "Provide a focus keyword to get placement guidance and a complete score."}

    all_words = split_into_words(content["body_text"])

    # collect just the first 150 words using a loop, instead of slicing
    first_150_words_list = []
    index = 0
    while index < len(all_words) and index < 150:
        first_150_words_list.append(all_words[index])
        index = index + 1

    first_150_text = " ".join(first_150_words_list).lower()

    if naive_string_search(first_150_text, keyword.lower()):
        message = "Focus keyword '" + keyword + "' appears within the first 150 words."
        return {"name": "Keyword placement", "score": 100, "passed": True, "message": message, "fix": NO_CHANGE_NEEDED}
    else:
        message = "Focus keyword '" + keyword + "' was not found in the first 150 words."
        fix = "Rework the opening paragraph so the focus keyword '" + keyword + "' appears naturally within the first 100-150 words."
        return {"name": "Keyword placement", "score": 30, "passed": False, "message": message, "fix": fix}


def check_keyword_density(content, keyword):
    word_count = content["word_count"]

    if not keyword or word_count == 0:
        # Same fix as check_keyword_in_first_paragraph: not-applicable,
        # not a free 100 - see the comment there for why.
        return {"name": "Keyword density", "score": None, "passed": None, "applicable": False,
                "message": "No focus keyword supplied - keyword density cannot be scored.",
                "fix": "Provide a focus keyword to get density guidance and a complete score."}

    body_words = split_into_words(content["body_text"].lower())
    keyword_words = split_into_words(keyword.lower())
    keyword_length = len(keyword_words)

    # The keyword might be more than one word (e.g. "home coffee brewing"),
    # so we slide a window of that size across the body text, word by word,
    # and check whether the words in that window match the keyword exactly.
    occurrences = 0
    position = 0
    while position <= len(body_words) - keyword_length:
        window = body_words[position:position + keyword_length]
        if window == keyword_words:
            occurrences = occurrences + 1
        position = position + 1

    density = (occurrences / word_count) * 100

    if density >= 0.5 and density <= 2.5:
        score = 100
        message = "Keyword density is " + str(round(density, 2)) + "% - within the healthy 0.5-2.5% range."
        fix = NO_CHANGE_NEEDED
    elif density < 0.5:
        score = 50
        message = "Keyword density is " + str(round(density, 2)) + "% - a little low, keyword may be under-used."
        fix = "Work the focus keyword '" + keyword + "' into a few more sentences naturally (aim for 0.5-2.5% density)."
    else:
        score = 30
        message = "Keyword density is " + str(round(density, 2)) + "% - likely keyword stuffing."
        fix = "Cut back on repeating '" + keyword + "' - swap some repeats for synonyms or pronouns so it reads naturally."

    passed = score >= 70
    return {"name": "Keyword density", "score": score, "passed": passed, "message": message, "fix": fix}


def check_image_alt_text(content):
    images_total = content["images_total"]
    images_missing_alt = content["images_missing_alt"]

    if images_total == 0:
        return {"name": "Image alt text", "score": 100, "passed": True,
                "message": "No images found on the page - check not applicable.",
                "fix": NO_CHANGE_NEEDED}

    covered = images_total - images_missing_alt
    percentage = (covered / images_total) * 100
    message = str(covered) + "/" + str(images_total) + " images have alt text (" + str(round(percentage)) + "% coverage)."

    # Real accessibility/SEO audits treat this as all-or-nothing-ish:
    # every image either carries meaning (needs alt text) or is
    # decorative (needs alt=""), so partial coverage still means real
    # images are invisible to search engines and screen readers. Full
    # marks now require 100%, not 80%, and the passing bar is raised too.
    if percentage >= 100:
        score = 100
    elif percentage >= 90:
        score = 80
    else:
        score = percentage

    passed = score >= 90
    if passed:
        fix = NO_CHANGE_NEEDED
    else:
        fix = "Add descriptive alt text to the " + str(images_missing_alt) + " image(s) currently missing it."
    return {"name": "Image alt text", "score": score, "passed": passed, "message": message, "fix": fix}


def check_links(content):
    internal_links = content["internal_links"]
    external_links = content["external_links"]

    if internal_links == 0 and external_links == 0:
        return {"name": "Links", "score": 30, "passed": False,
                "message": "No internal or external links found.",
                "fix": "Add 1-2 internal links to related pages on your own site, plus one relevant external link."}

    if internal_links == 0:
        message = str(external_links) + " external link(s) found, but no internal links."
        return {"name": "Links", "score": 60, "passed": False, "message": message,
                "fix": "Add at least one internal link to another page on your site so readers and crawlers can navigate further."}

    if internal_links >= 2:
        score = 100
        fix = NO_CHANGE_NEEDED
    else:
        score = 70
        fix = "Add one more internal link (2+ is ideal) to strengthen the page's internal linking."

    message = str(internal_links) + " internal link(s), " + str(external_links) + " external link(s)."
    passed = score >= 70
    return {"name": "Links", "score": score, "passed": passed, "message": message, "fix": fix}


def check_indexability(content):
    # The single highest-priority technical check in a real audit: if a
    # page is set to noindex, none of the other scores matter - the page
    # simply will not appear in search results at all. Scored separately
    # from, and much more harshly than, everything else so it can never
    # get lost in an averaged category score.
    if content.get("is_noindex"):
        return {"name": "Indexability", "score": 0, "passed": False,
                "message": "This page has a 'noindex' directive - search engines will not list it at all.",
                "fix": "Remove 'noindex' from the <meta name=\"robots\"> tag unless you are deliberately "
                       "hiding this page from search results.",
                "current": content.get("robots_content", "")}
    return {"name": "Indexability", "score": 100, "passed": True,
            "message": "No 'noindex' directive found - the page is eligible to be indexed.",
            "fix": NO_CHANGE_NEEDED, "current": content.get("robots_content", "")}


def check_canonical_tag(content):
    canonical_url = content.get("canonical_url", "")
    if canonical_url == "":
        return {"name": "Canonical tag", "score": 60, "passed": False,
                "message": "No canonical tag found.",
                "fix": "Add a <link rel=\"canonical\"> tag pointing to the preferred URL for this page, "
                       "to avoid duplicate-content issues if it's reachable at more than one URL.",
                "current": ""}
    return {"name": "Canonical tag", "score": 100, "passed": True,
            "message": "Canonical tag found: " + canonical_url,
            "fix": NO_CHANGE_NEEDED, "current": canonical_url}


def check_mobile_friendliness(content):
    # Google has used mobile-first indexing for years - a missing
    # viewport tag means the page isn't rendering responsively, which is
    # both a ranking and a usability problem.
    if content.get("has_viewport"):
        return {"name": "Mobile friendliness", "score": 100, "passed": True,
                "message": "A responsive viewport meta tag is present.",
                "fix": NO_CHANGE_NEEDED}
    return {"name": "Mobile friendliness", "score": 40, "passed": False,
            "message": "No viewport meta tag found - the page likely isn't rendering responsively on mobile.",
            "fix": "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> to the <head>."}


def check_social_tags(content):
    if content.get("has_open_graph"):
        return {"name": "Social sharing tags", "score": 100, "passed": True,
                "message": "Open Graph tags found - links will render with a proper preview when shared.",
                "fix": NO_CHANGE_NEEDED}
    return {"name": "Social sharing tags", "score": 65, "passed": False,
            "message": "No Open Graph tags (og:title / og:description / og:image) found.",
            "fix": "Add og:title, og:description, and og:image meta tags so shared links show a proper preview."}


def check_structured_data(content):
    if content.get("has_structured_data"):
        return {"name": "Structured data", "score": 100, "passed": True,
                "message": "JSON-LD structured data found - the page is eligible for rich results.",
                "fix": NO_CHANGE_NEEDED}
    return {"name": "Structured data", "score": 55, "passed": False,
            "message": "No JSON-LD structured data (schema.org) found on the page.",
            "fix": "Add JSON-LD structured data (e.g. Article, Product, or FAQPage schema, whichever fits "
                   "the content) so the page is eligible for rich results in search."}


def check_readability(content):
    word_count = content["word_count"]

    if word_count < 50:
        return {"name": "Readability", "score": 50, "passed": False,
                "message": "Not enough body text to reliably score readability.",
                "fix": "Add more body content - readability scoring needs at least 50 words to be meaningful."}

    ease = textstat.flesch_reading_ease(content["body_text"])

    if ease >= 60:
        score = 100
        message = "Flesch Reading Ease score is " + str(round(ease)) + " - easy to read for a general audience."
        fix = NO_CHANGE_NEEDED
    elif ease >= 40:
        score = 70
        message = "Flesch Reading Ease score is " + str(round(ease)) + " - fairly complex, consider shorter sentences."
        fix = "Break a few of the longer sentences into shorter ones and simplify complex words where possible."
    else:
        score = 40
        message = "Flesch Reading Ease score is " + str(round(ease)) + " - difficult to read, sentences may be too dense."
        fix = "Rewrite the densest paragraphs with shorter sentences, simpler words, and fewer nested clauses."

    passed = score >= 70
    return {"name": "Readability", "score": score, "passed": passed, "message": message, "fix": fix}


def check_content_length(content):
    word_count = content["word_count"]

    if word_count >= 900:
        score = 100
        message = str(word_count) + " words - solid length for in-depth content."
        fix = NO_CHANGE_NEEDED
    elif word_count >= 500:
        score = 75
        message = str(word_count) + " words - adequate, but longer content often ranks better for competitive terms."
        fix = "Consider expanding toward 900+ words by covering another subtopic or answering a related question."
    else:
        score = 40
        message = str(word_count) + " words - thin content, likely too short to cover the topic properly."
        fix = "Expand the content substantially - aim for at least 500-900 words so the topic is properly covered."

    passed = score >= 70
    return {"name": "Content length", "score": score, "passed": passed, "message": message, "fix": fix}


def run_all_checks(content, keyword=None):
    # Build up the list of results one check at a time using a normal list
    # and .append(), instead of putting them all in one big expression.
    checks = []

    checks.append(check_indexability(content))
    checks.append(check_title(content, keyword))
    checks.append(check_meta_description(content, keyword))
    checks.append(check_heading_structure(content))
    checks.append(check_keyword_in_first_paragraph(content, keyword))
    checks.append(check_keyword_density(content, keyword))
    checks.append(check_image_alt_text(content))
    checks.append(check_links(content))
    checks.append(check_canonical_tag(content))
    checks.append(check_mobile_friendliness(content))
    checks.append(check_social_tags(content))
    checks.append(check_structured_data(content))
    checks.append(check_readability(content))
    checks.append(check_content_length(content))

    return checks
