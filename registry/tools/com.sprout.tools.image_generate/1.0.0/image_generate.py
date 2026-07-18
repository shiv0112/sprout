"""
image_generate
--------------
Generate an AI image from a text prompt via Pollinations.ai — a free,
keyless text-to-image endpoint. Returns a base64 data URL plus a PNG
saved to a temp file, so the UI can render it immediately.
"""

import base64
import hashlib
import io
import os
import tempfile
import urllib.parse

import requests

REQUIRED_ENV_VARS = []

_MODELS = {"flux", "flux-realism", "flux-anime", "flux-3d", "turbo", "any-dark"}


def image_generate(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    model: str = "flux",
    seed: int | None = None,
) -> dict:
    """Generate an image from a text prompt.

    Args:
        prompt: What to draw. Be descriptive.
        width:  Output width (256-2048).
        height: Output height (256-2048).
        model:  Pollinations model — 'flux', 'flux-realism', 'flux-anime',
                'flux-3d', 'turbo', or 'any-dark'.
        seed:   Optional integer seed for reproducibility.
    """
    try:
        if not prompt or not prompt.strip():
            return {"success": False, "error": "Prompt is required."}
        width = max(256, min(int(width), 2048))
        height = max(256, min(int(height), 2048))
        model = model if model in _MODELS else "flux"
        if seed is None:
            seed = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)

        encoded = urllib.parse.quote(prompt.strip())
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width={width}&height={height}&model={model}&seed={seed}&nologo=true&enhance=true"
        )

        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/png")
        if not content_type.startswith("image/"):
            return {"success": False, "error": f"Unexpected content-type {content_type}."}

        b64 = base64.b64encode(resp.content).decode("utf-8")
        data_url = f"data:{content_type};base64,{b64}"

        tmp = tempfile.NamedTemporaryFile(suffix=".png", prefix="gen_", delete=False)
        tmp.write(resp.content)
        tmp.close()

        return {
            "success": True,
            "prompt": prompt,
            "model": model,
            "width": width,
            "height": height,
            "seed": seed,
            "url": url,
            "data_url": data_url,
            "file_path": tmp.name,
            "bytes": len(resp.content),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
