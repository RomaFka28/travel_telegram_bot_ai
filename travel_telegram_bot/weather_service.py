from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta


class WeatherError(RuntimeError):
    pass


@dataclass(slots=True)
class GeoResult:
    name: str
    country: str | None
    latitude: float
    longitude: float
    timezone: str | None


MONTHS_RU: dict[str, int] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "мая": 5,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}


def geocode_city(name: str) -> GeoResult | None:
    name = (name or "").strip()
    if not name:
        return None
    params = {
        "name": name,
        "count": "1",
        "language": "ru",
        "format": "json",
    }
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise WeatherError(f"Geocoding error: {exc}") from exc

    payload = json.loads(raw)
    results = payload.get("results") or []
    if not results:
        return None
    item = results[0]
    return GeoResult(
        name=str(item.get("name") or name),
        country=(str(item.get("country")) if item.get("country") else None),
        latitude=float(item["latitude"]),
        longitude=float(item["longitude"]),
        timezone=(str(item.get("timezone")) if item.get("timezone") else None),
    )


def _parse_dates_range(dates_text: str) -> tuple[date, date] | None:
    """
    Best-effort parser for strings like:
    - "12–16 июня"
    - "12-16 июня"
    - "12 июня"
    Returns a date range in the current year.
    """
    text = (dates_text or "").strip().lower()
    if not text or text == "не указаны":
        return None

    match = re.search(
        r"\b(\d{1,2})\s*(?:-|–|—|до)?\s*(\d{0,2})\s*([а-яё]+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    start_day = int(match.group(1))
    end_raw = match.group(2).strip()
    end_day = int(end_raw) if end_raw else start_day
    month_word = match.group(3)

    month = None
    for key, value in MONTHS_RU.items():
        if key in month_word:
            month = value
            break
    if not month:
        return None

    year = date.today().year
    try:
        start = date(year, month, start_day)
        end = date(year, month, end_day)
    except ValueError:
        return None
    if end < start:
        start, end = end, start
    return start, end


def fetch_weather_summary(destination: str, dates_text: str) -> str | None:
    """
    Returns a short Russian weather block for the trip dates.
    Uses Open-Meteo (no API key). Forecast availability is limited (typically ~16 days).
    """
    dates = _parse_dates_range(dates_text)
    if not dates:
        return None

    start, end = dates
    today = date.today()
    if start < today - timedelta(days=2):
        return "Погода: даты уже в прошлом — прогноз не строю."
    if start > today + timedelta(days=16):
        return None

    geo = geocode_city(destination)
    if not geo:
        return "Погода: не смог определить город для прогноза."

    forecast_start = max(start, today)
    forecast_end = min(end, today + timedelta(days=16))

    params = {
        "latitude": f"{geo.latitude:.6f}",
        "longitude": f"{geo.longitude:.6f}",
        "timezone": "auto",
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "wind_speed_10m_max",
            ]
        ),
        "start_date": forecast_start.isoformat(),
        "end_date": forecast_end.isoformat(),
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)

    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise WeatherError(f"Forecast error: {exc}") from exc

    payload = json.loads(raw)
    daily = payload.get("daily") or {}
    days = daily.get("time") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    prcp = daily.get("precipitation_sum") or []
    wind = daily.get("wind_speed_10m_max") or []

    if not days:
        return "Погода: прогноз недоступен."

    def _avg(values: list[object]) -> float | None:
        numbers = [float(value) for value in values if value is not None]
        return (sum(numbers) / len(numbers)) if numbers else None

    avg_max = _avg(tmax)
    avg_min = _avg(tmin)
    sum_prcp = sum(float(value) for value in prcp if value is not None) if prcp else 0.0
    max_wind = max((float(value) for value in wind if value is not None), default=None)

    place = geo.name + (f", {geo.country}" if geo.country else "")
    date_label = f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"

    parts: list[str] = [f"🌦 Погода ({place}, {date_label})"]
    if avg_min is not None and avg_max is not None:
        parts.append(f"Температура: в среднем {avg_min:.0f}…{avg_max:.0f}°C")
    if sum_prcp:
        parts.append(f"Осадки: суммарно ≈ {sum_prcp:.0f} мм")
    if max_wind is not None:
        parts.append(f"Ветер: до ≈ {max_wind:.0f} м/с")

    if end > forecast_end:
        parts.append("Примечание: прогноз есть не на все даты (ограничение горизонта).")
    return "\n".join(parts)
