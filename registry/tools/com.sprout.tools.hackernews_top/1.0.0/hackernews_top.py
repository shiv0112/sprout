"""
hackernews_top
--------------
Return the top, new, best, or 'ask' stories from Hacker News, fully hydrated
with titles, URLs, scores, authors, and comment counts. Uses the public
Firebase API — no key required.
"""

import concurrent.futures

import requests

REQUIRED_ENV_VARS = []

_FEEDS = {
    "top": "https://hacker-news.firebaseio.com/v0/topstories.json",
    "new": "https://hacker-news.firebaseio.com/v0/newstories.json",
    "best": "https://hacker-news.firebaseio.com/v0/beststories.json",
    "ask": "https://hacker-news.firebaseio.com/v0/askstories.json",
    "show": "https://hacker-news.firebaseio.com/v0/showstories.json",
    "job": "https://hacker-news.firebaseio.com/v0/jobstories.json",
}


def _item(item_id: int) -> dict | None:
    try:
        r = requests.get(
            f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=8
        )
        if r.ok:
            return r.json()
    except Exception:
        return None
    return None


def hackernews_top(feed: str = "top", limit: int = 10) -> dict:
    """Return hydrated Hacker News stories.

    Args:
        feed:  'top', 'new', 'best', 'ask', 'show', or 'job'.
        limit: 1-50 stories.
    """
    try:
        url = _FEEDS.get(feed.lower())
        if not url:
            return {"success": False, "error": f"Unknown feed '{feed}'."}
        limit = max(1, min(int(limit), 50))

        ids = requests.get(url, timeout=10).json() or []
        ids = ids[:limit]

        stories: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            for data in pool.map(_item, ids):
                if not data:
                    continue
                stories.append({
                    "id": data.get("id"),
                    "title": data.get("title", ""),
                    "url": data.get("url") or f"https://news.ycombinator.com/item?id={data.get('id')}",
                    "score": data.get("score", 0),
                    "author": data.get("by", ""),
                    "comments": data.get("descendants", 0),
                    "time": data.get("time"),
                    "type": data.get("type"),
                    "hn_url": f"https://news.ycombinator.com/item?id={data.get('id')}",
                })

        return {"success": True, "feed": feed, "count": len(stories), "stories": stories}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
