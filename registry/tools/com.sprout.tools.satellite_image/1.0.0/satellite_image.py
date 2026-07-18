"""
satellite_image
---------------
Produce a static satellite / street map image of any place on earth.
Uses Esri's World Imagery tile service (public, keyless) for satellite,
and OpenStreetMap for the street style. Auto-geocodes place names via
Nominatim. Returns a stitched PNG as base64 and a file path.
"""

import base64
import io
import math
import tempfile

import requests

REQUIRED_ENV_VARS = []

_HEADERS = {"User-Agent": "Sprout/1.0 (https://sprout.dev) satellite_image"}

_TILE_URLS = {
    "satellite": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "street": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "topo": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
}


def _lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    # Clamp latitude to the slippy-map valid range; the Web Mercator math
    # below diverges as |lat| → 90°. Then clamp the resulting tile indices
    # to [0, n-1] so an edge-of-world coordinate doesn't request a 404 tile.
    lat = max(-85.05112878, min(85.05112878, lat))
    lon = ((lon + 180.0) % 360.0) - 180.0  # wrap longitude into [-180, 180)
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n)
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return x, y


def satellite_image(
    location: str = "",
    lat: float | None = None,
    lon: float | None = None,
    zoom: int = 15,
    style: str = "satellite",
    tiles: int = 3,
) -> dict:
    """Build a stitched static map image for a place.

    Args:
        location: Place name (auto-geocoded if lat/lon omitted).
        lat,lon:  Explicit coordinates.
        zoom:     Zoom level 1-19 (higher = closer).
        style:    'satellite', 'street', or 'topo'.
        tiles:    Odd side length in tiles (1, 3, 5). Total tiles = tiles².
    """
    try:
        from PIL import Image

        if lat is None or lon is None:
            if not location:
                return {"success": False, "error": "Provide 'location' or lat+lon."}
            geo = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": location, "format": "jsonv2", "limit": 1},
                headers=_HEADERS,
                timeout=10,
            ).json()
            if not geo:
                return {"success": False, "error": f"Location not found: {location}"}
            lat, lon = float(geo[0]["lat"]), float(geo[0]["lon"])
            resolved = geo[0]["display_name"]
        else:
            resolved = f"{lat:.5f},{lon:.5f}"

        zoom = max(1, min(int(zoom), 19))
        tiles = tiles if tiles in (1, 3, 5) else 3
        pad = tiles // 2

        template = _TILE_URLS.get(style, _TILE_URLS["satellite"])
        tx, ty = _lonlat_to_tile(lon, lat, zoom)
        n = 2 ** zoom

        canvas = Image.new("RGB", (256 * tiles, 256 * tiles))
        for dx in range(-pad, pad + 1):
            for dy in range(-pad, pad + 1):
                # Wrap longitude tile index horizontally (the world is
                # cylindrical) and clamp latitude tile index vertically (it
                # isn't) so edge-of-world coordinates can't 404.
                x_idx = (tx + dx) % n
                y_idx = max(0, min(n - 1, ty + dy))
                url = template.format(z=zoom, x=x_idx, y=y_idx)
                resp = requests.get(url, headers=_HEADERS, timeout=15)
                resp.raise_for_status()
                tile = Image.open(io.BytesIO(resp.content)).convert("RGB")
                canvas.paste(tile, ((dx + pad) * 256, (dy + pad) * 256))

        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        b64 = base64.b64encode(data).decode("utf-8")

        tmp = tempfile.NamedTemporaryFile(suffix=".png", prefix="satmap_", delete=False)
        tmp.write(data)
        tmp.close()

        return {
            "success": True,
            "location": resolved,
            "lat": lat,
            "lon": lon,
            "zoom": zoom,
            "style": style,
            "width": 256 * tiles,
            "height": 256 * tiles,
            "data_url": f"data:image/png;base64,{b64}",
            "file_path": tmp.name,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
