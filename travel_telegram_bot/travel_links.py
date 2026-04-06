from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import date

from config import HTTP_IATA_MAX_RETRIES, HTTP_IATA_TIMEOUT
from date_utils import is_one_way_trip_text, resolve_trip_dates
from http_utils import safe_http_get
from travel_locale import detect_route_locale
from travel_result_models import TravelSearchResult, trim_results
from value_normalization import normalized_search_value

logger = logging.getLogger(__name__)


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tickets": (
        "билет",
        "авиа",
        "самолет",
        "самолёт",
        "летим",
        "перелет",
        "перелёт",
        "рейс",
        "вылет",
        "прилет",
        "туда-обратно",
        "туда обратно",
        "обратно",
        "в одну сторону",
        "без обратного",
        "flight",
        "ticket",
    ),
    "housing": ("отел", "гостини", "жиль", "апарт", "квартир", "суточно", "ночев", "домик", "турбаз", "hotel", "apartment", "stay"),
    "excursions": ("экскурс", "гид", "музей", "тур", "tripster", "sputnik", "wegotrip", "аудиогид", "activity", "excursion"),
    "road": ("поезд", "автобус", "дорог", "маршрут", "электрич", "жд", "ж/д", "tutu", "omio", "rome2rio", "train", "bus"),
    "car_rental": ("аренд", "машин", "авто", "тачк", "прокат авто", "car rent", "car rental"),
    "bike_rental": ("мото", "байк", "скутер", "мопед", "прокат байка", "прокат мото", "bike rental", "scooter"),
    "transfers": ("трансфер", "такси", "из аэропорта", "в аэропорт", "transfer", "airport taxi"),
}

CATEGORY_TITLES: dict[str, str] = {
    "tickets": "Билеты и перелет",
    "housing": "Жильё и размещение",
    "excursions": "Экскурсии и активности",
    "road": "Дорога по земле",
    "car_rental": "Аренда авто",
    "bike_rental": "Аренда мото / байка",
    "transfers": "Трансферы",
}

RU_CIS_CITY_SLUGS: dict[str, dict[str, str]] = {
    "санкт-петербург": {
        "ostrovok_path": "russia/st._petersburg",
        "ostrovok_q": "2042",
        "sutochno_host": "spb.sutochno.ru",
        "yandex_path": "saint-petersburg",
    },
    "сочи": {
        "ostrovok_path": "russia/sochi",
        "sutochno_host": "sochi.sutochno.ru",
        "yandex_path": "sochi",
    },
    "казань": {
        "ostrovok_path": "russia/kazan",
        "sutochno_host": "kazan.sutochno.ru",
        "yandex_path": "kazan",
    },
    "калининград": {
        "ostrovok_path": "russia/kaliningrad",
        "sutochno_host": "kaliningrad.sutochno.ru",
        "yandex_path": "kaliningrad",
    },
    "владивосток": {
        "ostrovok_path": "russia/vladivostok",
        "sutochno_host": "vladivostok.sutochno.ru",
        "yandex_path": "vladivostok",
    },
    "стамбул": {
        "ostrovok_path": "turkey/istanbul",
        "yandex_path": "istanbul",
    },
}

TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
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


RU_MONTHS: dict[str, int] = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

def _parse_ru_month_date(text: str) -> date | None:
    """Parse '12 июня' or '12 июня 2026' into a date."""
    m = re.match(r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+(\d{4}))?", text, re.IGNORECASE)
    if not m:
        return None
    day = int(m.group(1))
    month = RU_MONTHS.get(m.group(2).lower())
    year = int(m.group(3)) if m.group(3) else date.today().year
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_date_range(dates_text: str | None) -> tuple[str | None, str | None]:
    text = (dates_text or "").strip()
    if not text or text.lower() in {"не указаны", "не указано", "-"}:
        return None, None

    # 12.06 - 16.06 / 12.06.2026 по 16.06.2026
    numeric_range = re.search(
        r"\b(?:с\s*)?(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\s*(?:по|до|-|–|—)\s*(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if numeric_range:
        return _normalize_numeric_date(numeric_range.group(1)), _normalize_numeric_date(numeric_range.group(2))

    # "12 июня - 16 июня" / "12 июня по 16 июня" / "с 12 июня до 16 июня"
    ru_range = re.search(
        r"(?:с\s+)?(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+\d{4})?)\s*(?:по|до|-|–|—)\s*(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+\d{4})?)",
        text,
        flags=re.IGNORECASE,
    )
    if ru_range:
        d1 = _parse_ru_month_date(ru_range.group(1).strip())
        d2 = _parse_ru_month_date(ru_range.group(2).strip())
        if d1 and d2:
            return d1.isoformat(), d2.isoformat()

    # Single "12 июня"
    ru_single = re.search(
        r"\b(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+\d{4})?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if ru_single:
        d = _parse_ru_month_date(ru_single.group(1).strip())
        if d:
            return d.isoformat(), d.isoformat()

    numeric_single = re.search(r"\b(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\b", text)
    if numeric_single:
        normalized = _normalize_numeric_date(numeric_single.group(1))
        return normalized, normalized

    return None, None


def _normalize_numeric_date(value: str) -> str | None:
    parts = re.split(r"[./]", value)
    if len(parts) < 2:
        return None
    day = int(parts[0])
    month = int(parts[1])
    year = int(parts[2]) if len(parts) >= 3 else date.today().year
    if year < 100:
        year += 2000
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    return parsed.isoformat()


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
    if lowered in RU_CIS_CITY_SLUGS:
        return RU_CIS_CITY_SLUGS[lowered]
    slug = _transliterate_slug(normalized)
    return {
        "ostrovok_path": f"russia/{slug}",
        "yandex_path": slug,
    }


def _is_ru_cis_destination(destination: str, origin: str | None = None) -> bool:
    normalized = normalized_search_value(destination)
    if not normalized:
        return False
    if normalized.lower() in RU_CIS_CITY_SLUGS:
        return True
    try:
        locale = detect_route_locale(normalized, origin)
    except Exception:
        logger.debug("Route locale detection failed for %r (origin=%r)", normalized, origin)
        return False
    return bool(getattr(locale, "is_ru_cis_destination", False))


def _currency_for_destination(destination: str) -> str:
    normalized = (normalized_search_value(destination) or "").lower()
    if normalized in RU_CIS_CITY_SLUGS:
        return "RUB"
    if normalized in {"париж", "берлин", "рим", "барселона", "amsterdam", "paris", "berlin", "rome"}:
        return "EUR"
    if normalized in {"стамбул", "istanbul"}:
        return "TRY"
    return "LOCAL"


def _normalize_budget_level(budget_text: str) -> str:
    lowered = (budget_text or "").lower()
    if any(phrase in lowered for phrase in ("первый класс", "first class", "не ограничен", "без ограничений", "люкс", "премиум")):
        return "первый класс"
    if any(phrase in lowered for phrase in ("бизнес", "business", "комфорт", "хороший отель")):
        return "бизнес"
    return "эконом"


def _ticket_links(destination: str, origin: str | None, start_date: str | None, end_date: str | None) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    normalized_origin = normalized_search_value(origin)
    if not normalized_destination:
        return []

    origin_code = _resolve_iata_code(normalized_origin) if normalized_origin else ""
    destination_code = _resolve_iata_code(normalized_destination)
    if origin_code and destination_code:
        params: dict[str, str | int] = {
            "origin_iata": origin_code,
            "destination_iata": destination_code,
            "adults": 1,
        }
        if start_date:
            params["depart_date"] = start_date
        if end_date and end_date != start_date:
            params["return_date"] = end_date
        else:
            params["one_way"] = 1
        url = "https://www.aviasales.ru/search?" + urllib.parse.urlencode(params)
    else:
        url = "https://www.aviasales.ru/"
    return [("✈️ Aviasales", url)]


def _resolve_iata_code(term: str | None) -> str:
    normalized = normalized_search_value(term)
    if not normalized:
        return ""
    params = [
        ("term", normalized),
        ("locale", "ru"),
        ("types[]", "city"),
        ("types[]", "airport"),
    ]
    url = "https://autocomplete.travelpayouts.com/places2?" + urllib.parse.urlencode(params)
    try:
        raw = safe_http_get(url, max_retries=HTTP_IATA_MAX_RETRIES, timeout=HTTP_IATA_TIMEOUT)
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        logger.debug("IATA lookup failed for %r", normalized)
        return ""
    if not isinstance(payload, list):
        return ""
    for item in payload:
        code = str(item.get("code") or "").strip().upper()
        if code:
            return code
    return ""


def _housing_links(
    destination: str,
    start_date: str | None,
    end_date: str | None,
    group_size: int = 2,
    budget_text: str = "",
    context_text: str = "",
) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    budget_level = _normalize_budget_level(budget_text)
    context_lowered = (context_text or "").lower()
    wants_house = any(keyword in context_lowered for keyword in ("дом", "вилла", "коттедж", "house", "villa", "cottage"))
    wants_apartment = any(
        keyword in context_lowered for keyword in ("квартир", "апарт", "апартамент", "студи", "flat", "apartment")
    )
    wants_home_style = wants_house or wants_apartment

    encoded_destination = urllib.parse.quote_plus(normalized_destination)
    if _is_ru_cis_destination(normalized_destination):
        deeplink = _city_deeplink_data(normalized_destination)
        ostrovok_path = deeplink.get("ostrovok_path")
        ostrovok_q = deeplink.get("ostrovok_q")
        sutochno_host = deeplink.get("sutochno_host")
        yandex_path = deeplink.get("yandex_path")
        checkin_ru = _format_ru_date(start_date)
        checkout_ru = _format_ru_date(end_date)

        ostrovok_params: dict[str, str] = {}
        if ostrovok_q:
            ostrovok_params["q"] = ostrovok_q
        ostrovok_params["guests"] = str(max(1, group_size))
        if checkin_ru and checkout_ru:
            ostrovok_params["dates"] = f"{checkin_ru}-{checkout_ru}"
            ostrovok_params["search"] = "yes"
        ostrovok_url = f"https://ostrovok.ru/hotel/{ostrovok_path}/"
        if ostrovok_params:
            ostrovok_url += "?" + urllib.parse.urlencode(ostrovok_params)

        # Sutochno: correct URL format with term, guests_adults, occupied
        occupied_val = f"{start_date};{end_date}" if start_date and end_date else None
        sutochno_params = {
            "term": normalized_destination,
            "guests_adults": str(max(1, group_size)),
        }
        if occupied_val:
            sutochno_params["occupied"] = occupied_val
        sutochno_url = "https://sutochno.ru/front/searchapp/search?" + urllib.parse.urlencode(sutochno_params)

        yandex_params: dict[str, str] = {"adults": str(max(1, group_size))}
        if start_date:
            yandex_params["checkinDate"] = start_date
        if end_date:
            yandex_params["checkoutDate"] = end_date
        yandex_base = f"https://travel.yandex.ru/hotels/{yandex_path or _transliterate_slug(normalized_destination)}/"
        yandex_url = yandex_base + "?" + urllib.parse.urlencode(yandex_params)
        airbnb_url = "https://www.airbnb.ru/s/" + urllib.parse.quote(normalized_destination) + "/homes"
        if start_date:
            airbnb_params: dict[str, str] = {"checkin": start_date, "adults": str(max(1, group_size))}
            if end_date:
                airbnb_params["checkout"] = end_date
            airbnb_url += "?" + urllib.parse.urlencode(airbnb_params)

        if wants_home_style:
            return [
                ("🏠 Суточно", sutochno_url),
                ("🧳 Яндекс Путешествия", yandex_url),
                ("🏨 Островок", ostrovok_url),
            ]
        if budget_level == "первый класс":
            return [
                ("🧳 Яндекс Путешествия", yandex_url),
                ("🏨 Островок", ostrovok_url),
                ("🏠 Суточно", sutochno_url),
            ]
        if budget_level == "бизнес":
            return [
                ("🏨 Островок", ostrovok_url),
                ("🧳 Яндекс Путешествия", yandex_url),
                ("🏠 Суточно", sutochno_url),
            ]
        return [
            ("🏨 Островок", ostrovok_url),
            ("🏠 Суточно", sutochno_url),
            ("🧳 Яндекс Путешествия", yandex_url),
        ]

    search_hint = normalized_destination
    if budget_level == "первый класс":
        search_hint = f"{normalized_destination} luxury hotel"
    elif budget_level == "бизнес":
        search_hint = f"{normalized_destination} boutique hotel"
    elif wants_home_style or group_size >= 4:
        search_hint = f"{normalized_destination} apartment"

    booking_params = {"ss": search_hint}
    if start_date:
        booking_params["checkin"] = start_date
    if end_date:
        booking_params["checkout"] = end_date
    booking_url = "https://www.booking.com/searchresults.html?" + urllib.parse.urlencode(booking_params)
    booking_homes_url = "https://www.booking.com/searchresults.html?" + urllib.parse.urlencode(
        {
            **booking_params,
            "nflt": "ht_id=201",
        }
    )
    agoda_url = "https://www.agoda.com/search?" + urllib.parse.urlencode({"city": search_hint})
    tripadvisor_url = "https://www.tripadvisor.com/Search?" + urllib.parse.urlencode({"q": f"hotels {search_hint}"})
    airbnb_url = "https://www.airbnb.ru/s/" + urllib.parse.quote(normalized_destination) + "/homes"
    if start_date:
        airbnb_params = {"checkin": start_date, "adults": str(max(1, group_size))}
        if end_date:
            airbnb_params["checkout"] = end_date
        airbnb_url += "?" + urllib.parse.urlencode(airbnb_params)
    if wants_home_style:
        return [
            ("🏠 Airbnb", airbnb_url),
            ("🏡 Booking Homes", booking_homes_url),
            ("🛎 Agoda", agoda_url),
            ("🧭 Tripadvisor Rentals", "https://www.tripadvisor.com/Rentals"),
        ]
    if budget_level == "эконом":
        return [
            ("🛎 Agoda", agoda_url),
            ("🏨 Booking.com", booking_url),
            ("🧭 Tripadvisor Hotels", tripadvisor_url),
        ]
    return [
        ("🏨 Booking.com", booking_url),
        ("🛎 Agoda", agoda_url),
        ("🧭 Tripadvisor Hotels", tripadvisor_url),
    ]


def _excursion_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote_plus(normalized_destination)
    if _is_ru_cis_destination(normalized_destination):
        return [
            ("🎟 Tripster", f"https://experience.tripster.ru/search/?query={encoded_destination}"),
            ("🛰 Sputnik8", f"https://sputnik8.com/ru/search?query={encoded_destination}"),
            ("🎧 WeGoTrip", f"https://wegotrip.com/search/?query={encoded_destination}"),
        ]
    return [
        ("🌍 GetYourGuide", f"https://www.getyourguide.com/s/?q={encoded_destination}"),
        ("🏛 Tiqets", f"https://www.tiqets.com/en/search?query={encoded_destination}"),
        ("🧳 Viator", f"https://www.viator.com/searchResults/all?text={encoded_destination}"),
    ]


def _road_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote_plus(normalized_destination)
    if _is_ru_cis_destination(normalized_destination):
        return [
            ("🚆 Tutu", f"https://www.tutu.ru/poezda/order/?to={encoded_destination}"),
            ("🗺 Яндекс Карты", f"https://yandex.ru/maps/?text={encoded_destination}"),
        ]
    return [
        ("🚌 Omio", f"https://www.omio.com/search-frontend/results?destination={encoded_destination}"),
        ("🌍 Rome2Rio", f"https://www.rome2rio.com/s/{urllib.parse.quote(normalized_destination)}"),
        ("🗺 Google Maps", f"https://www.google.com/maps/search/{urllib.parse.quote(normalized_destination)}"),
    ]


def _car_rental_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote_plus(normalized_destination)
    if _is_ru_cis_destination(normalized_destination):
        return [
            ("🚗 Avito авто", f"https://www.avito.ru/rossiya?q=аренда+авто+{encoded_destination}"),
            ("🛞 Карта и прокат", f"https://yandex.ru/maps/?text=аренда+авто+{encoded_destination}"),
        ]
    return [
        ("🚗 Rentalcars", f"https://www.rentalcars.com/SearchResults.do?dropLocation={encoded_destination}"),
        ("🚙 DiscoverCars", f"https://www.discovercars.com/?q={encoded_destination}"),
    ]


def _bike_rental_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote_plus(normalized_destination)
    if _is_ru_cis_destination(normalized_destination):
        return [
            ("🏍 Avito мото", f"https://www.avito.ru/rossiya?q=аренда+мото+{encoded_destination}"),
            ("🛵 Карта и прокат", f"https://yandex.ru/maps/?text=аренда+байка+{encoded_destination}"),
        ]
    return [
        ("🏍 BikesBooking", f"https://bikesbooking.com/en/search?query={encoded_destination}"),
        ("🛵 Bike rental", f"https://www.google.com/maps/search/bike+rental+{urllib.parse.quote(normalized_destination)}"),
    ]


def _transfer_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote_plus(normalized_destination)
    if _is_ru_cis_destination(normalized_destination):
        return [("🚕 Трансфер / такси", f"https://yandex.ru/maps/?text=трансфер+{encoded_destination}")]
    return [
        ("🚐 Kiwitaxi", f"https://kiwitaxi.com/en/search?query={encoded_destination}"),
        ("🚕 Transfer", f"https://www.google.com/maps/search/airport+transfer+{urllib.parse.quote(normalized_destination)}"),
    ]


def _estimate_housing_result(destination: str, source: str, group_size: int, budget_text: str, context_text: str) -> tuple[str, str, int]:
    currency = _currency_for_destination(destination)
    lowered_context = (context_text or "").lower()
    budget_level = _normalize_budget_level(budget_text)

    if any(keyword in lowered_context for keyword in ("пентхаус", "вилла", "люкс")):
        style = "пентхаус / вилла"
    elif any(keyword in lowered_context for keyword in ("дом", "коттедж", "турбаза")):
        style = "дом / коттедж"
    elif any(keyword in lowered_context for keyword in ("квартира", "апартам", "студия")):
        style = "квартира / апартаменты"
    elif source == "🏠 Суточно":
        style = "квартира / дом"
    elif group_size >= 4:
        style = "квартира / дом"
    else:
        style = "отель / студия"

    if budget_level == "первый класс" and style == "отель / студия":
        style = "премиальный отель / люкс"
    elif budget_level == "бизнес" and style == "отель / студия":
        style = "комфортный отель / апартаменты"
    elif budget_level == "эконом" and style == "отель / студия":
        style = "базовый отель / студия"

    if currency == "RUB":
        base = 4900 if source == "🏨 Островок" else 5300 if source == "🏠 Суточно" else 5100
        if budget_level == "эконом":
            base -= 900
        elif budget_level == "первый класс":
            base += 3400
        elif budget_level == "бизнес":
            base += 1200
        if "пентхаус" in style or "вилла" in style:
            base += 5000
        elif "дом" in style:
            base += 1800
        elif "хостел" in style:
            base -= 1400
        score = 9 if source in {"🏨 Островок", "🏠 Суточно"} else 8
        if budget_level == "первый класс" and source == "🧳 Яндекс Путешествия":
            score = 10
        return f"от {base:,} ₽/ночь".replace(",", " "), style, score

    if currency == "EUR":
        base = 82 if source == "🛎 Agoda" else 95
        if budget_level == "бизнес":
            base += 55
        elif budget_level == "первый класс":
            base += 160
        return f"from {base} EUR/night", style, 8
    if currency == "TRY":
        base = 3200
        if budget_level == "бизнес":
            base = 6200
        elif budget_level == "первый класс":
            base = 11800
        return f"from {base:,} TRY/night".replace(",", " "), style, 8
    return "открыть варианты по ссылке", style, 8


def build_links_map(
    destination: str,
    dates_text: str | None,
    origin: str | None,
    *,
    days_count: int | None = None,
    group_size: int = 2,
    context_text: str = "",
    budget_text: str = "",
) -> dict[str, list[tuple[str, str]]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return {}

    start_resolved, end_resolved = resolve_trip_dates(dates_text, days_count)
    one_way = is_one_way_trip_text(context_text, dates_text)
    start_date = start_resolved.isoformat() if start_resolved else None
    end_date = None if one_way else (end_resolved.isoformat() if end_resolved else None)
    needs = detect_link_needs(context_text)
    if not needs:
        needs = {"tickets", "housing"}

    links: dict[str, list[tuple[str, str]]] = {}
    if "tickets" in needs:
        ticket_links = _ticket_links(normalized_destination, origin, start_date, end_date)
        if ticket_links:
            links["tickets"] = ticket_links
    if "housing" in needs:
        housing_links = _housing_links(
            normalized_destination,
            start_date,
            end_date,
            group_size,
            budget_text,
            context_text,
        )
        if housing_links:
            links["housing"] = housing_links
    if "excursions" in needs:
        links["excursions"] = _excursion_links(normalized_destination)
    if "road" in needs:
        links["road"] = _road_links(normalized_destination)
    if "car_rental" in needs:
        links["car_rental"] = _car_rental_links(normalized_destination)
    if "bike_rental" in needs:
        links["bike_rental"] = _bike_rental_links(normalized_destination)
    if "transfers" in needs:
        links["transfers"] = _transfer_links(normalized_destination)
    return {key: value for key, value in links.items() if value}


def build_structured_link_results(
    destination: str,
    dates_text: str | None,
    origin: str | None,
    *,
    days_count: int | None = None,
    group_size: int = 2,
    context_text: str = "",
    budget_text: str = "",
) -> dict[str, list[TravelSearchResult]]:
    links_map = build_links_map(
        destination,
        dates_text,
        origin,
        days_count=days_count,
        group_size=group_size,
        context_text=context_text,
        budget_text=budget_text,
    )
    results: dict[str, list[TravelSearchResult]] = {
        "tickets": [],
        "housing": [],
        "excursions": [],
        "road": [],
        "car_rental": [],
        "bike_rental": [],
        "transfers": [],
    }

    for category, items in links_map.items():
        for label, url in items:
            if category == "housing":
                price_text, style, score = _estimate_housing_result(destination, label, group_size, budget_text, context_text)
                h_start, h_end = resolve_trip_dates(dates_text, days_count)
                if is_one_way_trip_text(context_text, dates_text):
                    h_end = None
                total_nights = None
                if h_start and h_end:
                    try:
                        total_nights = (h_end - h_start).days
                    except TypeError:
                        pass
                note = style
                if total_nights and total_nights > 0:
                    # Extract price per night from price_text like "от 6 200 ₽/ночь"
                    price_match = re.search(r"от\s*([\d\s]+)\s*₽/ночь", price_text)
                    if price_match:
                        per_night = int(price_match.group(1).replace(" ", ""))
                        total_price = per_night * total_nights
                        note = f"{style}, ≈ {total_price:,} ₽ за {total_nights} ноч.".replace(",", " ")
                results[category].append(
                    TravelSearchResult(
                        title=f"{CATEGORY_TITLES[category]}: {label}",
                        price_text=price_text,
                        url=url,
                        source=label,
                        score=score,
                        note=note,
                    )
                )
            else:
                score = 9 if category == "tickets" else 8
                price_text = "открыть варианты по ссылке"
                note = f"оценка {score}/10"
                if category == "tickets":
                    price_text = "цена не загружена"
                    note = "откройте поиск по ссылке"
                results[category].append(
                    TravelSearchResult(
                        title=f"{CATEGORY_TITLES[category]}: {label}",
                        price_text=price_text,
                        url=url,
                        source=label,
                        score=score,
                        note=note,
                    )
                )

    normalized_results: dict[str, list[TravelSearchResult]] = {}
    for key, value in results.items():
        if not value:
            continue
        if key == "housing":
            value = sorted(value, key=lambda item: (-item.score, item.title))
        normalized_results[key] = trim_results(value)
    return normalized_results


def build_links_text(
    destination: str,
    dates_text: str | None,
    origin: str | None,
    *,
    days_count: int | None = None,
    group_size: int = 2,
    context_text: str = "",
    budget_text: str = "",
) -> str:
    links_map = build_links_map(
        destination,
        dates_text,
        origin,
        days_count=days_count,
        group_size=group_size,
        context_text=context_text,
        budget_text=budget_text,
    )
    if not links_map:
        return ""

    lines: list[str] = []
    for category, items in links_map.items():
        lines.append(CATEGORY_TITLES.get(category, category))
        for label, url in items:
            lines.append(f"• {label}: {url}")
        lines.append("")
    return "\n".join(lines).strip()
