from __future__ import annotations

import urllib.parse

from weather_service import _parse_dates_range


def build_links_text(destination: str, dates_text: str, origin: str | None = None) -> str:
    destination = (destination or "").strip()
    origin = (origin or "").strip()
    if not destination:
        return ""

    encoded_destination = urllib.parse.quote(destination)
    encoded_origin = urllib.parse.quote(origin) if origin and origin != "не указано" else ""
    links: list[str] = []

    booking_url = f"https://www.booking.com/searchresults.ru.html?ss={encoded_destination}"
    ostrovok_url = f"https://ostrovok.ru/hotel/russia/{encoded_destination}/"
    maps_url = f"https://www.google.com/maps/search/{encoded_destination}"

    date_range = _parse_dates_range(dates_text)
    if date_range:
        start, end = date_range
        checkin = start.isoformat()
        checkout = end.isoformat()
        booking_url = (
            "https://www.booking.com/searchresults.ru.html?"
            + urllib.parse.urlencode(
                {
                    "ss": destination,
                    "checkin": checkin,
                    "checkout": checkout,
                    "group_adults": "2",
                    "no_rooms": "1",
                }
            )
        )
        if encoded_origin:
            aviasales_url = (
                "https://www.aviasales.ru/search/"
                f"{urllib.parse.quote(origin)}{start.strftime('%d%m')}"
                f"{encoded_destination}{end.strftime('%d%m')}1"
            )
        else:
            aviasales_url = (
                "https://www.aviasales.ru/search?"
                + urllib.parse.urlencode({"destination": destination, "depart_date": checkin, "return_date": checkout})
            )
    else:
        aviasales_url = (
            f"https://www.aviasales.ru/search?destination={encoded_destination}"
            if not encoded_origin
            else f"https://www.aviasales.ru/search?origin={encoded_origin}&destination={encoded_destination}"
        )

    links.append(f"✈️ Билеты: {aviasales_url}")
    links.append(f"🏨 Жильё: {booking_url}")
    links.append(f"🛏 Альтернатива по жилью: {ostrovok_url}")
    links.append(f"🗺 Карта и точки рядом: {maps_url}")
    links.append("💡 Точных live-цен в боте пока нет: ссылки ведут в реальные поиски, где можно увидеть актуальную стоимость.")
    return "\n".join(links)
