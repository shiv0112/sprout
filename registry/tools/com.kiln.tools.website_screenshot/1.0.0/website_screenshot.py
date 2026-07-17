"""
website_screenshot
------------------
Render a live screenshot of any public URL via Microlink's free tier.
Returns a base64 PNG + local file path + page metadata (title, author, image).
No API key required (anonymous tier is rate-limited but sufficient for demos).
"""

import base64
import tempfile

import requests

REQUIRED_ENV_VARS = []


def website_screenshot(
    url: str,
    full_page: bool = False,
    device: str = "desktop",
    dark: bool = False,
) -> dict:
    """Capture a PNG screenshot of a public URL plus basic page metadata.

    Args:
        url:       Public URL to render.
        full_page: Capture the whole scrollable page instead of the viewport.
        device:    'desktop', 'iphone', 'ipad', or 'macbook-pro'.
        dark:      Use dark colour scheme when rendering.
    """
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        resp = requests.get(
            "https://api.microlink.io/",
            params={
                "url": url,
                "screenshot": "true",
                "meta": "true",
                "embed": "screenshot.url",
                "fullPage": str(full_page).lower(),
                "device": device,
                "colorScheme": "dark" if dark else "light",
            },
            timeout=45,
            allow_redirects=True,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "image/" in content_type:
            png = resp.content
        else:
            data = resp.json()
            if data.get("status") != "success":
                return {"success": False, "error": data.get("message", "Microlink failed.")}
            shot_url = (data.get("data", {}) or {}).get("screenshot", {}).get("url")
            if not shot_url:
                return {"success": False, "error": "No screenshot URL in response."}
            shot_resp = requests.get(shot_url, timeout=30)
            shot_resp.raise_for_status()
            shot_ct = shot_resp.headers.get("Content-Type", "")
            if not shot_ct.startswith("image/"):
                return {"success": False, "error": f"Screenshot URL returned non-image content-type {shot_ct}."}
            png = shot_resp.content

        b64 = base64.b64encode(png).decode("utf-8")
        tmp = tempfile.NamedTemporaryFile(suffix=".png", prefix="screenshot_", delete=False)
        tmp.write(png)
        tmp.close()

        meta_resp = requests.get(
            "https://api.microlink.io/", params={"url": url, "meta": "true"}, timeout=15
        )
        meta = {}
        if meta_resp.ok:
            body = meta_resp.json().get("data", {}) or {}
            meta = {
                "title": body.get("title", ""),
                "description": body.get("description", ""),
                "author": body.get("author", ""),
                "publisher": body.get("publisher", ""),
                "og_image": (body.get("image") or {}).get("url", "") if isinstance(body.get("image"), dict) else "",
            }

        return {
            "success": True,
            "url": url,
            "device": device,
            "full_page": full_page,
            "dark": dark,
            "data_url": f"data:image/png;base64,{b64}",
            "file_path": tmp.name,
            "bytes": len(png),
            "meta": meta,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
