"""
lyrics_fetch
------------
Fetch the lyrics of a song by artist and title via lyrics.ovh, plus optional
iTunes artwork and a 30-second preview URL — all keyless.
"""

import urllib.parse

import requests

REQUIRED_ENV_VARS = []


def lyrics_fetch(artist: str, title: str, include_artwork: bool = True) -> dict:
    """Return the lyrics for a song plus album artwork and a preview clip.

    Args:
        artist:          Performing artist (e.g. 'Queen').
        title:           Song title (e.g. 'Bohemian Rhapsody').
        include_artwork: Also fetch album art + preview via iTunes Search.
    """
    try:
        artist = artist.strip()
        title = title.strip()
        if not artist or not title:
            return {"success": False, "error": "Both 'artist' and 'title' are required."}

        lyrics_resp = requests.get(
            f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}",
            timeout=15,
        )
        if lyrics_resp.status_code == 404:
            return {"success": False, "error": f"No lyrics found for '{artist} – {title}'."}
        lyrics_resp.raise_for_status()
        lyrics = (lyrics_resp.json().get("lyrics") or "").strip()

        artwork = ""
        preview = ""
        album = ""
        genre = ""
        release_date = ""
        track_url = ""
        if include_artwork:
            try:
                itunes = requests.get(
                    "https://itunes.apple.com/search",
                    params={"term": f"{artist} {title}", "entity": "musicTrack", "limit": 1},
                    timeout=10,
                ).json()
                hits = itunes.get("results") or []
                if hits:
                    h = hits[0]
                    artwork = (h.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
                    preview = h.get("previewUrl", "")
                    album = h.get("collectionName", "")
                    genre = h.get("primaryGenreName", "")
                    release_date = h.get("releaseDate", "")
                    track_url = h.get("trackViewUrl", "")
            except Exception:
                pass

        return {
            "success": True,
            "artist": artist,
            "title": title,
            "lyrics": lyrics,
            "line_count": len([l for l in lyrics.splitlines() if l.strip()]),
            "album": album,
            "genre": genre,
            "release_date": release_date,
            "artwork_url": artwork,
            "preview_url": preview,
            "track_url": track_url,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
