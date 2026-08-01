# semantic.py
# This is the semantic/AI layer. It calls Azure AI Language's Key
# Phrase Extraction API to check whether the content actually stays
# on-topic for the focus keyword, instead of just counting words.
#
# SECURITY NOTES (read this before using):
# - The Azure endpoint and key are NEVER written in this file. They
#   are read from environment variables at runtime using os.environ.
# - Store them in a local ".env" file (see .env.example) which should
#   NEVER be committed to Git - add ".env" to your .gitignore.
# - If the credentials are missing, or the Azure call fails for any
#   reason (bad key, network issue, quota limit), this check does
#   NOT crash the app - it just skips itself and says so in the
#   message, and every other check still runs normally.

import os
import sys
import requests
from dotenv import load_dotenv

# Loads variables from a local .env file (if one exists) into the
# environment, so os.environ.get() below can find them.
load_dotenv()


def _log(message):
    # Small helper so setup problems show up in the server console
    # (where python app.py is running) instead of vanishing silently.
    # This is diagnostic output only - it never affects the API
    # response, and the app keeps working with whatever signals are
    # actually available.
    print("[semantic] " + message, file=sys.stderr)


# ---------------------------------------------------------------
# Local models (spaCy + sentence-transformers). These run entirely
# on your own machine - no internet needed once the model files are
# downloaded the first time. Loaded once here and reused, instead of
# reloading on every single check (that would be slow).
#
# Each one tracks *why* it isn't available (not installed, model not
# downloaded, no internet to download it, etc.) in a small status
# dict, instead of just silently going to None like before - that
# status is surfaced in the check's message when nothing else is
# available either, so a skipped check is explainable instead of a
# dead end.
# ---------------------------------------------------------------

SIGNAL_STATUS = {
    "azure": "not attempted yet",
    "spacy": "not attempted yet",
    "similarity": "not attempted yet",
}

try:
    import spacy
    try:
        _nlp = spacy.load("en_core_web_sm")
        SIGNAL_STATUS["spacy"] = "ok"
    except OSError as error:
        # spaCy itself imported fine, but the English model hasn't
        # been downloaded - this is the single most common setup
        # miss, so it gets its own message instead of a generic one.
        _nlp = None
        SIGNAL_STATUS["spacy"] = "model not downloaded - run: python -m spacy download en_core_web_sm"
        _log("spaCy model 'en_core_web_sm' is not downloaded. Run: "
             "python -m spacy download en_core_web_sm  (" + str(error) + ")")
except Exception as error:
    _nlp = None
    SIGNAL_STATUS["spacy"] = "not installed - run: pip install spacy"
    _log("spaCy is not installed or failed to import: " + str(error))

_similarity_model = None
_similarity_model_load_failed = False


def _get_similarity_model():
    # Loads the sentence-transformers model once and caches it in
    # _similarity_model, so we're not reloading it (or re-trying a
    # failed download) on every single request.
    global _similarity_model, _similarity_model_load_failed

    if _similarity_model is not None:
        return _similarity_model
    if _similarity_model_load_failed:
        return None

    try:
        from sentence_transformers import SentenceTransformer
    except Exception as error:
        _similarity_model_load_failed = True
        SIGNAL_STATUS["similarity"] = "not installed - run: pip install sentence-transformers"
        _log("sentence-transformers is not installed: " + str(error))
        return None

    try:
        _log("loading sentence-transformers model 'all-MiniLM-L6-v2' "
             "(first run downloads ~90MB from huggingface.co, then it's cached)...")
        _similarity_model = SentenceTransformer("all-MiniLM-L6-v2")
        SIGNAL_STATUS["similarity"] = "ok"
        _log("sentence-transformers model loaded successfully.")
        return _similarity_model
    except Exception as error:
        _similarity_model_load_failed = True
        error_text = str(error)
        if "huggingface.co" in error_text or "Connection" in error_text or "connect" in error_text.lower():
            SIGNAL_STATUS["similarity"] = ("couldn't download the model - no working connection to "
                                            "huggingface.co on first run. Check your internet connection "
                                            "or a firewall/proxy blocking huggingface.co, then restart the server.")
        else:
            SIGNAL_STATUS["similarity"] = "failed to load: " + error_text
        _log("sentence-transformers model failed to load: " + error_text)
        return None


def warm_up():
    # Loads the sentence-transformers model right away instead of
    # waiting for the first request that includes a keyword. Without
    # this, whoever sends the FIRST keyworded request after a fresh
    # deploy or a cold start pays the full ~90MB download + load cost
    # inline, on their own request - which can easily blow past a
    # server timeout and show up as a failed/empty response on their
    # end. Meant to be called once, in a background thread, right
    # after the Flask app starts, so the app can still bind its port
    # immediately while this loads in parallel.
    _get_similarity_model()


def get_key_phrases_local(text):
    # Same idea as get_key_phrases() above, but using spaCy running
    # locally instead of calling Azure. Pulls out noun phrases
    # ("water temperature") and named entities from the text.
    # Returns None if spaCy isn't available, so the caller can treat
    # it exactly the same way as an Azure failure.
    if _nlp is None:
        return None

    if text.strip() == "":
        return None

    doc = _nlp(text)
    phrases = []

    for chunk in doc.noun_chunks:
        phrase = chunk.text.strip()
        if phrase != "" and phrase not in phrases:
            phrases.append(phrase)

    for ent in doc.ents:
        phrase = ent.text.strip()
        if phrase != "" and phrase not in phrases:
            phrases.append(phrase)

    return phrases


def get_similarity_score(text, keyword):
    # Uses sentence-transformers to check how semantically close the
    # content is to the focus keyword - this can catch topic drift
    # even when the exact keyword words don't appear much, because it
    # compares meaning, not just word overlap.
    # Returns None if the model isn't available (not installed, or
    # couldn't download on first use) so the caller can skip safely.
    model = _get_similarity_model()
    if model is None:
        return None

    try:
        from sentence_transformers import util
        text_embedding = model.encode(text)
        keyword_embedding = model.encode(keyword)
        similarity = util.cos_sim(text_embedding, keyword_embedding)
        return float(similarity[0][0])
    except Exception as error:
        SIGNAL_STATUS["similarity"] = "failed during scoring: " + str(error)
        _log("sentence-transformers failed while scoring: " + str(error))
        return None


def limit_text_length(word_list, max_characters):
    # Azure's Language API has a per-document size limit (about 5120
    # characters). This builds up a string from the word list but
    # stops adding words once we're about to go over the limit -
    # a simple loop instead of blindly slicing the raw string, so we
    # never accidentally cut a request in half or send too much data.
    result = ""
    for word in word_list:
        if len(result) + len(word) + 1 > max_characters:
            break
        if result == "":
            result = word
        else:
            result = result + " " + word
    return result


def get_key_phrases(text):
    # Calls Azure AI Language's key phrase extraction endpoint.
    # Returns a list of key phrases, or None if the check could not
    # be run for any reason (missing credentials, network error,
    # bad response). None is treated as "skip this check", never
    # as a crash.
    endpoint = os.environ.get("AZURE_LANGUAGE_ENDPOINT")
    key = os.environ.get("AZURE_LANGUAGE_KEY")

    if not endpoint or not key:
        SIGNAL_STATUS["azure"] = "not configured - set AZURE_LANGUAGE_ENDPOINT and AZURE_LANGUAGE_KEY in .env"
        return None

    if text.strip() == "":
        return None

    url = endpoint.rstrip("/") + "/text/analytics/v3.1/keyPhrases"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json",
    }
    body = {
        "documents": [
            {"id": "1", "language": "en", "text": text}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=10)
        response.raise_for_status()
    except requests.RequestException as error:
        # Covers timeouts, connection errors, bad status codes (401
        # for a wrong key, 429 for rate limits, etc.) - all treated
        # the same way: skip the check, don't crash the app - but the
        # reason is still logged and recorded for the status message.
        SIGNAL_STATUS["azure"] = "call failed: " + str(error)
        _log("Azure key-phrase call failed: " + str(error))
        return None

    try:
        data = response.json()
        documents = data.get("documents", [])
        if len(documents) == 0:
            SIGNAL_STATUS["azure"] = "call succeeded but returned no documents"
            return None
        SIGNAL_STATUS["azure"] = "ok"
        return documents[0].get("keyPhrases", [])
    except (ValueError, KeyError, IndexError) as error:
        # Covers unexpected/malformed responses from the API.
        SIGNAL_STATUS["azure"] = "unexpected response from Azure: " + str(error)
        _log("Azure key-phrase response was malformed: " + str(error))
        return None


def score_phrase_overlap(phrases, keyword):
    # Shared scoring logic for "how many of these extracted phrases
    # relate to the focus keyword" - used for both Azure's phrases
    # and spaCy's phrases, so the same rule applies to either source.
    total_phrases = 0
    for phrase in phrases:
        total_phrases = total_phrases + 1

    if total_phrases == 0:
        return None

    keyword_words = keyword.lower().split()

    related_count = 0
    for phrase in phrases:
        phrase_lower = phrase.lower()
        phrase_is_related = False
        for kw in keyword_words:
            if kw in phrase_lower:
                phrase_is_related = True
        if phrase_is_related:
            related_count = related_count + 1

    coverage_ratio = related_count / total_phrases

    if coverage_ratio >= 0.3:
        return 100
    elif coverage_ratio >= 0.15:
        return 70
    else:
        return 40


def score_similarity(similarity):
    # Turns a raw cosine similarity number (roughly 0 to 1 for
    # related text, using this particular model) into the same
    # 0-100 scale the rest of the app uses.
    if similarity >= 0.45:
        return 100
    elif similarity >= 0.30:
        return 70
    else:
        return 40


def check_semantic_coverage(content, keyword=None):
    if not keyword:
        return {"name": "Semantic coverage", "score": 100, "passed": True,
                "message": "No focus keyword supplied - check skipped.",
                "fix": "Provide a focus keyword to get topic-coverage guidance."}

    safe_text = limit_text_length(content["word_list"], 5000)

    # Gather every signal we can. Each one is independent - if one
    # fails or isn't available, the others still work, and we only
    # fully skip the check if NONE of them are available.
    scores_used = []
    sources_used = []

    azure_phrases = get_key_phrases(safe_text)
    if azure_phrases is not None:
        azure_score = score_phrase_overlap(azure_phrases, keyword)
        if azure_score is not None:
            scores_used.append(azure_score)
            sources_used.append("Azure key phrases")

    local_phrases = get_key_phrases_local(safe_text)
    if local_phrases is not None:
        local_score = score_phrase_overlap(local_phrases, keyword)
        if local_score is not None:
            scores_used.append(local_score)
            sources_used.append("spaCy noun phrases/entities")

    similarity = get_similarity_score(safe_text, keyword)
    if similarity is not None:
        scores_used.append(score_similarity(similarity))
        sources_used.append("sentence-transformers similarity")

    if len(scores_used) == 0:
        reasons = ("Azure: " + SIGNAL_STATUS["azure"] + ". "
                   "spaCy: " + SIGNAL_STATUS["spacy"] + ". "
                   "sentence-transformers: " + SIGNAL_STATUS["similarity"] + ".")
        return {"name": "Semantic coverage", "score": 100, "passed": True,
                "message": "Semantic check skipped - none of the three signals are available right now. " + reasons,
                "fix": "See SEMANTIC_SETUP.md - or check the server console (where python app.py is "
                       "running) for a [semantic] log line explaining exactly which signal failed and why."}

    total = 0
    for s in scores_used:
        total = total + s
    final_score = round(total / len(scores_used), 1)

    sources_text = ""
    i = 0
    while i < len(sources_used):
        if i > 0:
            sources_text = sources_text + ", "
        sources_text = sources_text + sources_used[i]
        i = i + 1

    message = ("Semantic coverage for '" + keyword + "' scored using: " + sources_text
               + ". Combined score: " + str(final_score) + "/100.")

    passed = final_score >= 70
    if passed:
        fix = "No changes needed - this is already in good shape."
    else:
        fix = ("Add more supporting terms, subtopics, and related phrases around '" + keyword
               + "' so the page's content matches the topic more closely - see the keyword "
                 "suggestions above for phrases already showing up on the page.")
    return {"name": "Semantic coverage", "score": final_score, "passed": passed, "message": message, "fix": fix}


# ---------------------------------------------------------------
# This block only runs when you execute this file directly.
# It lets you test the credential-loading and fallback behaviour
# without needing a real Azure resource yet.
# ---------------------------------------------------------------
if __name__ == "__main__":
    fake_content = {
        "word_list": "home coffee brewing gives full control over water temperature and time".split()
    }

    result = check_semantic_coverage(fake_content, keyword="home coffee brewing")
    print(result)
