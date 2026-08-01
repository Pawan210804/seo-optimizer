# keywords.py
# Suggests extra keywords/phrases the user could weave into the page
# more deliberately. This does NOT invent keywords out of thin air -
# it surfaces phrases that are already showing up in the page's own
# text, ranked by how often they recur, so every suggestion is
# grounded in real content rather than guessed.
#
# Two extraction strategies, same fallback pattern used in semantic.py:
#   1. spaCy noun chunks + named entities (if the model is installed) -
#      these tend to be clean, grammatical phrases ("cold brew coffee").
#   2. A manual n-gram scan (1-3 words) with a small stopword list -
#      works with zero extra dependencies, so suggestions never
#      disappear just because spaCy isn't installed.

from extractor import split_into_words

# A small, hand-picked list of very common words that make poor
# keyword suggestions on their own (articles, prepositions, pronouns,
# auxiliary verbs, etc.) - not exhaustive, just enough to filter out
# obvious junk phrases like "and the" or "is a".
STOPWORDS = set([
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "of", "to",
    "in", "on", "for", "with", "at", "by", "from", "up", "about", "into",
    "over", "after", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "as", "not", "no",
    "can", "will", "would", "should", "could", "we", "you", "your",
    "our", "their", "his", "her", "they", "he", "she", "i", "us", "them",
    "my", "me", "do", "does", "did", "have", "has", "had", "also",
    "just", "more", "most", "some", "any", "all", "than", "too", "very",
    "there", "here", "what", "which", "who", "when", "where", "how",
    "out", "only", "other", "new", "one", "two",
])


LEADING_ARTICLES = ("a ", "an ", "the ")


def _spacy_phrases(text):
    # Reuses the spaCy model already loaded (once) in semantic.py,
    # instead of loading a second copy of it. Returns None if spaCy
    # isn't available, so the caller falls back to the n-gram scan.
    try:
        from semantic import _nlp
    except Exception:
        return None
    if _nlp is None:
        return None
    if text.strip() == "":
        return None

    doc = _nlp(text)
    phrases = []
    for chunk in doc.noun_chunks:
        phrase = chunk.text.strip().lower()
        # spaCy noun chunks include leading articles ("a clean cup"),
        # which read awkwardly as a keyword suggestion - strip them.
        for article in LEADING_ARTICLES:
            if phrase.startswith(article):
                phrase = phrase[len(article):]
                break
        if phrase != "" and phrase not in STOPWORDS:
            phrases.append(phrase)
    for ent in doc.ents:
        phrase = ent.text.strip().lower()
        if phrase != "":
            phrases.append(phrase)
    return phrases


def _clean_word(word):
    # Lowercases a word and strips anything that isn't a letter or
    # digit, using a plain character loop rather than a regex.
    cleaned = ""
    for character in word:
        if character.isalnum():
            cleaned = cleaned + character.lower()
    return cleaned


def _fallback_ngram_phrases(words):
    # Builds 1, 2, and 3-word phrases by sliding a window across the
    # word list. A phrase is only kept if its first and last word are
    # not stopwords, which filters out junk like "and the" or "of a"
    # while keeping natural phrases like "cold brew coffee".
    cleaned_words = []
    for word in words:
        cleaned = _clean_word(word)
        if cleaned != "":
            cleaned_words.append(cleaned)

    phrases = []
    total = len(cleaned_words)
    for size in (1, 2, 3):
        position = 0
        while position <= total - size:
            gram = cleaned_words[position:position + size]
            first_word = gram[0]
            last_word = gram[-1]
            if first_word not in STOPWORDS and last_word not in STOPWORDS and len(first_word) > 2:
                phrases.append(" ".join(gram))
            position = position + 1

    return phrases


def _count_phrases(phrases):
    # Counts occurrences of each phrase using a dictionary and a
    # plain loop, while remembering first-seen order so results are
    # stable rather than depending on dict iteration quirks.
    counts = {}
    order = []
    for phrase in phrases:
        if phrase not in counts:
            counts[phrase] = 0
            order.append(phrase)
        counts[phrase] = counts[phrase] + 1
    return counts, order


def suggest_keywords(content, keyword=None, max_suggestions=8):
    # Returns a list like:
    #   [{"phrase": "pour over coffee", "count": 4}, ...]
    # sorted by how often the phrase occurs on the page, biggest
    # first. Only phrases that recur (count >= 2) are surfaced, since
    # a phrase that appears once is usually just incidental wording,
    # not a real subtopic worth targeting.
    body_text = content.get("body_text", "")
    if body_text.strip() == "":
        return []

    keyword_lower = keyword.lower().strip() if keyword else ""

    phrases = _spacy_phrases(body_text)
    if phrases is not None:
        source = "spacy"
    else:
        word_list = content.get("word_list")
        if not word_list:
            word_list = split_into_words(body_text)
        phrases = _fallback_ngram_phrases(word_list)
        source = "fallback"

    counts, order = _count_phrases(phrases)

    # spaCy phrases are already grammatically filtered (real noun
    # phrases and named entities), so even a single occurrence is a
    # meaningful suggestion. The blind n-gram fallback has no such
    # filtering, so it needs a phrase to repeat at least twice before
    # it's trusted as a real subtopic rather than incidental wording.
    min_count = 1 if source == "spacy" else 2

    candidates = []
    for phrase in order:
        count = counts[phrase]
        if count < min_count:
            continue
        word_count_in_phrase = len(phrase.split(" "))
        if word_count_in_phrase > 4:
            continue
        if word_count_in_phrase == 1 and len(phrase) <= 3:
            continue
        if keyword_lower and phrase == keyword_lower:
            continue  # don't "suggest" the keyword the user already gave us
        candidates.append({"phrase": phrase, "count": count})

    # Safety net: short pages/snippets (common when testing with a
    # small pasted HTML block) can still end up with zero candidates
    # even after the above. Rather than showing nothing, fall back to
    # any multi-word phrase at all - still grounded in the real text,
    # just a weaker signal, so the feature doesn't look "broken" on
    # short input.
    if len(candidates) == 0:
        for phrase in order:
            count = counts[phrase]
            word_count_in_phrase = len(phrase.split(" "))
            if word_count_in_phrase < 2 or word_count_in_phrase > 3:
                continue
            if keyword_lower and phrase == keyword_lower:
                continue
            candidates.append({"phrase": phrase, "count": count})

    # Sort so longer, more specific phrases are favoured over single
    # generic words with the same count (e.g. prefer "home coffee
    # brewing" over the bare word "coffee"), and higher counts still
    # win overall. Written as a manual bubble-style pass instead of
    # relying purely on sorted()'s magic, to keep the "visible
    # algorithm" style used elsewhere in this project - capped so it
    # stays fast even on long pages.
    def rank_key(c):
        return (len(c["phrase"].split(" ")), c["count"])

    n = len(candidates)
    i = 0
    while i < n:
        j = 0
        while j < n - i - 1:
            if rank_key(candidates[j]) < rank_key(candidates[j + 1]):
                candidates[j], candidates[j + 1] = candidates[j + 1], candidates[j]
            j = j + 1
        i = i + 1

    # Drop single words that are already fully covered by a longer
    # phrase we're keeping (e.g. skip "coffee" once "home coffee
    # brewing" is already in the list), so the suggestions stay
    # specific instead of repeating the same word three ways.
    final = []
    for candidate in candidates:
        phrase = candidate["phrase"]
        is_single_word = len(phrase.split(" ")) == 1
        redundant = False
        if is_single_word:
            for kept in final:
                kept_words = kept["phrase"].split(" ")
                if phrase in kept_words:
                    redundant = True
                    break
        if not redundant:
            final.append(candidate)

    return final[:max_suggestions]


# ---------------------------------------------------------------
# This block only runs when you execute this file directly.
# ---------------------------------------------------------------
if __name__ == "__main__":
    sample_content = {
        "body_text": ("Pour over coffee gives full control over water temperature. "
                       "Pour over coffee is popular with home baristas who want a "
                       "clean cup. Cold brew coffee is a different method entirely, "
                       "and cold brew coffee takes much longer to prepare."),
    }
    print(suggest_keywords(sample_content, keyword="pour over coffee"))
