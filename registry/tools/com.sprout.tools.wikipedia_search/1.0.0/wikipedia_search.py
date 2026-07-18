"""
wikipedia_search
----------------
Look up a topic on Wikipedia. Returns the page summary, URL, and
thumbnail (when available) using the public REST API — no key required.
"""

import urllib.parse

import requests

REQUIRED_ENV_VARS = []

_HEADERS = {"User-Agent": "Sprout/1.0 (https://sprout.dev) wikipedia_search"}


def wikipedia_search(query: str, language: str = "en", sentences: int = 5) -> dict:
    """Search Wikipedia and return the best-matching article summary.

    Args:
        query:     Topic, person, place, or concept to look up.
        language:  Wikipedia language code (default 'en').
        sentences: Soft cap on the summary length (characters, approx sentences * 200).
    """
    try:
        base = f"https://{language}.wikipedia.org/w/api.php"
        search = requests.get(
            base,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 1,
                "format": "json",
            },
            headers=_HEADERS,
            timeout=10,
        ).json()

        hits = search.get("query", {}).get("search", [])
        if not hits:
            return {"success": False, "error": f"No Wikipedia article found for '{query}'."}

        title = hits[0]["title"]
        slug = urllib.parse.quote(title.replace(" ", "_"))
        summary = requests.get(
            f"https://{language}.wikipedia.org/api/rest_v1/page/summary/{slug}",
            headers=_HEADERS,
            timeout=10,
        ).json()

        extract = summary.get("extract", "") or ""
        if sentences and len(extract) > sentences * 220:
            extract = extract[: sentences * 220].rsplit(". ", 1)[0] + "."

        return {
            "success": True,
            "title": summary.get("title", title),
            "extract": extract,
            "url": summary.get("content_urls", {}).get("desktop", {}).get("page", ""),
            "thumbnail": (summary.get("thumbnail") or {}).get("source", ""),
            "description": summary.get("description", ""),
            "language": language,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
