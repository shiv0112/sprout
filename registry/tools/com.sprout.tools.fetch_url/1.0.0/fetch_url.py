"""
com.aria.tools.fetch_url
Fetch any public URL and return plain text. No API key required.
"""
import requests
from bs4 import BeautifulSoup


def fetch_url(url: str, max_chars: int = 3000) -> dict:
    """Fetch a URL and return its plain-text content with HTML stripped."""
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ARIA/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove boilerplate tags
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "meta", "link"]):
            tag.decompose()

        title   = soup.title.string.strip() if soup.title and soup.title.string else ""
        text    = soup.get_text(separator="\n", strip=True)
        lines   = [ln for ln in text.splitlines() if ln.strip()]
        content = "\n".join(lines)

        truncated = len(content) > max_chars
        return {
            "url":       url,
            "title":     title,
            "content":   content[:max_chars],
            "truncated": truncated,
            "success":   True,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "url": url}
