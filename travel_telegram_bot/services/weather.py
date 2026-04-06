"""
Сервис погоды через Open-Meteo API (без API ключа).

Функции:
- geocode — геокодирование города
- get_forecast_for_date — прогноз на конкретную дату
- format_forecast — человекочитаемый текст прогноза
- get_forecast_for_city — orchestrator: город + дата → прогноз
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("☀️", "Ясно"),
    1: ("🌤", "Малооблачно"),
    2: ("⛅", "Переменная облачность"),
    3: ("☁️", "Пасмурно"),
    45: ("🌫", "Туман"),
    48: ("🌫", "Изморозь"),
    51: ("🌦", "Лёгкая морось"),
    53: ("🌦", "Морось"),
    55: ("🌧", "Сильная морось"),
    61: ("🌧", "Небольшой дождь"),
    63: ("🌧", "Дождь"),
    65: ("🌧", "Сильный дождь"),
    71: ("🌨", "Небольшой снег"),
    73: ("🌨", "Снег"),
    75: ("❄️", "Сильный снег"),
    77: ("🌨", "Снежная крупа"),
    80: ("🌦", "Небольшой ливень"),
    81: ("🌧", "Ливень"),
    82: ("⛈", "Сильный ливень"),
    85: ("🌨", "Снегопад"),
    86: ("❄️", "Сильный снегопад"),
    95: ("⛈", "Гроза"),
    96: ("⛈", "Гроза с градом"),
    99: ("⛈", "Сильная гроза с градом"),
}


async def geocode(city: str) -> tuple[float, float] | None:
    """
    Геокодирует название города через Open-Meteo geocoding API.

    Returns (latitude, longitude) или None если не найден.
    """
    params = {"name": city.strip(), "count": 1, "language": "ru"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(GEOCODING_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as e:
        logger.warning("Geocoding failed for %r: %s", city, e)
        return None

    results = data.get("results")
    if not results or not isinstance(results, list) or len(results) == 0:
        return None

    first = results[0]
    try:
        lat = float(first["latitude"])
        lon = float(first["longitude"])
        return lat, lon
    except (KeyError, TypeError, ValueError):
        return None


async def get_forecast_for_date(
    lat: float, lon: float, target_date: str,
) -> dict[str, Any] | None:
    """
    Получает прогноз погоды на конкретную дату (YYYY-MM-DD).

    Open-Meteo отдаёт daily forecast до 16 дней вперёд.
    Возвращает срез данных для нужного дня или None если дата вне диапазона.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max,weathercode,precipitation_sum,wind_speed_10m_max",
        "start_date": target_date,
        "end_date": target_date,
        "timezone": "auto",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(WEATHER_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    daily = data.get("daily", {})
    times = daily.get("time", [])
    if not times or times[0] != target_date:
        return None

    # Извлекаем данные первого (и единственного) дня
    result: dict[str, Any] = {}
    for key in daily:
        if key == "time":
            continue
        values = daily[key]
        if isinstance(values, list) and len(values) > 0:
            result[key] = values[0]

    return result if result else None


def format_forecast(city: str, forecast: dict[str, Any], target_date: str) -> str:
    """
    Форматирует прогноз погоды на дату в человекочитаемый текст.

    Args:
        city: название города
        forecast: данные прогноза (max/min temp, weathercode, etc.)
        target_date: дата в формате YYYY-MM-DD

    Returns:
        Строка с эмодзи и прогнозом.
    """
    if not forecast:
        return ""

    wmo_code = forecast.get("weathercode", -1)
    emoji, desc = WMO_CODES.get(wmo_code, ("🌡", "Нет данных"))

    t_max = forecast.get("temperature_2m_max")
    t_min = forecast.get("temperature_2m_min")
    feels_max = forecast.get("apparent_temperature_max")
    precip = forecast.get("precipitation_sum")
    wind_max = forecast.get("wind_speed_10m_max")

    # Форматируем дату для вывода
    try:
        from datetime import date as date_cls
        d = date_cls.fromisoformat(target_date)
        date_str = d.strftime("%d.%m.%Y")
    except ValueError:
        date_str = target_date

    lines: list[str] = [f"📍 Прогноз на {date_str} — {city}"]
    if t_max is not None and t_min is not None:
        feels_part = f" (ощущается до {round(feels_max)}°C)" if feels_max is not None else ""
        lines.append(f"🌡 {round(t_min)}°C … {round(t_max)}°C{feels_part}")
    if precip is not None:
        lines.append(f"🌧 Осадки: {precip} мм")
    if wind_max is not None:
        lines.append(f"💨 Ветер: до {round(wind_max)} км/ч")
    lines.append(f"{emoji} {desc}")

    return "\n".join(lines)


async def get_forecast_for_city(city: str, target_date: str) -> str:
    """
    Orchestrator: город + дата → прогноз погоды.

    На геодкодинге: «❌ Город не найден.»
    На HTTP ошибке: «❌ Сервис погоды недоступен.»
    На отсутствии данных: «❌ Прогноз на <date> недоступен.»
    """
    coords = await geocode(city)
    if not coords:
        return f"❌ Город «{city}» не найден."

    lat, lon = coords
    try:
        forecast = await get_forecast_for_date(lat, lon, target_date)
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as e:
        logger.warning("Forecast fetch failed for %r (%s,%s) on %s: %s", city, lat, lon, target_date, e)
        return f"❌ Сервис погоды недоступен. Попробуйте позже."

    if not forecast:
        return f"❌ Прогноз на {target_date} для «{city}» недоступен (дальний срок)."

    return format_forecast(city, forecast, target_date)
