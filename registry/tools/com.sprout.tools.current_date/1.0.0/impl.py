"""
current_date
------------
Return the current date and time in a requested format. No network calls —
uses the system clock, so it works offline and doesn't depend on any API.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

REQUIRED_ENV_VARS = []


def current_date(format: str | None = None, tz: str = "UTC") -> dict:
    """Return the current date/time in the given timezone and format.

    Args:
        format: strftime-compatible format (default: ISO-8601).
                Accepts the friendly tokens YYYY, MM, DD, HH, mm, ss as aliases.
        tz:     IANA timezone name (e.g. 'Asia/Singapore', 'America/New_York').
    """
    try:
        zone = ZoneInfo(tz) if tz and tz != "UTC" else timezone.utc
    except Exception:
        return {"success": False, "error": f"Unknown timezone '{tz}'"}

    now = datetime.now(zone)

    if format:
        friendly = (
            format.replace("YYYY", "%Y")
                  .replace("MM", "%m")
                  .replace("DD", "%d")
                  .replace("HH", "%H")
                  .replace("mm", "%M")
                  .replace("ss", "%S")
        )
        try:
            formatted = now.strftime(friendly)
        except Exception as exc:
            return {"success": False, "error": f"Bad format string: {exc}"}
    else:
        formatted = now.isoformat()

    return {
        "date": formatted,
        "timestamp": int(now.timestamp()),
        "iso": now.isoformat(),
        "timezone": tz,
        "weekday": now.strftime("%A"),
        "success": True,
    }
