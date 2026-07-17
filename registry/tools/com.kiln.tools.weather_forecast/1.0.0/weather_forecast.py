"""
weather_forecast
----------------
Free, keyless weather forecast via Open-Meteo.
Accepts either a place name (auto-geocoded) or explicit lat/lon.
"""

import requests

REQUIRED_ENV_VARS = []

_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def weather_forecast(
    location: str = "",
    lat: float | None = None,
    lon: float | None = None,
    days: int = 7,
    units: str = "metric",
) -> dict:
    """Return current weather + daily forecast for a place or coordinate.

    Args:
        location: Place name (e.g. 'Singapore', 'Paris, France'). Ignored if lat+lon provided.
        lat, lon: Explicit coordinates.
        days:     Forecast days, 1-14.
        units:    'metric' (°C, km/h, mm) or 'imperial' (°F, mph, inch).
    """
    try:
        if lat is None or lon is None:
            if not location:
                return {"success": False, "error": "Provide either 'location' or lat/lon."}
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en", "format": "json"},
                timeout=10,
            ).json()
            results = geo.get("results") or []
            if not results:
                return {"success": False, "error": f"Location not found: {location}"}
            hit = results[0]
            lat, lon = hit["latitude"], hit["longitude"]
            resolved = f"{hit.get('name', location)}, {hit.get('country', '')}".strip(", ")
        else:
            resolved = f"{lat:.4f},{lon:.4f}"

        days = max(1, min(int(days), 14))
        imperial = units == "imperial"

        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
                "forecast_days": days,
                "temperature_unit": "fahrenheit" if imperial else "celsius",
                "wind_speed_unit": "mph" if imperial else "kmh",
                "precipitation_unit": "inch" if imperial else "mm",
                "timezone": "auto",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        current = data.get("current", {}) or {}
        daily = data.get("daily", {}) or {}
        forecast = []
        for i, date in enumerate(daily.get("time", [])):
            forecast.append({
                "date": date,
                "weather": _WMO.get(daily["weather_code"][i], "unknown"),
                "temp_max": daily["temperature_2m_max"][i],
                "temp_min": daily["temperature_2m_min"][i],
                "precipitation": daily["precipitation_sum"][i],
                "wind_max": daily["wind_speed_10m_max"][i],
            })

        return {
            "success": True,
            "location": resolved,
            "lat": lat,
            "lon": lon,
            "units": units,
            "current": {
                "temp": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "humidity": current.get("relative_humidity_2m"),
                "wind": current.get("wind_speed_10m"),
                "weather": _WMO.get(current.get("weather_code", -1), "unknown"),
            },
            "forecast": forecast,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
