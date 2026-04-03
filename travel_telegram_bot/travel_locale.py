from __future__ import annotations

import logging
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from threading import Lock
import time

from value_normalization import normalized_search_value
from weather_service import WeatherError, geocode_city

logger = logging.getLogger(__name__)


IATA_TRAVEL_INFO_URL = "https://www.iata.org/en/youandiata/travelers/"

RU_CIS_COUNTRIES = {
    "Россия",
    "Russian Federation",
    "Беларусь",
    "Belarus",
    "Казахстан",
    "Kazakhstan",
    "Армения",
    "Armenia",
    "Кыргызстан",
    "Kyrgyzstan",
    "Киргизия",
    "Узбекистан",
    "Uzbekistan",
    "Азербайджан",
    "Azerbaijan",
    "Таджикистан",
    "Tajikistan",
    "Грузия",
    "Georgia",
    "Молдова",
    "Moldova",
}

EURO_COUNTRIES = {
    "Germany",
    "France",
    "Spain",
    "Italy",
    "Portugal",
    "Netherlands",
    "Belgium",
    "Austria",
    "Greece",
    "Finland",
    "Ireland",
    "Cyprus",
    "Malta",
    "Slovakia",
    "Slovenia",
    "Estonia",
    "Latvia",
    "Lithuania",
    "Luxembourg",
    "Croatia",
    "Германия",
    "Франция",
    "Испания",
    "Италия",
    "Португалия",
    "Нидерланды",
    "Бельгия",
    "Австрия",
    "Греция",
    "Финляндия",
    "Ирландия",
    "Кипр",
    "Мальта",
    "Словакия",
    "Словения",
    "Эстония",
    "Латвия",
    "Литва",
    "Люксембург",
    "Хорватия",
}

CURRENCY_BY_COUNTRY = {
    "Россия": "RUB",
    "Russian Federation": "RUB",
    "Беларусь": "BYN",
    "Belarus": "BYN",
    "Казахстан": "KZT",
    "Kazakhstan": "KZT",
    "Армения": "AMD",
    "Armenia": "AMD",
    "Кыргызстан": "KGS",
    "Kyrgyzstan": "KGS",
    "Узбекистан": "UZS",
    "Uzbekistan": "UZS",
    "Азербайджан": "AZN",
    "Azerbaijan": "AZN",
    "Грузия": "GEL",
    "Georgia": "GEL",
    "Турция": "TRY",
    "Turkey": "TRY",
    "ОАЭ": "AED",
    "United Arab Emirates": "AED",
    "Таиланд": "THB",
    "Thailand": "THB",
    "США": "USD",
    "United States": "USD",
    "Великобритания": "GBP",
    "United Kingdom": "GBP",
    "Япония": "JPY",
    "Japan": "JPY",
    "Китай": "CNY",
    "China": "CNY",
    "Южная Корея": "KRW",
    "South Korea": "KRW",
    "Вьетнам": "VND",
    "Vietnam": "VND",
    "Индонезия": "IDR",
    "Indonesia": "IDR",
    "Индия": "INR",
    "India": "INR",
    "Чехия": "CZK",
    "Czechia": "CZK",
    "Венгрия": "HUF",
    "Hungary": "HUF",
    "Польша": "PLN",
    "Poland": "PLN",
    "Швейцария": "CHF",
    "Switzerland": "CHF",
}

COUNTRY_CACHE_MAXSIZE = 256
COUNTRY_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 часов для успешных запросов
COUNTRY_CACHE_NEGATIVE_TTL_SECONDS = 5 * 60  # 5 минут для ошибок
COUNTRY_CACHE_ERROR_TTL_SECONDS = 2 * 60  # 2 минуты для transient ошибок
GEOCODE_TIMEOUT_SECONDS = 6.0

_COUNTRY_CACHE: OrderedDict[str, tuple[float, str | None, bool]] = OrderedDict()  # (expires_at, value, is_error)
_COUNTRY_CACHE_LOCK = Lock()


@dataclass(slots=True)
class RouteLocale:
    origin_country: str | None
    destination_country: str | None

    @property
    def is_international(self) -> bool:
        return bool(self.origin_country and self.destination_country and self.origin_country != self.destination_country)

    @property
    def is_ru_cis_destination(self) -> bool:
        return is_ru_or_cis_country(self.destination_country)


def _cache_expiry_for(value: str | None, is_error: bool = False) -> float:
    if is_error:
        ttl_seconds = COUNTRY_CACHE_ERROR_TTL_SECONDS
    elif value:
        ttl_seconds = COUNTRY_CACHE_TTL_SECONDS
    else:
        ttl_seconds = COUNTRY_CACHE_NEGATIVE_TTL_SECONDS
    return time.monotonic() + ttl_seconds


def _resolve_place_country_uncached(normalized: str) -> tuple[str | None, bool]:
    """
    Возвращает (country, is_error).
    is_error=True означает временную ошибку (стоит закэшировать кратко).
    """
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            geo = executor.submit(geocode_city, normalized).result(timeout=GEOCODE_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        logger.warning("Geocoding timeout for %r (%.1fs)", normalized, GEOCODE_TIMEOUT_SECONDS)
        return None, True  # Временная ошибка — короткий TTL
    except WeatherError as exc:
        logger.warning("Geocoding error for %r: %s", normalized, exc)
        return None, False  # Постоянная ошибка (город не найден) — обычный негативный TTL
    if not geo:
        return None, False
    return (geo.country or "").strip() or None, False


def _clear_resolve_place_country_cache() -> None:
    with _COUNTRY_CACHE_LOCK:
        _COUNTRY_CACHE.clear()


def resolve_place_country(place: str) -> str | None:
    normalized = normalized_search_value(place)
    if not normalized:
        return None

    now = time.monotonic()
    with _COUNTRY_CACHE_LOCK:
        cached = _COUNTRY_CACHE.get(normalized)
        if cached is not None:
            expires_at, cached_value, cached_is_error = cached
            if expires_at > now:
                _COUNTRY_CACHE.move_to_end(normalized)
                return cached_value
            _COUNTRY_CACHE.pop(normalized, None)

    resolved_country, is_error = _resolve_place_country_uncached(normalized)
    with _COUNTRY_CACHE_LOCK:
        _COUNTRY_CACHE[normalized] = (_cache_expiry_for(resolved_country, is_error), resolved_country, is_error)
        _COUNTRY_CACHE.move_to_end(normalized)
        while len(_COUNTRY_CACHE) > COUNTRY_CACHE_MAXSIZE:
            _COUNTRY_CACHE.popitem(last=False)
    return resolved_country


def is_ru_or_cis_country(country: str | None) -> bool:
    return bool(country and country.strip() in RU_CIS_COUNTRIES)


def default_currency_for_country(country: str | None) -> str:
    if not country:
        return "LOCAL"
    if country in EURO_COUNTRIES:
        return "EUR"
    return CURRENCY_BY_COUNTRY.get(country, "LOCAL")


def detect_route_locale(destination: str, origin: str | None = None) -> RouteLocale:
    return RouteLocale(
        origin_country=resolve_place_country(origin or ""),
        destination_country=resolve_place_country(destination),
    )


def build_entry_requirements_text(destination: str, origin: str | None = None) -> str:
    locale = detect_route_locale(destination, origin)
    if not locale.is_international:
        return ""

    origin_country = locale.origin_country or "страны выезда"
    destination_country = locale.destination_country or "страны назначения"
    return "\n".join(
        [
            f"Маршрут международный: {origin_country} → {destination_country}.",
            "Точные визовые и въездные требования зависят от гражданства, ВНЖ, типа паспорта и транзитов.",
            "Перед покупкой билетов проверьте: нужна ли виза или ETA, срок действия паспорта, транзитные правила, страховку и условия въезда.",
            f"Официальный ориентир для проверки: IATA Travel Centre / Timatic — {IATA_TRAVEL_INFO_URL}",
        ]
    )
