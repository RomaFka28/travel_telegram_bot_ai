from __future__ import annotations

import urllib.parse

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


def detect_link_needs(context_text: str) -> set[str]:
    lowered = (context_text or "").lower()
    needs: set[str] = set()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            needs.add(category)
    return needs


def _ticket_links(destination: str, origin: str, start_date: str | None, end_date: str | None) -> list[tuple[str, str]]:
    encoded_destination = urllib.parse.quote(destination)
    encoded_origin = urllib.parse.quote(origin) if origin and origin != "не указано" else ""
    if start_date and end_date:
        if encoded_origin:
            aviasales = (
                "https://www.aviasales.ru/search/"
                f"{urllib.parse.quote(origin)}{start_date[8:10]}{start_date[5:7]}"
                f"{encoded_destination}{end_date[8:10]}{end_date[5:7]}1"
            )
        else:
            aviasales = (
                "https://www.aviasales.ru/search?"
                + urllib.parse.urlencode(
                    {"destination": destination, "depart_date": start_date, "return_date": end_date}
                )
            )
    elif encoded_origin:
        aviasales = (
            "https://www.aviasales.ru/search?"
            + urllib.parse.urlencode({"origin": origin, "destination": destination})
        )
    else:
        aviasales = f"https://www.aviasales.ru/search?destination={encoded_destination}"
    return [("✈️ Билеты", aviasales)]


def _housing_links(destination: str, start_date: str | None, end_date: str | None) -> list[tuple[str, str]]:
    if start_date and end_date:
        return [
            (
                "🏨 Островок",
                "https://ostrovok.ru/hotel/search/?"
                + urllib.parse.urlencode({"q": destination, "checkin": start_date, "checkout": end_date}),
            ),
            (
                "🏠 Суточно",
                "https://sutochno.ru/search?"
                + urllib.parse.urlencode({"q": destination, "datefrom": start_date, "dateto": end_date}),
            ),
            (
                "🧳 Яндекс Путешествия",
                "https://travel.yandex.ru/hotels/search?"
                + urllib.parse.urlencode({"where": destination, "checkinDate": start_date, "checkoutDate": end_date}),
            ),
        ]
    encoded_destination = urllib.parse.quote(destination)
    return [
        ("🏨 Островок", f"https://ostrovok.ru/hotel/search/?q={encoded_destination}"),
        ("🏠 Суточно", f"https://sutochno.ru/search?city={encoded_destination}"),
        ("🧳 Яндекс Путешествия", f"https://travel.yandex.ru/hotels/search?where={encoded_destination}"),
        (
            "🏘 Avito Путешествия",
            f"https://www.avito.ru/rossiya/kvartiry/sdam/posutochno?cd=1&q={encoded_destination}",
        ),
        ("🌲 Мир Турбаз", f"https://mirturbaz.ru/catalog/russia?search={encoded_destination}"),
    ]


def _excursion_links(destination: str) -> list[tuple[str, str]]:
    encoded_destination = urllib.parse.quote(destination)
    return [
        ("🎟 Tripster", f"https://experience.tripster.ru/search/?query={encoded_destination}"),
        ("🛰 Sputnik8", f"https://sputnik8.com/ru/search?query={encoded_destination}"),
        ("🎧 WeGoTrip", f"https://wegotrip.com/search/?query={encoded_destination}"),
        ("🥾 YouTravel", f"https://youtravel.me/search?query={encoded_destination}"),
    ]


def _road_links(destination: str) -> list[tuple[str, str]]:
    encoded_destination = urllib.parse.quote(destination)
    return [
        ("🚆 Tutu", f"https://www.tutu.ru/poezda/order/?to={encoded_destination}"),
        ("🗺 Маршрут", f"https://yandex.ru/maps/?text={encoded_destination}"),
    ]


def _car_rental_links(destination: str) -> list[tuple[str, str]]:
    encoded_destination = urllib.parse.quote(destination)
    return [
        ("🚗 Avito аренда авто", f"https://www.avito.ru/rossiya?q=аренда+авто+{encoded_destination}"),
        ("🚘 Карта и прокат", f"https://yandex.ru/maps/?text=аренда+авто+{encoded_destination}"),
    ]


def _bike_rental_links(destination: str) -> list[tuple[str, str]]:
    encoded_destination = urllib.parse.quote(destination)
    return [
        ("🏍 Avito мото / байк", f"https://www.avito.ru/rossiya?q=аренда+мото+{encoded_destination}"),
        ("🛵 Карта и прокат", f"https://yandex.ru/maps/?text=аренда+байка+{encoded_destination}"),
    ]


def _transfer_links(destination: str) -> list[tuple[str, str]]:
    encoded_destination = urllib.parse.quote(destination)
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
    destination = (destination or "").strip()
    origin = (origin or "").strip()
    if not destination:
        return {}

    date_range = _parse_dates_range(dates_text)
    start_date = date_range[0].isoformat() if date_range else None
    end_date = date_range[1].isoformat() if date_range else None
    needs = detect_link_needs(context_text)

    link_items: list[tuple[str, str]] = []
    if "tickets" in needs:
        link_items.extend(_ticket_links(destination, origin, start_date, end_date))
    if "housing" in needs:
        link_items.extend(_housing_links(destination, start_date, end_date))
    if "excursions" in needs:
        link_items.extend(_excursion_links(destination))
    if "road" in needs:
        link_items.extend(_road_links(destination))
    if "car_rental" in needs:
        link_items.extend(_car_rental_links(destination))
    if "bike_rental" in needs:
        link_items.extend(_bike_rental_links(destination))
    if "transfers" in needs:
        link_items.extend(_transfer_links(destination))

    if not link_items:
        link_items = _ticket_links(destination, origin, start_date, end_date) + _housing_links(destination, start_date, end_date)[:2]

    return dict(link_items)


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
