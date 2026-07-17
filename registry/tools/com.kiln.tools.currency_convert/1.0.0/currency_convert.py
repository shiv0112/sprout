"""
currency_convert.py — Kiln tool implementation

Uses the free exchangerate-api.com API for live exchange rates.
"""

import json
import urllib.request

REQUIRED_ENV_VARS = []


def currency_convert(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert a monetary amount from one currency to another using live rates."""
    try:
        url = f"https://open.er-api.com/v6/latest/{from_currency}"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        rates = data.get("rates", {})
        rate = rates.get(to_currency)

        if rate is None:
            return {"error": f"Currency '{to_currency}' not found in rates for '{from_currency}'.", "success": False}

        converted = round(amount * rate, 2)

        return {
            "amount": amount,
            "from_currency": from_currency,
            "to_currency": to_currency,
            "converted": converted,
            "rate": round(rate, 6),
            "success": True,
        }
    except Exception as e:
        return {"error": f"Failed to fetch exchange rate: {e}", "success": False}
