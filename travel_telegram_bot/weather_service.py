from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, timedelta

from date_utils import parse_dates_range
from http_utils import safe_http_get


class WeatherError(RuntimeError):
    pass


@dataclass(slots=True)
class GeoResult:
    name: str
    country: str | None
    latitude: float
    longitude: float
    timezone: str | None


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
        raw = safe_http_get(url, max_retries=2, timeout=20)
        raw_str = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        raise WeatherError(f"Geocoding error: {exc}") from exc

    payload = json.loads(raw_str)
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


def fetch_weather_summary(destination: str, dates_text: str) -> str | None:
    """
    Returns a short Russian weather block for the trip dates.
    Uses Open-Meteo (no API key). Forecast availability is limited (typically ~16 days).
    """
    dates = parse_dates_range(dates_text)
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
        raw = safe_http_get(url, max_retries=2, timeout=20)
        raw_str = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        raise WeatherError(f"Forecast error: {exc}") from exc

    payload = json.loads(raw_str)
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
