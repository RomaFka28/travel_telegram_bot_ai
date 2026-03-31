from __future__ import annotations

import urllib.parse

from travel_locale import detect_route_locale
from travel_result_models import TravelSearchResult, trim_results
from value_normalization import normalized_search_value
from weather_service import _parse_dates_range


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tickets": ("билет", "авиа", "самолет", "самолёт", "летим", "перелет", "перелёт", "рейс"),
    "housing": ("отел", "гостини", "жиль", "апарт", "квартир", "суточ", "ночев", "домик", "турбаз"),
    "excursions": ("экскурс", "гид", "музей", "тур", "tripster", "sputnik", "wegotrip", "аудиогид"),
    "road": ("поезд", "автобус", "дорог", "маршрут", "электрич", "жд", "ж/д", "tutu", "omio"),
    "car_rental": ("аренд", "машин", "авто", "тачк", "прокат авто", "car rent"),
    "bike_rental": ("мото", "байк", "скутер", "мопед", "прокат байка", "прокат мото"),
    "transfers": ("трансфер", "такси", "из аэропорта", "в аэропорт"),
}

CATEGORY_TITLES: dict[str, str] = {
    "tickets": "Билеты и перелёт",
    "housing": "Жильё и размещение",
    "excursions": "Экскурсии и активности",
    "road": "Дорога по земле",
    "car_rental": "Аренда авто",
    "bike_rental": "Аренда мото / байка",
    "transfers": "Трансферы",
}

SOURCE_LABELS = {
    "✈️ Билеты": "Билеты",
    "🏨 Островок": "Островок",
    "🏠 Суточно": "Суточно",
    "🧳 Яндекс Путешествия": "Яндекс Путешествия",
    "🏘 Avito Путешествия": "Avito Путешествия",
    "🌲 Мир Турбаз": "Мир Турбаз",
    "🏨 Booking.com": "Booking.com",
    "🛎 Agoda": "Agoda",
    "🧭 Tripadvisor Hotels": "Tripadvisor Hotels",
    "🎟 Tripster": "Tripster",
    "🛰 Sputnik8": "Sputnik8",
    "🎧 WeGoTrip": "WeGoTrip",
    "🥾 YouTravel": "YouTravel",
    "🌍 GetYourGuide": "GetYourGuide",
    "🏛 Tiqets": "Tiqets",
    "🧳 Viator": "Viator",
    "🚆 Tutu": "Tutu",
    "🗺 Маршрут": "Маршрут",
    "🚄 Omio": "Omio",
    "🌍 Rome2Rio": "Rome2Rio",
    "🚗 Avito аренда авто": "Avito аренда авто",
    "🚘 Карта и прокат": "Карта и прокат",
    "🚗 Rentalcars": "Rentalcars",
    "🚙 DiscoverCars": "DiscoverCars",
    "🏍 Avito мото / байк": "Avito мото / байк",
    "🛵 Карта и прокат": "Карта и прокат",
    "🛵 BikesBooking": "BikesBooking",
    "🚕 Трансфер / такси": "Трансфер / такси",
    "🚐 Kiwitaxi": "Kiwitaxi",
}

CITY_DEEPLINKS = {
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
    return [("✈️ Билеты", aviasales)]


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
                    "🏨 Островок",
                    f"https://ostrovok.ru/hotel/{ostrovok_path}?" + urllib.parse.urlencode(ostrovok_params),
                ),
                (
                    "🧳 Яндекс Путешествия",
                    f"https://travel.yandex.ru/hotels/{yandex_path}/?"
                    + urllib.parse.urlencode({"checkinDate": start_date, "checkoutDate": end_date, "adults": max(1, group_size)}),
                ),
            ] + (
                [
                    (
                        "🏠 Суточно",
                        f"https://{sutochno_host}/?{urllib.parse.urlencode({'arrival': start_date, 'departure': end_date, 'guests': max(1, group_size)})}",
                    )
                ]
                if sutochno_host
                else []
            )

        results = [
            ("🏨 Островок", f"https://ostrovok.ru/hotel/{ostrovok_path}/"),
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
        ("🏨 Booking.com", "https://www.booking.com/searchresults.html?" + urllib.parse.urlencode(booking_params)),
        ("🛎 Agoda", "https://www.agoda.com/search?" + urllib.parse.urlencode({"city": normalized_destination})),
        ("🧭 Tripadvisor Hotels", "https://www.tripadvisor.com/Search?" + urllib.parse.urlencode({"q": f"hotels {normalized_destination}"})),
    ]


def _excursion_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [
            ("🎟 Tripster", f"https://experience.tripster.ru/search/?query={encoded_destination}"),
            ("🛰 Sputnik8", f"https://sputnik8.com/ru/search?query={encoded_destination}"),
            ("🎧 WeGoTrip", f"https://wegotrip.com/search/?query={encoded_destination}"),
            ("🥾 YouTravel", f"https://youtravel.me/search?query={encoded_destination}"),
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
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [
            ("🚆 Tutu", f"https://www.tutu.ru/poezda/order/?to={encoded_destination}"),
            ("🗺 Маршрут", f"https://yandex.ru/maps/?text={encoded_destination}"),
        ]
    return [
        ("🚄 Omio", f"https://www.omio.com/search-frontend/results?destination={encoded_destination}"),
        ("🌍 Rome2Rio", f"https://www.rome2rio.com/s/{encoded_destination}"),
        ("🗺 Маршрут", f"https://www.google.com/maps/search/{encoded_destination}"),
    ]


def _car_rental_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [
            ("🚗 Avito аренда авто", f"https://www.avito.ru/rossiya?q=аренда+авто+{encoded_destination}"),
            ("🚘 Карта и прокат", f"https://yandex.ru/maps/?text=аренда+авто+{encoded_destination}"),
        ]
    return [
        ("🚗 Rentalcars", f"https://www.rentalcars.com/SearchResults.do?dropLocation={encoded_destination}"),
        ("🚙 DiscoverCars", f"https://www.discovercars.com/?q={encoded_destination}"),
        ("🚘 Карта и прокат", f"https://www.google.com/maps/search/car+rental+{encoded_destination}"),
    ]


def _bike_rental_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [
            ("🏍 Avito мото / байк", f"https://www.avito.ru/rossiya?q=аренда+мото+{encoded_destination}"),
            ("🛵 Карта и прокат", f"https://yandex.ru/maps/?text=аренда+байка+{encoded_destination}"),
        ]
    return [
        ("🛵 BikesBooking", f"https://bikesbooking.com/en/search?query={encoded_destination}"),
        ("🛵 Карта и прокат", f"https://www.google.com/maps/search/bike+rental+{encoded_destination}"),
    ]


def _transfer_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    locale = detect_route_locale(normalized_destination)
    if locale.is_ru_cis_destination:
        return [("🚕 Трансфер / такси", f"https://yandex.ru/maps/?text=трансфер+{encoded_destination}")]
    return [
        ("🚐 Kiwitaxi", f"https://kiwitaxi.com/en/search?query={encoded_destination}"),
        ("🚕 Трансфер / такси", f"https://www.google.com/maps/search/airport+transfer+{encoded_destination}"),
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
        results = [
            TravelSearchResult(
                title=f"{CATEGORY_TITLES.get(category, category)}: {label}",
                price_text="Откройте ссылку, чтобы увидеть актуальные варианты и цены.",
                url=url,
                source=SOURCE_LABELS.get(label, label),
                dates=dates_text or "",
                note="Подобрано из обсуждения в чате.",
            )
            for label, url in items
        ]
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
        "Бот показывает только те поисковые сценарии, которые прозвучали в переписке. "
        "Live-цены по билетам доступны через Travelpayouts."
    )
    return "\n".join(lines)
