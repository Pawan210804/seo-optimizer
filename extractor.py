# extractor.py
# This file pulls the raw information out of a webpage that we need
# for SEO checks: title, meta description, headings, body text,
# image alt text, and links.
#
# Written to be "DSA proof" - instead of relying on Python shortcuts
# like regex and .split(), the important parts (splitting text into
# words, pulling the domain out of a URL) are written as plain loops,
# so the actual algorithm is visible and you can explain it in a viva.

import socket
import ipaddress
import requests
import urllib3
from urllib.parse import urljoin, urlparse, urldefrag
from bs4 import BeautifulSoup

# We only skip certificate verification as a deliberate, flagged fallback
# (see fetch_html below) when a site's cert chain is broken - so the
# warning urllib3 raises for every unverified request is just noise here.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def split_into_words(text):
    # Manually splits a string into a list of words, without using
    # the built-in .split(). We walk through the string one character
    # at a time, building up a word until we hit a space or newline.
    words = []
    current_word = ""

    for character in text:
        if character == " " or character == "\n" or character == "\t":
            if current_word != "":
                words.append(current_word)
                current_word = ""
        else:
            current_word = current_word + character

    # catch the last word (it won't be added by the loop above,
    # since there's no space after it)
    if current_word != "":
        words.append(current_word)

    return words


def count_words(word_list):
    # Counts items in a list using a loop instead of len(),
    # just to make the counting step explicit.
    total = 0
    for word in word_list:
        total = total + 1
    return total


def get_domain(url):
    # Pulls just the domain name out of a full URL, e.g.
    # "https://example.com/blog/post" -> "example.com"
    # Written as a manual scan instead of a regex match.
    if not url:
        return ""

    length = len(url)
    i = 0

    # Step 1: skip past "http://" or "https://" by looking for "//"
    found_slashes = False
    while i < length - 1:
        if url[i] == "/" and url[i + 1] == "/":
            i = i + 2
            found_slashes = True
            break
        i = i + 1

    if not found_slashes:
        return ""

    # Step 2: read characters into the domain until we hit the next "/"
    # (or the end of the string, if there's no path after the domain)
    domain = ""
    while i < length and url[i] != "/":
        domain = domain + url[i]
        i = i + 1

    return domain.lower()


def naive_string_search(text, pattern):
    # Classic naive substring search algorithm: slides the pattern
    # across the text one position at a time and checks for a match.
    # Used instead of Python's "in" operator so the matching logic
    # is explicit. Returns True if pattern is found inside text.
    n = len(text)
    m = len(pattern)

    if m == 0 or m > n:
        return False

    i = 0
    while i <= n - m:
        j = 0
        while j < m and text[i + j] == pattern[j]:
            j = j + 1
        if j == m:
            return True  # matched all m characters of the pattern
        i = i + 1

    return False


MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 5MB - a page this large is either not real content or abusive input
MAX_REDIRECTS = 5


def _is_blocked_ip(ip_string):
    # True for any address that shouldn't be reachable from a request a
    # random visitor typed into a web form: loopback (127.0.0.1, ::1),
    # RFC1918/private ranges (10.x, 172.16-31.x, 192.168.x), link-local
    # (169.254.x.x - this is where cloud metadata endpoints like AWS's
    # 169.254.169.254 live), multicast, and other reserved ranges.
    try:
        ip = ipaddress.ip_address(ip_string)
    except ValueError:
        return True  # unparseable -> treat as unsafe rather than guess
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _resolve_all_ips(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return []
    ips = set()
    for info in infos:
        ips.add(info[4][0])
    return ips


def assert_safe_to_fetch(url):
    # SSRF guard. Both /analyze and /analyze-site accept a URL typed by
    # whoever is calling the API and then fetch it server-side - without
    # this check, that's a classic Server-Side Request Forgery hole: a
    # caller could point the "url" field at http://127.0.0.1:PORT/, an
    # internal admin panel, or a cloud metadata endpoint
    # (http://169.254.169.254/...) and use this server as a proxy to
    # reach things that are only supposed to be reachable from inside
    # the network. Only plain http/https URLs that resolve to a public
    # IP address are allowed through. Raises requests.exceptions.InvalidURL
    # (a RequestException subclass) on anything unsafe, so callers that
    # already catch requests.RequestException handle this the same way
    # they handle "site is down".
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise requests.exceptions.InvalidURL("Only http:// and https:// URLs are allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise requests.exceptions.InvalidURL("URL has no hostname.")

    ips = _resolve_all_ips(hostname)
    if not ips:
        raise requests.exceptions.InvalidURL("Could not resolve hostname '" + hostname + "'.")

    for ip in ips:
        if _is_blocked_ip(ip):
            raise requests.exceptions.InvalidURL(
                "That URL resolves to a private or internal address and cannot be fetched."
            )


def fetch_html(url):
    # Downloads the raw HTML of a webpage.
    #
    # Some real-world sites (often ones with a misconfigured cert chain -
    # missing the intermediate certificate) fail a strict SSL check even
    # though a browser loads them fine, because browsers cache and chase
    # down missing intermediates and requests does not. To make this tool
    # actually usable across "all sites" without silently trusting broken
    # certs, we try a normal verified request first, and only fall back to
    # an unverified retry if that specific request fails on SSL - never as
    # a blanket default. When that fallback is used, we say so, so the
    # caller always knows the connection wasn't verified.
    #
    # Redirects are followed manually, one hop at a time, and every hop
    # is re-checked with assert_safe_to_fetch(). A site that returns a
    # public IP on the first check and then redirects to an internal one
    # (DNS-rebinding-style SSRF) would otherwise slip straight through a
    # check that only ran once at the start.
    headers = {"User-Agent": "Mozilla/5.0 (SEO-Optimizer-Bot/0.1)"}

    def _get(current_url, verify):
        hops = 0
        while True:
            assert_safe_to_fetch(current_url)
            response = requests.get(
                current_url, headers=headers, timeout=10, verify=verify,
                allow_redirects=False, stream=True,
            )
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise requests.exceptions.TooManyRedirects("Redirect with no Location header.")
                current_url = urljoin(current_url, location)
                hops += 1
                if hops > MAX_REDIRECTS:
                    raise requests.exceptions.TooManyRedirects("Too many redirects.")
                continue

            response.raise_for_status()

            # Read the body ourselves with a hard cap instead of trusting
            # Content-Length (which a malicious/misbehaving server can
            # simply lie about) - stops a huge or infinite response body
            # from being used to exhaust memory.
            content_chunks = []
            total_bytes = 0
            for chunk in response.iter_content(chunk_size=65536):
                total_bytes += len(chunk)
                if total_bytes > MAX_DOWNLOAD_BYTES:
                    response.close()
                    raise requests.exceptions.ContentDecodingError(
                        "Response exceeded the " + str(MAX_DOWNLOAD_BYTES // (1024 * 1024)) + "MB size limit."
                    )
                content_chunks.append(chunk)

            response.encoding = response.encoding or "utf-8"
            raw_bytes = b"".join(content_chunks)
            text = raw_bytes.decode(response.encoding, errors="replace")
            return text

    try:
        return _get(url, verify=True), False
    except requests.exceptions.SSLError:
        return _get(url, verify=False), True


def extract_content(html, url=None):
    soup = BeautifulSoup(html, "html.parser")

    # ---- title ----
    title = ""
    title_tag = soup.find("title")
    if title_tag is not None:
        title = title_tag.get_text().strip()

    # ---- meta description ----
    meta_description = ""
    meta_tag = soup.find("meta", attrs={"name": "description"})
    if meta_tag is not None and meta_tag.get("content"):
        meta_description = meta_tag["content"].strip()

    # ---- indexability / crawler directives ----
    # If a page says "noindex", nothing else about it matters for SEO -
    # search engines simply won't list it - so this is checked and
    # surfaced separately from every other signal.
    robots_tag = soup.find("meta", attrs={"name": "robots"})
    robots_content = ""
    if robots_tag is not None and robots_tag.get("content"):
        robots_content = robots_tag["content"].strip().lower()
    is_noindex = "noindex" in robots_content

    # ---- canonical tag ----
    canonical_url = ""
    canonical_tag = soup.find("link", rel="canonical")
    if canonical_tag is not None and canonical_tag.get("href"):
        canonical_url = canonical_tag["href"].strip()

    # ---- mobile viewport ----
    viewport_tag = soup.find("meta", attrs={"name": "viewport"})
    has_viewport = viewport_tag is not None and (viewport_tag.get("content") or "").strip() != ""

    # ---- Open Graph / social sharing tags ----
    og_title = soup.find("meta", property="og:title")
    og_description = soup.find("meta", property="og:description")
    og_image = soup.find("meta", property="og:image")
    has_open_graph = any([og_title is not None, og_description is not None, og_image is not None])

    # ---- structured data (schema.org JSON-LD) ----
    structured_data_tags = soup.find_all("script", attrs={"type": "application/ld+json"})
    has_structured_data = len(structured_data_tags) > 0

    # ---- declared page language ----
    html_tag = soup.find("html")
    has_lang = html_tag is not None and (html_tag.get("lang") or "").strip() != ""

    # ---- headings (h1 to h6) ----
    headings = {}
    heading_levels = ["h1", "h2", "h3", "h4", "h5", "h6"]
    for level in heading_levels:
        tags_found = soup.find_all(level)
        texts = []
        for tag in tags_found:
            texts.append(tag.get_text().strip())
        headings[level] = texts

    # ---- body text ----
    # Remove tags that are not real content (menus, scripts, footers)
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()

    raw_text = soup.get_text(separator=" ")
    words = split_into_words(raw_text)
    body_text = " ".join(words)
    word_count = count_words(words)

    # ---- images and alt text ----
    images = soup.find_all("img")
    images_total = 0
    images_missing_alt = 0
    for img in images:
        images_total = images_total + 1
        alt_text = img.get("alt", "")
        if alt_text.strip() == "":
            images_missing_alt = images_missing_alt + 1

    # ---- links ----
    page_domain = get_domain(url)
    internal_links = 0
    external_links = 0

    all_links = soup.find_all("a", href=True)
    for link in all_links:
        href = link["href"]

        # skip links that are not really page-to-page navigation
        if len(href) > 0 and href[0] == "#":
            continue
        if href.startswith("mailto:") or href.startswith("javascript:"):
            continue

        if href.startswith("//"):
            # protocol-relative URL, e.g. "//cdn.example.com/x" - this points
            # at whatever domain follows, NOT the current page, so it must be
            # checked against page_domain just like a full http(s) link.
            link_domain = get_domain("https:" + href)
            if page_domain != "" and link_domain == page_domain:
                internal_links = internal_links + 1
            else:
                external_links = external_links + 1
        elif href.startswith("/"):
            internal_links = internal_links + 1
        elif href.startswith("http"):
            link_domain = get_domain(href)
            if page_domain != "" and link_domain == page_domain:
                internal_links = internal_links + 1
            else:
                external_links = external_links + 1
        else:
            # plain relative link with no leading slash, e.g. "about.html"
            # or "contact" - this still points at a page on the same site,
            # so it counts as internal rather than being dropped entirely.
            internal_links = internal_links + 1

    content = {
        "url": url,
        "title": title,
        "meta_description": meta_description,
        "headings": headings,
        "body_text": body_text,
        "word_list": words,
        "word_count": word_count,
        "images_total": images_total,
        "images_missing_alt": images_missing_alt,
        "internal_links": internal_links,
        "external_links": external_links,
        "ssl_unverified": False,
        "is_noindex": is_noindex,
        "robots_content": robots_content,
        "canonical_url": canonical_url,
        "has_viewport": has_viewport,
        "has_open_graph": has_open_graph,
        "has_structured_data": has_structured_data,
        "has_lang": has_lang,
    }
    return content


def extract_from_url(url):
    html, ssl_unverified = fetch_html(url)
    content = extract_content(html, url)
    content["ssl_unverified"] = ssl_unverified
    return content


def extract_internal_links(html, base_url):
    # Returns a de-duplicated list of absolute, same-domain page URLs
    # found in the HTML - used by site_crawler.py to discover which
    # pages to visit next when analyzing a whole site.
    #
    # This is a separate, narrower pass from the internal/external
    # *counting* done inside extract_content(): here we need real,
    # resolvable URLs to actually fetch, so relative links are resolved
    # against base_url with urljoin() rather than just classified.
    if not base_url:
        return []

    soup = BeautifulSoup(html, "html.parser")
    page_domain = get_domain(base_url)
    if page_domain == "":
        return []

    links = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()

        if href == "" or href[0] == "#":
            continue
        if href.startswith("mailto:") or href.startswith("javascript:") or href.startswith("tel:"):
            continue

        absolute = urljoin(base_url, href)
        absolute, _fragment = urldefrag(absolute)  # drop "#section" anchors - same page, not a new one

        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue

        # Skip obvious non-page assets a crawler shouldn't try to parse
        # as HTML (images, stylesheets, scripts, documents, feeds).
        path_lower = parsed.path.lower()
        skip_extensions = (
            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
            ".css", ".js", ".pdf", ".zip", ".xml", ".json", ".mp4",
            ".mp3", ".woff", ".woff2", ".ttf",
        )
        if path_lower.endswith(skip_extensions):
            continue

        link_domain = get_domain(absolute)
        if link_domain != page_domain:
            continue  # external link - out of scope for a same-site crawl

        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)

    return links


# ---------------------------------------------------------------
# This block only runs when you execute this file directly
# (like pressing Run/Debug on extractor.py in VS Code).
# ---------------------------------------------------------------
if __name__ == "__main__":
    sample_html = """
    <html>
    <head>
        <title>Best Home Coffee Brewing Methods</title>
        <meta name="description" content="Learn the best home coffee brewing methods for beginners.">
    </head>
    <body>
        <h1>Best Home Coffee Brewing Methods</h1>
        <h2>Pour-Over</h2>
        <p>Pour-over brewing gives full control over water temperature and time.</p>
        <img src="a.jpg" alt="pour over setup">
        <img src="b.jpg">
        <a href="/grinders">grinders</a>
        <a href="https://example.com/beans">buy beans</a>
    </body>
    </html>
    """

    result = extract_content(sample_html, url="https://example.com/coffee")

    print("Title:", result["title"])
    print("Meta description:", result["meta_description"])
    print("Word count:", result["word_count"])
    print("H1 headings:", result["headings"]["h1"])
    print("H2 headings:", result["headings"]["h2"])
    print("Images total:", result["images_total"])
    print("Images missing alt text:", result["images_missing_alt"])
    print("Internal links:", result["internal_links"])
    print("External links:", result["external_links"])

    print()
    print("Testing get_domain():")
    print(" ", get_domain("https://example.com/blog/post"))
    print(" ", get_domain("http://sub.example.co.in/page"))

    print("Testing naive_string_search():")
    print(" ", naive_string_search("home coffee brewing guide", "coffee"))
    print(" ", naive_string_search("home coffee brewing guide", "tea"))
