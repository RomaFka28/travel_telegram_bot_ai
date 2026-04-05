"""
Сервис погоды через Open-Meteo API (без API ключа).

Функции:
- geocode — геокодирование города
- get_current_weather — текущая погода по координатам
- format_weather — человекочитаемый текст
- get_weather_for_city — orchestrator: город → текст погоды
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


async def get_current_weather(lat: float, lon: float) -> dict[str, Any]:
    """
    Получает текущую погоду по координатам через Open-Meteo forecast API.

    Returns полный JSON ответа.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weathercode,windspeed_10m,relative_humidity_2m,precipitation",
        "timezone": "auto",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(WEATHER_URL, params=params)
        resp.raise_for_status()
        return resp.json()


def format_weather(city: str, data: dict[str, Any]) -> str:
    """
    Форматирует данные погоды в человекочитаемый текст на русском.

    Args:
        city: название города
        data: ответ Open-Meteo API

    Returns:
        Строка с эмодзи и данными о погоде.
    """
    current = data.get("current", {})
    if not current:
        return f"❌ Нет данных о погоде для {city}."

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    wmo_code = current.get("weathercode", -1)
    wind = current.get("windspeed_10m")
    humidity = current.get("relative_humidity_2m")
    precip = current.get("precipitation")

    emoji, desc = WMO_CODES.get(wmo_code, ("🌡", "Неизвестно"))

    lines: list[str] = [f"{emoji} {city}"]
    if temp is not None:
        feels_text = f" (ощущается как {round(feels)}°C)" if feels is not None else ""
        lines.append(f"🌡 Температура: {round(temp)}°C{feels_text}")
    if humidity is not None:
        lines.append(f"💧 Влажность: {round(humidity)}%")
    if wind is not None:
        lines.append(f"💨 Ветер: {round(wind)} км/ч")
    if precip is not None:
        lines.append(f"🌧 Осадки: {precip} мм")
    lines.append(desc)

    return "\n".join(lines)


async def get_weather_for_city(city: str) -> str:
    """
    Orchestrator: город → текст погоды.

    На геодкодинге: «❌ Город не найден.»
    На HTTP ошибке: «❌ Сервис погоды недоступен.»
    """
    coords = await geocode(city)
    if not coords:
        return f"❌ Город «{city}» не найден."

    lat, lon = coords
    try:
        data = await get_current_weather(lat, lon)
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as e:
        logger.warning("Weather fetch failed for %r (%s,%s): %s", city, lat, lon, e)
        return f"❌ Сервис погоды недоступен. Попробуйте позже."

    return format_weather(city, data)
