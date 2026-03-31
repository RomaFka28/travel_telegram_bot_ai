from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from value_normalization import normalized_search_value
from weather_service import WeatherError, geocode_city


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


@lru_cache(maxsize=256)
def resolve_place_country(place: str) -> str | None:
    normalized = normalized_search_value(place)
    if not normalized:
        return None
    try:
        geo = geocode_city(normalized)
    except WeatherError:
        return None
    return (geo.country or "").strip() or None


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
