"""
com.aria.tools.crypto_price
Real-time crypto prices via CoinGecko public API. No API key required.
"""
import requests


def crypto_price(coin_id: str, currency: str = "usd") -> dict:
    """Get real-time cryptocurrency price and 24h change via CoinGecko."""
    try:
        currency = currency.lower()
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids":                   coin_id.lower(),
                "vs_currencies":         currency,
                "include_24hr_change":   "true",
                "include_market_cap":    "false",
            },
            headers={"User-Agent": "ARIA/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if coin_id.lower() not in data:
            return {"success": False, "error": f"Coin '{coin_id}' not found. Check the CoinGecko coin ID."}

        coin_data  = data[coin_id.lower()]
        price      = coin_data.get(currency, 0)
        change_24h = coin_data.get(f"{currency}_24h_change", 0)

        return {
            "coin":       coin_id.lower(),
            "price":      round(price, 6) if price < 1 else round(price, 2),
            "currency":   currency.upper(),
            "change_24h": round(change_24h, 2),
            "success":    True,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
