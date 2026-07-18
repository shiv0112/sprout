"""
stock_quote
-----------
Real-time equity / ETF / crypto quotes via Yahoo Finance's public
query API. No key required. Returns price, change, day range,
52-week range, market cap, and recent intraday closes for a sparkline.
"""

import requests

REQUIRED_ENV_VARS = []

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Sprout/1.0)",
    "Accept": "application/json",
}


def stock_quote(symbol: str, interval: str = "5m", range_: str = "1d") -> dict:
    """Return a rich quote for a ticker.

    Args:
        symbol:   Ticker (e.g. 'AAPL', 'NVDA', 'BTC-USD', '^GSPC').
        interval: Chart interval — '1m', '5m', '15m', '1h', '1d'.
        range_:   Chart range — '1d', '5d', '1mo', '3mo', '1y', '5y', 'max'.
    """
    try:
        symbol = symbol.strip().upper()

        chart = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": interval, "range": range_, "includePrePost": "false"},
            headers=_HEADERS,
            timeout=15,
        )
        chart.raise_for_status()
        body = chart.json().get("chart", {})
        if body.get("error"):
            return {"success": False, "error": body["error"].get("description", "Yahoo error.")}

        result = (body.get("result") or [None])[0]
        if not result:
            return {"success": False, "error": f"No data for symbol '{symbol}'."}

        meta = result.get("meta", {}) or {}
        closes = ((result.get("indicators", {}) or {}).get("quote") or [{}])[0].get("close") or []
        closes = [c for c in closes if c is not None][-150:]

        price = meta.get("regularMarketPrice")
        prev_raw = meta.get("chartPreviousClose")
        prev = prev_raw if prev_raw is not None else meta.get("previousClose")
        # Use explicit None checks: a 0.0 previous close is unusual but
        # legitimate (e.g. brand-new IPOs intraday) and shouldn't suppress
        # the absolute change. Percent change still requires prev != 0 to
        # avoid division-by-zero.
        change = (price - prev) if (price is not None and prev is not None) else None
        change_pct = (
            (change / prev * 100)
            if (change is not None and prev not in (None, 0))
            else None
        )

        return {
            "success": True,
            "symbol": meta.get("symbol", symbol),
            "name": meta.get("longName") or meta.get("shortName") or symbol,
            "exchange": meta.get("exchangeName"),
            "currency": meta.get("currency"),
            "price": price,
            "previous_close": prev,
            "change": round(change, 4) if change is not None else None,
            "change_pct": round(change_pct, 3) if change_pct is not None else None,
            "day_high": meta.get("regularMarketDayHigh"),
            "day_low": meta.get("regularMarketDayLow"),
            "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
            "volume": meta.get("regularMarketVolume"),
            "timezone": meta.get("exchangeTimezoneName"),
            "market_state": meta.get("marketState"),
            "sparkline": closes,
            "interval": interval,
            "range": range_,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
