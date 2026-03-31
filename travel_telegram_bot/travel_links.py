from __future__ import annotations

import urllib.parse

from travel_locale import detect_route_locale
from travel_result_models import TravelSearchResult, trim_results
from value_normalization import normalized_search_value
from weather_service import _parse_dates_range


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tickets": ("ÃÂ±ÃÂ¸ÃÂ»ÃÂµÃ‘â€š", "ÃÂ°ÃÂ²ÃÂ¸ÃÂ°", "Ã‘ÂÃÂ°ÃÂ¼ÃÂ¾ÃÂ»ÃÂµÃ‘â€š", "Ã‘ÂÃÂ°ÃÂ¼ÃÂ¾ÃÂ»Ã‘â€˜Ã‘â€š", "ÃÂ»ÃÂµÃ‘â€šÃÂ¸ÃÂ¼", "ÃÂ¿ÃÂµÃ‘â‚¬ÃÂµÃÂ»ÃÂµÃ‘â€š", "ÃÂ¿ÃÂµÃ‘â‚¬ÃÂµÃÂ»Ã‘â€˜Ã‘â€š", "Ã‘â‚¬ÃÂµÃÂ¹Ã‘Â"),
    "housing": ("ÃÂ¾Ã‘â€šÃÂµÃÂ»", "ÃÂ³ÃÂ¾Ã‘ÂÃ‘â€šÃÂ¸ÃÂ½ÃÂ¸", "ÃÂ¶ÃÂ¸ÃÂ»Ã‘Å’", "ÃÂ°ÃÂ¿ÃÂ°Ã‘â‚¬Ã‘â€š", "ÃÂºÃÂ²ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ¸Ã‘â‚¬", "Ã‘ÂÃ‘Æ’Ã‘â€šÃÂ¾Ã‘â€¡", "ÃÂ½ÃÂ¾Ã‘â€¡ÃÂµÃÂ²", "ÃÂ´ÃÂ¾ÃÂ¼ÃÂ¸ÃÂº", "Ã‘â€šÃ‘Æ’Ã‘â‚¬ÃÂ±ÃÂ°ÃÂ·"),
    "excursions": ("Ã‘ÂÃÂºÃ‘ÂÃÂºÃ‘Æ’Ã‘â‚¬Ã‘Â", "ÃÂ³ÃÂ¸ÃÂ´", "ÃÂ¼Ã‘Æ’ÃÂ·ÃÂµÃÂ¹", "Ã‘â€šÃ‘Æ’Ã‘â‚¬", "tripster", "sputnik", "wegotrip", "ÃÂ°Ã‘Æ’ÃÂ´ÃÂ¸ÃÂ¾ÃÂ³ÃÂ¸ÃÂ´"),
    "road": ("ÃÂ¿ÃÂ¾ÃÂµÃÂ·ÃÂ´", "ÃÂ°ÃÂ²Ã‘â€šÃÂ¾ÃÂ±Ã‘Æ’Ã‘Â", "ÃÂ´ÃÂ¾Ã‘â‚¬ÃÂ¾ÃÂ³", "ÃÂ¼ÃÂ°Ã‘â‚¬Ã‘Ë†Ã‘â‚¬Ã‘Æ’Ã‘â€š", "Ã‘ÂÃÂ»ÃÂµÃÂºÃ‘â€šÃ‘â‚¬ÃÂ¸Ã‘â€¡", "ÃÂ¶ÃÂ´", "ÃÂ¶/ÃÂ´", "tutu", "omio"),
    "car_rental": ("ÃÂ°Ã‘â‚¬ÃÂµÃÂ½ÃÂ´", "ÃÂ¼ÃÂ°Ã‘Ë†ÃÂ¸ÃÂ½", "ÃÂ°ÃÂ²Ã‘â€šÃÂ¾", "Ã‘â€šÃÂ°Ã‘â€¡ÃÂº", "ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š ÃÂ°ÃÂ²Ã‘â€šÃÂ¾", "car rent"),
    "bike_rental": ("ÃÂ¼ÃÂ¾Ã‘â€šÃÂ¾", "ÃÂ±ÃÂ°ÃÂ¹ÃÂº", "Ã‘ÂÃÂºÃ‘Æ’Ã‘â€šÃÂµÃ‘â‚¬", "ÃÂ¼ÃÂ¾ÃÂ¿ÃÂµÃÂ´", "ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š ÃÂ±ÃÂ°ÃÂ¹ÃÂºÃÂ°", "ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š ÃÂ¼ÃÂ¾Ã‘â€šÃÂ¾"),
    "transfers": ("Ã‘â€šÃ‘â‚¬ÃÂ°ÃÂ½Ã‘ÂÃ‘â€žÃÂµÃ‘â‚¬", "Ã‘â€šÃÂ°ÃÂºÃ‘ÂÃÂ¸", "ÃÂ¸ÃÂ· ÃÂ°Ã‘ÂÃ‘â‚¬ÃÂ¾ÃÂ¿ÃÂ¾Ã‘â‚¬Ã‘â€šÃÂ°", "ÃÂ² ÃÂ°Ã‘ÂÃ‘â‚¬ÃÂ¾ÃÂ¿ÃÂ¾Ã‘â‚¬Ã‘â€š"),
}

CATEGORY_TITLES: dict[str, str] = {
    "tickets": "Ãâ€˜ÃÂ¸ÃÂ»ÃÂµÃ‘â€šÃ‘â€¹ ÃÂ¸ ÃÂ¿ÃÂµÃ‘â‚¬ÃÂµÃÂ»Ã‘â€˜Ã‘â€š",
    "housing": "Ãâ€“ÃÂ¸ÃÂ»Ã‘Å’Ã‘â€˜ ÃÂ¸ Ã‘â‚¬ÃÂ°ÃÂ·ÃÂ¼ÃÂµÃ‘â€°ÃÂµÃÂ½ÃÂ¸ÃÂµ",
    "excursions": "ÃÂ­ÃÂºÃ‘ÂÃÂºÃ‘Æ’Ã‘â‚¬Ã‘ÂÃÂ¸ÃÂ¸ ÃÂ¸ ÃÂ°ÃÂºÃ‘â€šÃÂ¸ÃÂ²ÃÂ½ÃÂ¾Ã‘ÂÃ‘â€šÃÂ¸",
    "road": "Ãâ€ÃÂ¾Ã‘â‚¬ÃÂ¾ÃÂ³ÃÂ° ÃÂ¿ÃÂ¾ ÃÂ·ÃÂµÃÂ¼ÃÂ»ÃÂµ",
    "car_rental": "ÃÂÃ‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ° ÃÂ°ÃÂ²Ã‘â€šÃÂ¾",
    "bike_rental": "ÃÂÃ‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ° ÃÂ¼ÃÂ¾Ã‘â€šÃÂ¾ / ÃÂ±ÃÂ°ÃÂ¹ÃÂºÃÂ°",
    "transfers": "ÃÂ¢Ã‘â‚¬ÃÂ°ÃÂ½Ã‘ÂÃ‘â€žÃÂµÃ‘â‚¬Ã‘â€¹",
}

SOURCE_LABELS = {
    "Ã¢Å“Ë†Ã¯Â¸Â Ãâ€˜ÃÂ¸ÃÂ»ÃÂµÃ‘â€šÃ‘â€¹": "Ãâ€˜ÃÂ¸ÃÂ»ÃÂµÃ‘â€šÃ‘â€¹",
    "Ã°Å¸ÂÂ¨ ÃÅ¾Ã‘ÂÃ‘â€šÃ‘â‚¬ÃÂ¾ÃÂ²ÃÂ¾ÃÂº": "ÃÅ¾Ã‘ÂÃ‘â€šÃ‘â‚¬ÃÂ¾ÃÂ²ÃÂ¾ÃÂº",
    "Ã°Å¸ÂÂ  ÃÂ¡Ã‘Æ’Ã‘â€šÃÂ¾Ã‘â€¡ÃÂ½ÃÂ¾": "ÃÂ¡Ã‘Æ’Ã‘â€šÃÂ¾Ã‘â€¡ÃÂ½ÃÂ¾",
    "Ã°Å¸Â§Â³ ÃÂ¯ÃÂ½ÃÂ´ÃÂµÃÂºÃ‘Â ÃÅ¸Ã‘Æ’Ã‘â€šÃÂµÃ‘Ë†ÃÂµÃ‘ÂÃ‘â€šÃÂ²ÃÂ¸Ã‘Â": "ÃÂ¯ÃÂ½ÃÂ´ÃÂµÃÂºÃ‘Â ÃÅ¸Ã‘Æ’Ã‘â€šÃÂµÃ‘Ë†ÃÂµÃ‘ÂÃ‘â€šÃÂ²ÃÂ¸Ã‘Â",
    "Ã°Å¸ÂËœ Avito ÃÅ¸Ã‘Æ’Ã‘â€šÃÂµÃ‘Ë†ÃÂµÃ‘ÂÃ‘â€šÃÂ²ÃÂ¸Ã‘Â": "Avito ÃÅ¸Ã‘Æ’Ã‘â€šÃÂµÃ‘Ë†ÃÂµÃ‘ÂÃ‘â€šÃÂ²ÃÂ¸Ã‘Â",
    "Ã°Å¸Å’Â² ÃÅ“ÃÂ¸Ã‘â‚¬ ÃÂ¢Ã‘Æ’Ã‘â‚¬ÃÂ±ÃÂ°ÃÂ·": "ÃÅ“ÃÂ¸Ã‘â‚¬ ÃÂ¢Ã‘Æ’Ã‘â‚¬ÃÂ±ÃÂ°ÃÂ·",
    "Ã°Å¸ÂÂ¨ Booking.com": "Booking.com",
    "Ã°Å¸â€ºÅ½ Agoda": "Agoda",
    "Ã°Å¸Â§Â­ Tripadvisor Hotels": "Tripadvisor Hotels",
    "Ã°Å¸Å½Å¸ Tripster": "Tripster",
    "Ã°Å¸â€ºÂ° Sputnik8": "Sputnik8",
    "Ã°Å¸Å½Â§ WeGoTrip": "WeGoTrip",
    "Ã°Å¸Â¥Â¾ YouTravel": "YouTravel",
    "Ã°Å¸Å’Â GetYourGuide": "GetYourGuide",
    "Ã°Å¸Ââ€º Tiqets": "Tiqets",
    "Ã°Å¸Â§Â³ Viator": "Viator",
    "Ã°Å¸Å¡â€  Tutu": "Tutu",
    "Ã°Å¸â€”Âº ÃÅ“ÃÂ°Ã‘â‚¬Ã‘Ë†Ã‘â‚¬Ã‘Æ’Ã‘â€š": "ÃÅ“ÃÂ°Ã‘â‚¬Ã‘Ë†Ã‘â‚¬Ã‘Æ’Ã‘â€š",
    "Ã°Å¸Å¡â€ž Omio": "Omio",
    "Ã°Å¸Å’Â Rome2Rio": "Rome2Rio",
    "Ã°Å¸Å¡â€” Avito ÃÂ°Ã‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ° ÃÂ°ÃÂ²Ã‘â€šÃÂ¾": "Avito ÃÂ°Ã‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ° ÃÂ°ÃÂ²Ã‘â€šÃÂ¾",
    "Ã°Å¸Å¡Ëœ ÃÅ¡ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ° ÃÂ¸ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š": "ÃÅ¡ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ° ÃÂ¸ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š",
    "Ã°Å¸Å¡â€” Rentalcars": "Rentalcars",
    "Ã°Å¸Å¡â„¢ DiscoverCars": "DiscoverCars",
    "Ã°Å¸ÂÂ Avito ÃÂ¼ÃÂ¾Ã‘â€šÃÂ¾ / ÃÂ±ÃÂ°ÃÂ¹ÃÂº": "Avito ÃÂ¼ÃÂ¾Ã‘â€šÃÂ¾ / ÃÂ±ÃÂ°ÃÂ¹ÃÂº",
    "Ã°Å¸â€ºÂµ ÃÅ¡ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ° ÃÂ¸ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š": "ÃÅ¡ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ° ÃÂ¸ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š",
    "Ã°Å¸â€ºÂµ BikesBooking": "BikesBooking",
    "Ã°Å¸Å¡â€¢ ÃÂ¢Ã‘â‚¬ÃÂ°ÃÂ½Ã‘ÂÃ‘â€žÃÂµÃ‘â‚¬ / Ã‘â€šÃÂ°ÃÂºÃ‘ÂÃÂ¸": "ÃÂ¢Ã‘â‚¬ÃÂ°ÃÂ½Ã‘ÂÃ‘â€žÃÂµÃ‘â‚¬ / Ã‘â€šÃÂ°ÃÂºÃ‘ÂÃÂ¸",
    "Ã°Å¸Å¡Â Kiwitaxi": "Kiwitaxi",
}

CITY_DEEPLINKS = {
    "Ã‘ÂÃÂ°ÃÂ½ÃÂºÃ‘â€š-ÃÂ¿ÃÂµÃ‘â€šÃÂµÃ‘â‚¬ÃÂ±Ã‘Æ’Ã‘â‚¬ÃÂ³": {
        "ostrovok_path": "russia/st._petersburg",
        "ostrovok_q": "2042",
        "sutochno_host": "spb.sutochno.ru",
        "yandex_path": "saint-petersburg",
    },
    "Ã‘ÂÃÂ¾Ã‘â€¡ÃÂ¸": {
        "ostrovok_path": "russia/sochi",
        "sutochno_host": "sochi.sutochno.ru",
        "yandex_path": "sochi",
    },
    "ÃÂºÃÂ°ÃÂ·ÃÂ°ÃÂ½Ã‘Å’": {
        "ostrovok_path": "russia/kazan",
        "sutochno_host": "kazan.sutochno.ru",
        "yandex_path": "kazan",
    },
    "ÃÂºÃÂ°ÃÂ»ÃÂ¸ÃÂ½ÃÂ¸ÃÂ½ÃÂ³Ã‘â‚¬ÃÂ°ÃÂ´": {
        "ostrovok_path": "russia/kaliningrad",
        "sutochno_host": "kaliningrad.sutochno.ru",
        "yandex_path": "kaliningrad",
    },
    "ÃÂ²ÃÂ»ÃÂ°ÃÂ´ÃÂ¸ÃÂ²ÃÂ¾Ã‘ÂÃ‘â€šÃÂ¾ÃÂº": {
        "ostrovok_path": "russia/vladivostok",
        "sutochno_host": "vladivostok.sutochno.ru",
        "yandex_path": "vladivostok",
    },
    "Ã‘ÂÃ‘â€šÃÂ°ÃÂ¼ÃÂ±Ã‘Æ’ÃÂ»": {
        "ostrovok_path": "turkey/istanbul",
        "yandex_path": "istanbul",
    },
}

TRANSLIT_MAP = {
    "ÃÂ°": "a", "ÃÂ±": "b", "ÃÂ²": "v", "ÃÂ³": "g", "ÃÂ´": "d", "ÃÂµ": "e", "Ã‘â€˜": "e", "ÃÂ¶": "zh", "ÃÂ·": "z", "ÃÂ¸": "i",
    "ÃÂ¹": "y", "ÃÂº": "k", "ÃÂ»": "l", "ÃÂ¼": "m", "ÃÂ½": "n", "ÃÂ¾": "o", "ÃÂ¿": "p", "Ã‘â‚¬": "r", "Ã‘Â": "s", "Ã‘â€š": "t",
    "Ã‘Æ’": "u", "Ã‘â€ž": "f", "Ã‘â€¦": "h", "Ã‘â€ ": "ts", "Ã‘â€¡": "ch", "Ã‘Ë†": "sh", "Ã‘â€°": "sch", "Ã‘Å ": "", "Ã‘â€¹": "y", "Ã‘Å’": "",
    "Ã‘Â": "e", "Ã‘Å½": "yu", "Ã‘Â": "ya",
}


def detect_link_needs(context_text: str) -> set[str]:
    lowered = (context_text or "").lower()
    needs: set[str] = set()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            needs.add(category)
    return needs


def _transliterate_slug(value: str) -> str:
    slug_parts: list[str] = []
    for char in (value or "").lower():
        if char.isascii() and char.isalnum():
            slug_parts.append(char)
        elif char in TRANSLIT_MAP:
            slug_parts.append(TRANSLIT_MAP[char])
        elif char in {" ", "-", "_", "."}:
            slug_parts.append("-")
    slug = "".join(slug_parts)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def _format_ru_date(iso_date: str | None) -> str | None:
    if not iso_date:
        return None
    year, month, day = iso_date.split("-")
    return f"{day}.{month}.{year}"


def _city_deeplink_data(destination: str) -> dict[str, str]:
    normalized = normalized_search_value(destination)
    if not normalized:
        return {}
    lowered = normalized.lower()
    if lowered in CITY_DEEPLINKS:
        return CITY_DEEPLINKS[lowered]
    return {
        "ostrovok_path": f"russia/{_transliterate_slug(normalized)}",
        "yandex_path": _transliterate_slug(normalized),
    }


def _detect_housing_style(context_text: str, group_size: int, source: str) -> str:
    lowered = (context_text or "").lower()
    if any(keyword in lowered for keyword in ("ÃÂ¿ÃÂµÃÂ½Ã‘â€šÃ‘â€¦ÃÂ°Ã‘Æ’Ã‘Â", "ÃÂ²ÃÂ¸ÃÂ»ÃÂ»ÃÂ°", "ÃÂ»Ã‘Å½ÃÂºÃ‘Â")):
        return "ÃÂ¿ÃÂµÃÂ½Ã‘â€šÃ‘â€¦ÃÂ°Ã‘Æ’Ã‘Â / ÃÂ²ÃÂ¸ÃÂ»ÃÂ»ÃÂ°"
    if any(keyword in lowered for keyword in ("ÃÂ´ÃÂ¾ÃÂ¼", "ÃÂºÃÂ¾Ã‘â€šÃ‘â€šÃÂµÃÂ´ÃÂ¶", "Ã‘â€šÃÂ°Ã‘Æ’ÃÂ½Ã‘â€¦ÃÂ°Ã‘Æ’Ã‘Â")):
        return "ÃÂ´ÃÂ¾ÃÂ¼ / ÃÂºÃÂ¾Ã‘â€šÃ‘â€šÃÂµÃÂ´ÃÂ¶"
    if any(keyword in lowered for keyword in ("ÃÂºÃÂ²ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ¸Ã‘â‚¬ÃÂ°", "ÃÂ°ÃÂ¿ÃÂ°Ã‘â‚¬Ã‘â€š", "ÃÂ°ÃÂ¿ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ°ÃÂ¼ÃÂµÃÂ½Ã‘â€š", "Ã‘ÂÃ‘â€šÃ‘Æ’ÃÂ´ÃÂ¸Ã‘Â")):
        return "ÃÂºÃÂ²ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ¸Ã‘â‚¬ÃÂ° / ÃÂ°ÃÂ¿ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ°ÃÂ¼ÃÂµÃÂ½Ã‘â€šÃ‘â€¹"
    if any(keyword in lowered for keyword in ("Ã‘â€¦ÃÂ¾Ã‘ÂÃ‘â€šÃÂµÃÂ»", "ÃÂºÃÂ¾ÃÂ¹ÃÂºÃÂ¾", "ÃÂºÃÂ¾ÃÂ¼ÃÂ½ÃÂ°Ã‘â€šÃÂ°")):
        return "Ã‘â€¦ÃÂ¾Ã‘ÂÃ‘â€šÃÂµÃÂ» / ÃÂºÃÂ¾ÃÂ¼ÃÂ½ÃÂ°Ã‘â€šÃÂ°"
    if source == "ÃÂ¡Ã‘Æ’Ã‘â€šÃÂ¾Ã‘â€¡ÃÂ½ÃÂ¾":
        return "ÃÂºÃÂ²ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ¸Ã‘â‚¬ÃÂ° / ÃÂ´ÃÂ¾ÃÂ¼"
    if group_size >= 4:
        return "ÃÂºÃÂ²ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ¸Ã‘â‚¬ÃÂ° / ÃÂ´ÃÂ¾ÃÂ¼"
    return "ÃÂ¾Ã‘â€šÃÂµÃÂ»Ã‘Å’ / Ã‘ÂÃ‘â€šÃ‘Æ’ÃÂ´ÃÂ¸Ã‘Â"


def _estimate_housing_result(
    destination: str,
    source: str,
    group_size: int,
    budget_text: str,
    context_text: str,
) -> tuple[str, str, int]:
    locale = detect_route_locale(destination)
    currency = default_currency_for_country(locale.destination_country)
    style = _detect_housing_style(context_text, group_size, source)
    lowered_budget = (budget_text or "").lower()

    if currency == "RUB":
        base = 4300
        if source == "ÃÅ¾Ã‘ÂÃ‘â€šÃ‘â‚¬ÃÂ¾ÃÂ²ÃÂ¾ÃÂº":
            base = 4900
        elif source == "ÃÂ¡Ã‘Æ’Ã‘â€šÃÂ¾Ã‘â€¡ÃÂ½ÃÂ¾":
            base = 5200 if group_size >= 3 else 4500
        elif source == "ÃÂ¯ÃÂ½ÃÂ´ÃÂµÃÂºÃ‘Â ÃÅ¸Ã‘Æ’Ã‘â€šÃÂµÃ‘Ë†ÃÂµÃ‘ÂÃ‘â€šÃÂ²ÃÂ¸Ã‘Â":
            base = 5000
        if any(token in lowered_budget for token in ("Ã‘ÂÃÂºÃÂ¾ÃÂ½ÃÂ¾ÃÂ¼", "ÃÂ´ÃÂ¾ ", "ÃÂ¿ÃÂ¾ÃÂ´ÃÂµÃ‘Ë†ÃÂµÃÂ²ÃÂ»ÃÂµ", "ÃÂ½ÃÂµÃÂ´ÃÂ¾Ã‘â‚¬ÃÂ¾ÃÂ³ÃÂ¾")):
            base -= 900
        elif any(token in lowered_budget for token in ("ÃÂ¿ÃÂµÃ‘â‚¬ÃÂ²Ã‘â€¹ÃÂ¹ ÃÂºÃÂ»ÃÂ°Ã‘ÂÃ‘Â", "ÃÂ½ÃÂµ ÃÂ¾ÃÂ³Ã‘â‚¬ÃÂ°ÃÂ½ÃÂ¸Ã‘â€¡ÃÂµÃÂ½", "ÃÂ»Ã‘Å½ÃÂºÃ‘Â", "ÃÂ¿Ã‘â‚¬ÃÂµÃÂ¼ÃÂ¸Ã‘Æ’ÃÂ¼")):
            base += 3400
        elif any(token in lowered_budget for token in ("ÃÂ±ÃÂ¸ÃÂ·ÃÂ½ÃÂµÃ‘Â", "ÃÂºÃÂ¾ÃÂ¼Ã‘â€žÃÂ¾Ã‘â‚¬Ã‘â€š", "Ã‘ÂÃ‘â‚¬ÃÂµÃÂ´ÃÂ½")):
            base += 1200
        if "ÃÂ¿ÃÂµÃÂ½Ã‘â€šÃ‘â€¦ÃÂ°Ã‘Æ’Ã‘Â" in style or "ÃÂ²ÃÂ¸ÃÂ»ÃÂ»ÃÂ°" in style:
            base += 5000
        elif "ÃÂ´ÃÂ¾ÃÂ¼" in style:
            base += 1800
        elif "Ã‘â€¦ÃÂ¾Ã‘ÂÃ‘â€šÃÂµÃÂ»" in style:
            base -= 1400
        score = 9 if source in {"ÃÅ¾Ã‘ÂÃ‘â€šÃ‘â‚¬ÃÂ¾ÃÂ²ÃÂ¾ÃÂº", "ÃÂ¡Ã‘Æ’Ã‘â€šÃÂ¾Ã‘â€¡ÃÂ½ÃÂ¾"} else 8
        return f"ÃÂ¾Ã‘â€š {base:,} Ã¢â€šÂ½/ÃÂ½ÃÂ¾Ã‘â€¡Ã‘Å’".replace(",", " "), style, score

    if currency == "EUR":
        base = 82 if source == "Agoda" else 95
        score = 8
        return f"from {base} EUR/night", style, score

    if currency == "USD":
        score = 8
        return "from 110 USD/night", style, score

    return "ÃÂ²ÃÂ°Ã‘â‚¬ÃÂ¸ÃÂ°ÃÂ½Ã‘â€šÃ‘â€¹ ÃÂ¿ÃÂ¾ Ã‘ÂÃ‘ÂÃ‘â€¹ÃÂ»ÃÂºÃÂµ", style, 8


def _ticket_links(destination: str, origin: str, start_date: str | None, end_date: str | None) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    normalized_origin = normalized_search_value(origin)
    if not normalized_destination:
        return []

    if start_date and end_date:
        aviasales = (
            "https://www.aviasales.ru/search?"
            + urllib.parse.urlencode(
                {
                    "origin": normalized_origin or "",
                    "destination": normalized_destination,
                    "depart_date": start_date,
                    "return_date": end_date,
                }
            )
        )
    elif normalized_origin:
        aviasales = (
            "https://www.aviasales.ru/search?"
            + urllib.parse.urlencode({"origin": normalized_origin, "destination": normalized_destination})
        )
    else:
        aviasales = "https://www.aviasales.ru"
    return [("Ã¢Å“Ë†Ã¯Â¸Â Ãâ€˜ÃÂ¸ÃÂ»ÃÂµÃ‘â€šÃ‘â€¹", aviasales)]


def _housing_links(destination: str, start_date: str | None, end_date: str | None, group_size: int = 2) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []

    locale = detect_route_locale(normalized_destination)
    ru_cis = locale.is_ru_cis_destination
    if ru_cis:
        deeplink = _city_deeplink_data(normalized_destination)
        ostrovok_path = deeplink.get("ostrovok_path")
        ostrovok_q = deeplink.get("ostrovok_q")
        sutochno_host = deeplink.get("sutochno_host")
        yandex_path = deeplink.get("yandex_path")
        checkin_ru = _format_ru_date(start_date)
        checkout_ru = _format_ru_date(end_date)
        if start_date and end_date:
            ostrovok_params = {
                "dates": f"{checkin_ru}-{checkout_ru}",
                "guests": str(max(1, group_size)),
                "search": "yes",
            }
            if ostrovok_q:
                ostrovok_params["q"] = ostrovok_q
            return [
                (
                    "Ã°Å¸ÂÂ¨ ÃÅ¾Ã‘ÂÃ‘â€šÃ‘â‚¬ÃÂ¾ÃÂ²ÃÂ¾ÃÂº",
                    f"https://ostrovok.ru/hotel/{ostrovok_path}?" + urllib.parse.urlencode(ostrovok_params),
                ),
                (
                    "Ã°Å¸Â§Â³ ÃÂ¯ÃÂ½ÃÂ´ÃÂµÃÂºÃ‘Â ÃÅ¸Ã‘Æ’Ã‘â€šÃÂµÃ‘Ë†ÃÂµÃ‘ÂÃ‘â€šÃÂ²ÃÂ¸Ã‘Â",
                    f"https://travel.yandex.ru/hotels/{yandex_path}/?"
                    + urllib.parse.urlencode({"checkinDate": start_date, "checkoutDate": end_date, "adults": max(1, group_size)}),
                ),
            ] + (
                [
                    (
                        "Ã°Å¸ÂÂ  ÃÂ¡Ã‘Æ’Ã‘â€šÃÂ¾Ã‘â€¡ÃÂ½ÃÂ¾",
                        f"https://{sutochno_host}/?{urllib.parse.urlencode({'arrival': start_date, 'departure': end_date, 'guests': max(1, group_size)})}",
                    )
                ]
                if sutochno_host
                else []
            )

        results = [
            ("🏨 Островок", f"https://ostrovok.ru/hotel/{ostrovok_path}/" + (("?" + urllib.parse.urlencode({"q": ostrovok_q})) if ostrovok_q else "")),
            ("🧳 Яндекс Путешествия", f"https://travel.yandex.ru/hotels/{yandex_path}/"),
            ("🏘 Avito Путешествия", "https://www.avito.ru/rossiya/kvartiry/sdam/posutochno"),
            ("🌲 Мир Турбаз", "https://mirturbaz.ru/catalog/russia"),
        ]
        if sutochno_host:
            results.insert(1, ("🏠 Суточно", f"https://{sutochno_host}/"))
        return results

    booking_params = {"ss": normalized_destination}
    if start_date and end_date:
        booking_params.update({"checkin": start_date, "checkout": end_date})
    return [
        ("Ã°Å¸ÂÂ¨ Booking.com", "https://www.booking.com/searchresults.html?" + urllib.parse.urlencode(booking_params)),
        ("Ã°Å¸â€ºÅ½ Agoda", "https://www.agoda.com/search?" + urllib.parse.urlencode({"city": normalized_destination})),
        ("Ã°Å¸Â§Â­ Tripadvisor Hotels", "https://www.tripadvisor.com/Search?" + urllib.parse.urlencode({"q": f"hotels {normalized_destination}"})),
    ]


def _excursion_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [
            ("Ã°Å¸Å½Å¸ Tripster", f"https://experience.tripster.ru/search/?query={encoded_destination}"),
            ("Ã°Å¸â€ºÂ° Sputnik8", f"https://sputnik8.com/ru/search?query={encoded_destination}"),
            ("Ã°Å¸Å½Â§ WeGoTrip", f"https://wegotrip.com/search/?query={encoded_destination}"),
            ("Ã°Å¸Â¥Â¾ YouTravel", f"https://youtravel.me/search?query={encoded_destination}"),
        ]
    return [
        ("Ã°Å¸Å’Â GetYourGuide", f"https://www.getyourguide.com/s/?q={encoded_destination}"),
        ("Ã°Å¸Ââ€º Tiqets", f"https://www.tiqets.com/en/search?query={encoded_destination}"),
        ("Ã°Å¸Â§Â³ Viator", f"https://www.viator.com/searchResults/all?text={encoded_destination}"),
    ]


def _road_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [
            ("Ã°Å¸Å¡â€  Tutu", f"https://www.tutu.ru/poezda/order/?to={encoded_destination}"),
            ("Ã°Å¸â€”Âº ÃÅ“ÃÂ°Ã‘â‚¬Ã‘Ë†Ã‘â‚¬Ã‘Æ’Ã‘â€š", f"https://yandex.ru/maps/?text={encoded_destination}"),
        ]
    return [
        ("Ã°Å¸Å¡â€ž Omio", f"https://www.omio.com/search-frontend/results?destination={encoded_destination}"),
        ("Ã°Å¸Å’Â Rome2Rio", f"https://www.rome2rio.com/s/{encoded_destination}"),
        ("Ã°Å¸â€”Âº ÃÅ“ÃÂ°Ã‘â‚¬Ã‘Ë†Ã‘â‚¬Ã‘Æ’Ã‘â€š", f"https://www.google.com/maps/search/{encoded_destination}"),
    ]


def _car_rental_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [
            ("Ã°Å¸Å¡â€” Avito ÃÂ°Ã‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ° ÃÂ°ÃÂ²Ã‘â€šÃÂ¾", f"https://www.avito.ru/rossiya?q=ÃÂ°Ã‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ°+ÃÂ°ÃÂ²Ã‘â€šÃÂ¾+{encoded_destination}"),
            ("Ã°Å¸Å¡Ëœ ÃÅ¡ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ° ÃÂ¸ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š", f"https://yandex.ru/maps/?text=ÃÂ°Ã‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ°+ÃÂ°ÃÂ²Ã‘â€šÃÂ¾+{encoded_destination}"),
        ]
    return [
        ("Ã°Å¸Å¡â€” Rentalcars", f"https://www.rentalcars.com/SearchResults.do?dropLocation={encoded_destination}"),
        ("Ã°Å¸Å¡â„¢ DiscoverCars", f"https://www.discovercars.com/?q={encoded_destination}"),
        ("Ã°Å¸Å¡Ëœ ÃÅ¡ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ° ÃÂ¸ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š", f"https://www.google.com/maps/search/car+rental+{encoded_destination}"),
    ]


def _bike_rental_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [
            ("Ã°Å¸ÂÂ Avito ÃÂ¼ÃÂ¾Ã‘â€šÃÂ¾ / ÃÂ±ÃÂ°ÃÂ¹ÃÂº", f"https://www.avito.ru/rossiya?q=ÃÂ°Ã‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ°+ÃÂ¼ÃÂ¾Ã‘â€šÃÂ¾+{encoded_destination}"),
            ("Ã°Å¸â€ºÂµ ÃÅ¡ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ° ÃÂ¸ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š", f"https://yandex.ru/maps/?text=ÃÂ°Ã‘â‚¬ÃÂµÃÂ½ÃÂ´ÃÂ°+ÃÂ±ÃÂ°ÃÂ¹ÃÂºÃÂ°+{encoded_destination}"),
        ]
    return [
        ("Ã°Å¸â€ºÂµ BikesBooking", f"https://bikesbooking.com/en/search?query={encoded_destination}"),
        ("Ã°Å¸â€ºÂµ ÃÅ¡ÃÂ°Ã‘â‚¬Ã‘â€šÃÂ° ÃÂ¸ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂºÃÂ°Ã‘â€š", f"https://www.google.com/maps/search/bike+rental+{encoded_destination}"),
    ]


def _transfer_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [("Ã°Å¸Å¡â€¢ ÃÂ¢Ã‘â‚¬ÃÂ°ÃÂ½Ã‘ÂÃ‘â€žÃÂµÃ‘â‚¬ / Ã‘â€šÃÂ°ÃÂºÃ‘ÂÃÂ¸", f"https://yandex.ru/maps/?text=Ã‘â€šÃ‘â‚¬ÃÂ°ÃÂ½Ã‘ÂÃ‘â€žÃÂµÃ‘â‚¬+{encoded_destination}")]
    return [
        ("Ã°Å¸Å¡Â Kiwitaxi", f"https://kiwitaxi.com/en/search?query={encoded_destination}"),
        ("Ã°Å¸Å¡â€¢ ÃÂ¢Ã‘â‚¬ÃÂ°ÃÂ½Ã‘ÂÃ‘â€žÃÂµÃ‘â‚¬ / Ã‘â€šÃÂ°ÃÂºÃ‘ÂÃÂ¸", f"https://www.google.com/maps/search/airport+transfer+{encoded_destination}"),
    ]


def build_links_map(
    destination: str,
    dates_text: str,
    origin: str | None = None,
    *,
    group_size: int = 2,
    context_text: str = "",
) -> dict[str, str]:
    normalized_destination = normalized_search_value(destination)
    normalized_origin = normalized_search_value(origin) or ""
    if not normalized_destination:
        return {}

    date_range = _parse_dates_range(dates_text)
    start_date = date_range[0].isoformat() if date_range else None
    end_date = date_range[1].isoformat() if date_range else None
    needs = detect_link_needs(context_text)

    link_items: list[tuple[str, str]] = []
    if "tickets" in needs:
        link_items.extend(_ticket_links(normalized_destination, normalized_origin, start_date, end_date))
    if "housing" in needs:
        link_items.extend(_housing_links(normalized_destination, start_date, end_date, group_size))
    if "excursions" in needs:
        link_items.extend(_excursion_links(normalized_destination))
    if "road" in needs:
        link_items.extend(_road_links(normalized_destination))
    if "car_rental" in needs:
        link_items.extend(_car_rental_links(normalized_destination))
    if "bike_rental" in needs:
        link_items.extend(_bike_rental_links(normalized_destination))
    if "transfers" in needs:
        link_items.extend(_transfer_links(normalized_destination))

    if not link_items:
        link_items = _ticket_links(normalized_destination, normalized_origin, start_date, end_date) + _housing_links(
            normalized_destination, start_date, end_date, group_size
        )[:2]

    return dict(link_items)


def build_structured_link_results(
    destination: str,
    dates_text: str,
    origin: str | None = None,
    *,
    group_size: int = 2,
    context_text: str = "",
    budget_text: str = "",
) -> dict[str, list[TravelSearchResult]]:
    normalized_destination = normalized_search_value(destination)
    normalized_origin = normalized_search_value(origin) or ""
    if not normalized_destination:
        return {}

    date_range = _parse_dates_range(dates_text)
    start_date = date_range[0].isoformat() if date_range else None
    end_date = date_range[1].isoformat() if date_range else None
    needs = detect_link_needs(context_text)
    if not needs:
        needs = {"tickets", "housing"}

    category_links: dict[str, list[tuple[str, str]]] = {}
    if "tickets" in needs:
        ticket_links = _ticket_links(normalized_destination, normalized_origin, start_date, end_date)
        if ticket_links:
            category_links["tickets"] = ticket_links
    if "housing" in needs:
        housing_links = _housing_links(normalized_destination, start_date, end_date, group_size)
        if housing_links:
            category_links["housing"] = housing_links
    if "excursions" in needs:
        excursion_links = _excursion_links(normalized_destination)
        if excursion_links:
            category_links["excursions"] = excursion_links
    if "road" in needs:
        road_links = _road_links(normalized_destination)
        if road_links:
            category_links["road"] = road_links
    if "car_rental" in needs:
        car_links = _car_rental_links(normalized_destination)
        if car_links:
            category_links["car_rental"] = car_links
    if "bike_rental" in needs:
        bike_links = _bike_rental_links(normalized_destination)
        if bike_links:
            category_links["bike_rental"] = bike_links
    if "transfers" in needs:
        transfer_links = _transfer_links(normalized_destination)
        if transfer_links:
            category_links["transfers"] = transfer_links

    structured: dict[str, list[TravelSearchResult]] = {}
    for category, items in category_links.items():
        results: list[TravelSearchResult] = []
        for index, (label, url) in enumerate(items):
            source = SOURCE_LABELS.get(label, label)
            if category == "housing":
                price_text, style, score = _estimate_housing_result(
                    normalized_destination,
                    source,
                    group_size,
                    budget_text,
                    context_text,
                )
                results.append(
                    TravelSearchResult(
                        title=f"{CATEGORY_TITLES.get(category, category)}: {source}",
                        price_text=price_text,
                        url=url,
                        source=source,
                        score=score,
                        dates=dates_text or "",
                        note=f"{style}, Ð¾Ñ†ÐµÐ½ÐºÐ° {score}/10",
                    )
                )
            else:
                score = max(7, 9 - index)
                results.append(
                    TravelSearchResult(
                        title=f"{CATEGORY_TITLES.get(category, category)}: {source}",
                        price_text="ÐžÑ‚ÐºÑ€Ñ‹Ñ‚ÑŒ Ð²Ð°Ñ€Ð¸Ð°Ð½Ñ‚Ñ‹ Ð¿Ð¾ ÑÑÑ‹Ð»ÐºÐµ",
                        url=url,
                        source=source,
                        score=score,
                        dates=dates_text or "",
                        note=f"Ð¾Ñ†ÐµÐ½ÐºÐ° {score}/10",
                    )
                )
        structured[category] = trim_results(results)
    return structured



def build_links_text(
    destination: str,
    dates_text: str,
    origin: str | None = None,
    *,
    group_size: int = 2,
    context_text: str = "",
) -> str:
    links = build_links_map(destination, dates_text, origin, group_size=group_size, context_text=context_text)
    if not links:
        return ""

    lines = [f"{label}: {url}" for label, url in links.items()]
    lines.append(
        "Ãâ€˜ÃÂ¾Ã‘â€š ÃÂ¿ÃÂ¾ÃÂºÃÂ°ÃÂ·Ã‘â€¹ÃÂ²ÃÂ°ÃÂµÃ‘â€š Ã‘â€šÃÂ¾ÃÂ»Ã‘Å’ÃÂºÃÂ¾ Ã‘â€šÃÂµ ÃÂ¿ÃÂ¾ÃÂ¸Ã‘ÂÃÂºÃÂ¾ÃÂ²Ã‘â€¹ÃÂµ Ã‘ÂÃ‘â€ ÃÂµÃÂ½ÃÂ°Ã‘â‚¬ÃÂ¸ÃÂ¸, ÃÂºÃÂ¾Ã‘â€šÃÂ¾Ã‘â‚¬Ã‘â€¹ÃÂµ ÃÂ¿Ã‘â‚¬ÃÂ¾ÃÂ·ÃÂ²Ã‘Æ’Ã‘â€¡ÃÂ°ÃÂ»ÃÂ¸ ÃÂ² ÃÂ¿ÃÂµÃ‘â‚¬ÃÂµÃÂ¿ÃÂ¸Ã‘ÂÃÂºÃÂµ. "
        "Live-Ã‘â€ ÃÂµÃÂ½Ã‘â€¹ ÃÂ¿ÃÂ¾ ÃÂ±ÃÂ¸ÃÂ»ÃÂµÃ‘â€šÃÂ°ÃÂ¼ ÃÂ´ÃÂ¾Ã‘ÂÃ‘â€šÃ‘Æ’ÃÂ¿ÃÂ½Ã‘â€¹ Ã‘â€¡ÃÂµÃ‘â‚¬ÃÂµÃÂ· Travelpayouts."
    )
    return "\n".join(lines)
