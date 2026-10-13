"""Where "today" comes from: plain HTTP, no keys, no agent turn.

- **Weather** is MSN Weather, the same service the Windows Widgets board shows
  (the key below is the public one MSN's own pages ship with). If it stops
  answering, Open-Meteo stands in.
- **Markets** are Yahoo Finance's chart endpoint: the same NIFTY, SENSEX, Nifty Bank
  and Midcap figures the Widgets markets card shows.
- **News** is the RSS reader in `ambient/news.py`, plus a business feed for finance.
- **Calendars** are iCalendar URLs: the public Indian holidays calendar, and any
  private address you put in `.env` (`NOVA_CALENDAR_ICS`).

Every fetcher returns None (or []) on failure rather than raising: a briefing with
the weather missing is still a briefing.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from ..ambient import news

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Nova/1.0"}
MSN_KEY = "j5i4gDqHL6nGYwx5wi5kRhXjtf2c5qgFX9fzfk0TOo"
FINANCE_FEEDS = [
    ("Google News Business", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en"),
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
]
HOLIDAYS_ICS = ("https://calendar.google.com/calendar/ical/"
                "en.indian%23holiday%40group.v.calendar.google.com/public/basic.ics")

# WMO weather codes (Open-Meteo) in words.
_WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
        51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
        65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers",
        81: "rain showers", 82: "violent showers", 95: "thunderstorms", 96: "thunderstorms with hail",
        99: "thunderstorms with hail"}


def _get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as response:
        return response.read()


# -- weather --------------------------------------------------------------------------
def weather(lat: float, lon: float, place: str, timeout: float = 8.0) -> dict[str, Any] | None:
    try:
        return _msn_weather(lat, lon, place, timeout)
    except Exception:
        pass
    try:
        return _open_meteo(lat, lon, place, timeout)
    except Exception:
        return None


def _msn_weather(lat: float, lon: float, place: str, timeout: float) -> dict[str, Any]:
    query = urllib.parse.urlencode({"apikey": MSN_KEY, "lat": lat, "lon": lon, "units": "C",
                                    "locale": "en-in", "region": "in"})
    data = json.loads(_get(f"https://assets.msn.com/service/weather/overview?{query}", timeout))
    w = data["value"][0]["responses"][0]["weather"][0]
    now, days = w["current"], w["forecast"]["days"]
    today = days[0]["daily"]
    hours = [h for day in days[:2] for h in day.get("hourly", [])][:12]
    aqi = now.get("aqi")
    return {
        "place": place, "source": "MSN Weather",
        "now": {"temp": now.get("temp"), "feels": now.get("feels"), "text": now.get("cap", ""),
                "aqi": int(aqi) if aqi else None, "aqi_text": now.get("aqiSeverity", "")},
        "today": {"high": today.get("tempHi"), "low": today.get("tempLo"),
                  "day": today.get("day", {}).get("summary", ""),
                  "night": today.get("night", {}).get("summary", ""),
                  "rain_chance": today.get("precip")},
        "hours": [{"at": h["valid"][11:16], "temp": h.get("temp"), "text": h.get("cap", ""),
                   "rain_chance": h.get("precip")} for h in hours],
        "next_days": [{"date": d["daily"]["valid"][:10], "high": d["daily"].get("tempHi"),
                       "low": d["daily"].get("tempLo"), "text": d["daily"].get("pvdrCap", ""),
                       "rain_chance": d["daily"].get("precip")} for d in days[1:6]],
        "alerts": [a.get("title") or a.get("safetyGuide", "") for a in w.get("alerts", [])][:2],
    }


def _open_meteo(lat: float, lon: float, place: str, timeout: float) -> dict[str, Any]:
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 6,
        "current": "temperature_2m,apparent_temperature,weather_code",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"})
    data = json.loads(_get(f"https://api.open-meteo.com/v1/forecast?{query}", timeout))
    cur, hourly, daily = data["current"], data["hourly"], data["daily"]
    now_hour = cur["time"][:13]
    first = next((i for i, t in enumerate(hourly["time"]) if t[:13] > now_hour), 0)
    return {
        "place": place, "source": "Open-Meteo",
        "now": {"temp": cur["temperature_2m"], "feels": cur["apparent_temperature"],
                "text": _WMO.get(cur["weather_code"], ""), "aqi": None, "aqi_text": ""},
        "today": {"high": daily["temperature_2m_max"][0], "low": daily["temperature_2m_min"][0],
                  "day": _WMO.get(daily["weather_code"][0], ""), "night": "",
                  "rain_chance": daily["precipitation_probability_max"][0]},
        "hours": [{"at": hourly["time"][i][11:16], "temp": hourly["temperature_2m"][i],
                   "text": _WMO.get(hourly["weather_code"][i], ""),
                   "rain_chance": hourly["precipitation_probability"][i]}
                  for i in range(first, min(first + 12, len(hourly["time"])))],
        "next_days": [{"date": daily["time"][i], "high": daily["temperature_2m_max"][i],
                       "low": daily["temperature_2m_min"][i], "text": _WMO.get(daily["weather_code"][i], ""),
                       "rain_chance": daily["precipitation_probability_max"][i]} for i in range(1, 6)],
        "alerts": [],
    }


# -- markets --------------------------------------------------------------------------
def markets(symbols: dict[str, str], timeout: float = 8.0) -> list[dict[str, Any]]:
    """{"NIFTY": "^NSEI", ...} -> quotes with the change against the previous close."""
    quotes = []
    for name, symbol in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d"
            result = json.loads(_get(url, timeout))["chart"]["result"][0]
            meta = result["meta"]
            closes = [c for c in result["indicators"]["quote"][0].get("close", []) if c is not None]
            price = meta["regularMarketPrice"]
            # chartPreviousClose is the close before the 5-day window; the day before is closes[-2].
            previous = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
            change = (price - previous) / previous * 100 if previous else 0.0
            quotes.append({
                "name": name, "price": round(price, 2), "change": f"{change:+.2f}%",
                "direction": "up" if change > 0.05 else "down" if change < -0.05 else "flat",
                "points": [round(c, 2) for c in closes], "source": "Yahoo Finance",
                "url": f"https://finance.yahoo.com/quote/{urllib.parse.quote(symbol)}",
                "at": datetime.fromtimestamp(meta.get("regularMarketTime", 0)).strftime("%Y-%m-%d %H:%M"),
            })
        except Exception:
            continue
    return quotes


# -- news -----------------------------------------------------------------------------
def india_news(count: int, topics: list[str] | None = None) -> list[dict[str, Any]]:
    try:
        return news.fetch(count, topics)[0]
    except Exception:
        return []


def finance_news(count: int, timeout: float = 8.0) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source, url in FINANCE_FEEDS:
        try:
            items = news._read_feed(source, url, timeout)
        except Exception:
            continue
        for item in items:
            key = item["title"].lower()[:60]
            if key not in seen:
                seen.add(key)
                picked.append(item)
            if len(picked) >= count:
                return picked
    return picked


# -- calendars ------------------------------------------------------------------------
def calendar_text(url: str, timeout: float = 10.0) -> str | None:
    try:
        return _get(url, timeout).decode("utf-8", "replace")
    except Exception:
        return None
