"""
youtube_transcript
------------------
Pull the full spoken transcript of any YouTube video that has captions
(manual or auto). Also returns the video title/author via oEmbed. No key.
"""

import re

import requests

REQUIRED_ENV_VARS = []


def _extract_id(url_or_id: str) -> str | None:
    s = url_or_id.strip()
    if re.match(r"^[A-Za-z0-9_-]{11}$", s):
        return s
    m = re.search(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else None


def youtube_transcript(video: str, language: str = "en", preserve_timing: bool = False) -> dict:
    """Fetch a YouTube transcript.

    Args:
        video:           Video URL, youtu.be link, or 11-character ID.
        language:        Preferred caption language code.
        preserve_timing: Return per-segment start/duration instead of plain text.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
    except ImportError as exc:
        return {"success": False, "error": f"youtube-transcript-api not installed: {exc}"}

    vid = _extract_id(video)
    if not vid:
        return {"success": False, "error": f"Could not parse video id from '{video}'."}

    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(vid)
        try:
            chosen = transcripts.find_transcript([language])
        except NoTranscriptFound:
            try:
                chosen = transcripts.find_generated_transcript([language])
            except NoTranscriptFound:
                chosen = next(iter(transcripts))
        segments = chosen.fetch()
    except TranscriptsDisabled:
        return {"success": False, "error": "Captions are disabled for this video."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    title = ""
    author = ""
    try:
        oembed = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=8,
        )
        if oembed.ok:
            j = oembed.json()
            title = j.get("title", "")
            author = j.get("author_name", "")
    except Exception:
        pass

    if preserve_timing:
        segs = [{"start": s["start"], "duration": s["duration"], "text": s["text"]} for s in segments]
        text = "\n".join(s["text"] for s in segments)
    else:
        segs = []
        text = " ".join(s["text"].replace("\n", " ") for s in segments)

    return {
        "success": True,
        "video_id": vid,
        "title": title,
        "author": author,
        "language": getattr(chosen, "language_code", language),
        "is_generated": getattr(chosen, "is_generated", False),
        "text": text.strip(),
        "char_count": len(text),
        "segments": segs,
        "url": f"https://www.youtube.com/watch?v={vid}",
    }
