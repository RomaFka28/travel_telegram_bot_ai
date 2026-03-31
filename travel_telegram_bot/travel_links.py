from __future__ import annotations

import urllib.parse

from travel_result_models import TravelSearchResult, trim_results
from value_normalization import normalized_search_value
from weather_service import _parse_dates_range


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tickets": ("билет", "авиа", "самолет", "самолёт", "летим", "перелет", "перелёт", "рейс"),
    "housing": ("отел", "гостини", "жиль", "апарт", "квартир", "суточ", "ночев", "домик", "турбаз"),
    "excursions": ("экскурс", "гид", "музей", "тур", "tripster", "sputnik", "wegotrip", "аудиогид"),
    "road": ("поезд", "автобус", "дорог", "маршрут", "электрич", "жд", "ж/д", "tutu"),
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
    "🎟 Tripster": "Tripster",
    "🛰 Sputnik8": "Sputnik8",
    "🎧 WeGoTrip": "WeGoTrip",
    "🥾 YouTravel": "YouTravel",
    "🚆 Tutu": "Tutu",
    "🗺 Маршрут": "Маршрут",
    "🚗 Avito аренда авто": "Avito аренда авто",
    "🚘 Карта и прокат": "Карта и прокат",
    "🏍 Avito мото / байк": "Avito мото / байк",
    "🛵 Карта и прокат": "Карта и прокат",
    "🚕 Трансфер / такси": "Трансфер / такси",
}


def detect_link_needs(context_text: str) -> set[str]:
    lowered = (context_text or "").lower()
    needs: set[str] = set()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            needs.add(category)
    return needs


def _ticket_links(destination: str, origin: str, start_date: str | None, end_date: str | None) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    normalized_origin = normalized_search_value(origin)
    if not normalized_destination:
        return []

    encoded_destination = urllib.parse.quote(normalized_destination)
    encoded_origin = urllib.parse.quote(normalized_origin) if normalized_origin else ""

    if start_date and end_date:
        if encoded_origin:
            aviasales = (
                "https://www.aviasales.ru/search/"
                f"{urllib.parse.quote(normalized_origin)}{start_date[8:10]}{start_date[5:7]}"
                f"{encoded_destination}{end_date[8:10]}{end_date[5:7]}1"
            )
        else:
            aviasales = (
                "https://www.aviasales.ru/search?"
                + urllib.parse.urlencode(
                    {"destination": normalized_destination, "depart_date": start_date, "return_date": end_date}
                )
            )
    elif encoded_origin:
        aviasales = (
            "https://www.aviasales.ru/search?"
            + urllib.parse.urlencode({"origin": normalized_origin, "destination": normalized_destination})
        )
    else:
        aviasales = f"https://www.aviasales.ru/search?destination={encoded_destination}"
    return [("✈️ Билеты", aviasales)]


def _housing_links(destination: str, start_date: str | None, end_date: str | None) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []

    if start_date and end_date:
        return [
            (
                "🏨 Островок",
                "https://ostrovok.ru/hotel/search/?"
                + urllib.parse.urlencode({"q": normalized_destination, "checkin": start_date, "checkout": end_date}),
            ),
            (
                "🏠 Суточно",
                "https://sutochno.ru/search?"
                + urllib.parse.urlencode({"q": normalized_destination, "datefrom": start_date, "dateto": end_date}),
            ),
            (
                "🧳 Яндекс Путешествия",
                "https://travel.yandex.ru/hotels/search?"
                + urllib.parse.urlencode({"where": normalized_destination, "checkinDate": start_date, "checkoutDate": end_date}),
            ),
        ]

    encoded_destination = urllib.parse.quote(normalized_destination)
    return [
        ("🏨 Островок", f"https://ostrovok.ru/hotel/search/?q={encoded_destination}"),
        ("🏠 Суточно", f"https://sutochno.ru/search?city={encoded_destination}"),
        ("🧳 Яндекс Путешествия", f"https://travel.yandex.ru/hotels/search?where={encoded_destination}"),
        ("🏘 Avito Путешествия", f"https://www.avito.ru/rossiya/kvartiry/sdam/posutochno?cd=1&q={encoded_destination}"),
        ("🌲 Мир Турбаз", f"https://mirturbaz.ru/catalog/russia?search={encoded_destination}"),
    ]


def _excursion_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    return [
        ("🎟 Tripster", f"https://experience.tripster.ru/search/?query={encoded_destination}"),
        ("🛰 Sputnik8", f"https://sputnik8.com/ru/search?query={encoded_destination}"),
        ("🎧 WeGoTrip", f"https://wegotrip.com/search/?query={encoded_destination}"),
        ("🥾 YouTravel", f"https://youtravel.me/search?query={encoded_destination}"),
    ]


def _road_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    return [
        ("🚆 Tutu", f"https://www.tutu.ru/poezda/order/?to={encoded_destination}"),
        ("🗺 Маршрут", f"https://yandex.ru/maps/?text={encoded_destination}"),
    ]


def _car_rental_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    return [
        ("🚗 Avito аренда авто", f"https://www.avito.ru/rossiya?q=аренда+авто+{encoded_destination}"),
        ("🚘 Карта и прокат", f"https://yandex.ru/maps/?text=аренда+авто+{encoded_destination}"),
    ]


def _bike_rental_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    return [
        ("🏍 Avito мото / байк", f"https://www.avito.ru/rossiya?q=аренда+мото+{encoded_destination}"),
        ("🛵 Карта и прокат", f"https://yandex.ru/maps/?text=аренда+байка+{encoded_destination}"),
    ]


def _transfer_links(destination: str) -> list[tuple[str, str]]:
    normalized_destination = normalized_search_value(destination)
    if not normalized_destination:
        return []
    encoded_destination = urllib.parse.quote(normalized_destination)
    return [
        ("🚕 Трансфер / такси", f"https://yandex.ru/maps/?text=трансфер+{encoded_destination}"),
    ]


def build_links_map(
    destination: str,
    dates_text: str,
    origin: str | None = None,
    *,
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
        link_items.extend(_housing_links(normalized_destination, start_date, end_date))
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
            normalized_destination, start_date, end_date
        )[:2]

    return dict(link_items)


def build_structured_link_results(
    destination: str,
    dates_text: str,
    origin: str | None = None,
    *,
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
        housing_links = _housing_links(normalized_destination, start_date, end_date)
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
    context_text: str = "",
) -> str:
    links = build_links_map(destination, dates_text, origin, context_text=context_text)
    if not links:
        return ""

    lines = [f"{label}: {url}" for label, url in links.items()]
    lines.append(
        "💡 Бот показывает только те поисковые сценарии, которые прозвучали в переписке. "
        "Live-цены по билетам доступны через Travelpayouts."
    )
    return "\n".join(lines)
