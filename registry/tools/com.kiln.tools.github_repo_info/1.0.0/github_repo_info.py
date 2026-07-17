"""
github_repo_info
----------------
Rich metadata for any public GitHub repo: stars, forks, language breakdown,
license, top contributors, recent commits, README excerpt, and the latest
release. Uses anonymous REST (60 req/h) — no token required.
"""

import base64
import re

import requests

REQUIRED_ENV_VARS = []

_HEADERS = {
    "User-Agent": "Kiln/1.0 (https://kiln.dev) github_repo_info",
    "Accept": "application/vnd.github+json",
}


def _parse(repo: str) -> tuple[str, str] | None:
    repo = repo.strip().rstrip("/")
    m = re.match(r"^(?:https?://github\.com/)?([^/\s]+)/([^/\s#?]+)(?:\.git)?$", repo)
    if not m:
        return None
    return m.group(1), m.group(2)


def github_repo_info(repo: str, readme_chars: int = 1500, contributors: int = 5) -> dict:
    """Return a rich dossier on a GitHub repo.

    Args:
        repo:         'owner/name' or a full github.com URL.
        readme_chars: Max README characters to include.
        contributors: Top N contributor profiles.
    """
    try:
        parsed = _parse(repo)
        if not parsed:
            return {"success": False, "error": f"Invalid repo '{repo}'. Expected 'owner/name'."}
        owner, name = parsed
        base = f"https://api.github.com/repos/{owner}/{name}"

        meta_resp = requests.get(base, headers=_HEADERS, timeout=15)
        if meta_resp.status_code == 404:
            return {"success": False, "error": f"Repo '{owner}/{name}' not found."}
        if meta_resp.status_code == 403:
            return {"success": False, "error": "GitHub rate-limited (60 req/h anonymous)."}
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        languages = {}
        try:
            languages = requests.get(f"{base}/languages", headers=_HEADERS, timeout=10).json() or {}
        except Exception:
            pass

        top_contribs = []
        try:
            contribs = requests.get(
                f"{base}/contributors",
                params={"per_page": max(1, min(contributors, 20))},
                headers=_HEADERS,
                timeout=10,
            ).json() or []
            top_contribs = [
                {"login": c["login"], "contributions": c["contributions"], "avatar_url": c["avatar_url"]}
                for c in contribs if isinstance(c, dict) and "login" in c
            ]
        except Exception:
            pass

        recent_commits = []
        try:
            commits = requests.get(f"{base}/commits", params={"per_page": 5}, headers=_HEADERS, timeout=10).json() or []
            for c in commits:
                if isinstance(c, dict):
                    recent_commits.append({
                        "sha": c.get("sha", "")[:7],
                        "message": (c.get("commit", {}).get("message", "") or "").split("\n", 1)[0],
                        "author": (c.get("commit", {}).get("author", {}) or {}).get("name", ""),
                        "date": (c.get("commit", {}).get("author", {}) or {}).get("date", ""),
                    })
        except Exception:
            pass

        readme = ""
        try:
            r = requests.get(f"{base}/readme", headers=_HEADERS, timeout=10).json()
            if isinstance(r, dict) and r.get("content"):
                raw = base64.b64decode(r["content"]).decode("utf-8", errors="ignore")
                readme = raw[:readme_chars]
        except Exception:
            pass

        latest_release = None
        try:
            rel = requests.get(f"{base}/releases/latest", headers=_HEADERS, timeout=10)
            if rel.ok:
                j = rel.json()
                latest_release = {
                    "tag": j.get("tag_name"),
                    "name": j.get("name"),
                    "published_at": j.get("published_at"),
                    "url": j.get("html_url"),
                }
        except Exception:
            pass

        return {
            "success": True,
            "full_name": meta.get("full_name"),
            "description": meta.get("description"),
            "url": meta.get("html_url"),
            "homepage": meta.get("homepage"),
            "stars": meta.get("stargazers_count", 0),
            "forks": meta.get("forks_count", 0),
            "watchers": meta.get("subscribers_count", meta.get("watchers_count", 0)),
            "open_issues": meta.get("open_issues_count", 0),
            "default_branch": meta.get("default_branch"),
            "primary_language": meta.get("language"),
            "languages": languages,
            "license": (meta.get("license") or {}).get("spdx_id"),
            "topics": meta.get("topics", []),
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "pushed_at": meta.get("pushed_at"),
            "archived": meta.get("archived", False),
            "contributors": top_contribs,
            "recent_commits": recent_commits,
            "latest_release": latest_release,
            "readme_excerpt": readme,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
