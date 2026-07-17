"""
geocode_location
----------------
Forward and reverse geocoding via OpenStreetMap's Nominatim. No key required.
Pass a place name to resolve to coordinates, or pass lat+lon for reverse lookup.
"""

import requests

REQUIRED_ENV_VARS = []

_HEADERS = {"User-Agent": "Kiln/1.0 (https://kiln.dev) geocode_location"}


def geocode_location(
    query: str = "",
    lat: float | None = None,
    lon: float | None = None,
    limit: int = 5,
) -> dict:
    """Resolve a place name to coordinates, or coordinates to an address.

    Args:
        query:   Address or place name (forward geocoding).
        lat,lon: Coordinates to reverse-geocode. Takes precedence over query.
        limit:   Max forward-geocode results (1-10).
    """
    try:
        if lat is not None and lon is not None:
            data = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18, "addressdetails": 1},
                headers=_HEADERS,
                timeout=10,
            ).json()
            if not data or data.get("error"):
                return {"success": False, "error": data.get("error", "No match.")}
            return {
                "success": True,
                "mode": "reverse",
                "address": data.get("display_name", ""),
                "components": data.get("address", {}),
                "lat": float(data.get("lat", lat)),
                "lon": float(data.get("lon", lon)),
            }

        if not query:
            return {"success": False, "error": "Provide 'query' or lat+lon."}

        limit = max(1, min(int(limit), 10))
        results = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 1},
            headers=_HEADERS,
            timeout=10,
        ).json()
        if not results:
            return {"success": False, "error": f"No match for '{query}'."}

        matches = [
            {
                "display_name": r["display_name"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "type": r.get("type", ""),
                "importance": r.get("importance", 0),
            }
            for r in results
        ]
        top = matches[0]
        return {
            "success": True,
            "mode": "forward",
            "query": query,
            "lat": top["lat"],
            "lon": top["lon"],
            "address": top["display_name"],
            "matches": matches,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
