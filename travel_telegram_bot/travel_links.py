from __future__ import annotations

import urllib.parse

from weather_service import _parse_dates_range


def build_links_map(destination: str, dates_text: str, origin: str | None = None) -> dict[str, str]:
    destination = (destination or "").strip()
    origin = (origin or "").strip()
    if not destination:
        return {}

    encoded_destination = urllib.parse.quote(destination)
    encoded_origin = urllib.parse.quote(origin) if origin and origin != "не указано" else ""

    links = {
        "✈️ Билеты": f"https://www.aviasales.ru/search?destination={encoded_destination}",
        "🚆 Поезда / дорога": f"https://www.tutu.ru/poezda/order/?to={encoded_destination}",
        "🏨 Жильё": f"https://ostrovok.ru/hotel/search/?q={encoded_destination}",
        "🏠 Посуточно": f"https://sutochno.ru/search?city={encoded_destination}",
        "🏘 Avito Путешествия": f"https://www.avito.ru/rossiya/kvartiry/sdam/posutochno?cd=1&q={encoded_destination}",
        "🧳 Альтернатива": f"https://travel.yandex.ru/hotels/search?where={encoded_destination}",
        "🌲 Мир Турбаз": f"https://mirturbaz.ru/catalog/russia?search={encoded_destination}",
        "🎟 Экскурсии": f"https://experience.tripster.ru/search/?query={encoded_destination}",
        "🛰 Sputnik8": f"https://sputnik8.com/ru/search?query={encoded_destination}",
        "🎧 WeGoTrip": f"https://wegotrip.com/search/?query={encoded_destination}",
        "🥾 YouTravel": f"https://youtravel.me/search?query={encoded_destination}",
        "🗺 Карта и точки рядом": f"https://yandex.ru/maps/?text={encoded_destination}",
    }

    date_range = _parse_dates_range(dates_text)
    if date_range:
        start, end = date_range
        checkin = start.isoformat()
        checkout = end.isoformat()
        links["🏨 Жильё"] = (
            "https://ostrovok.ru/hotel/search/?"
            + urllib.parse.urlencode({"q": destination, "checkin": checkin, "checkout": checkout})
        )
        links["🏠 Посуточно"] = (
            "https://sutochno.ru/search?"
            + urllib.parse.urlencode({"q": destination, "datefrom": checkin, "dateto": checkout})
        )
        links["🧳 Альтернатива"] = (
            "https://travel.yandex.ru/hotels/search?"
            + urllib.parse.urlencode({"where": destination, "checkinDate": checkin, "checkoutDate": checkout})
        )
        if encoded_origin:
            links["✈️ Билеты"] = (
                "https://www.aviasales.ru/search/"
                f"{urllib.parse.quote(origin)}{start.strftime('%d%m')}"
                f"{encoded_destination}{end.strftime('%d%m')}1"
            )
        else:
            links["✈️ Билеты"] = (
                "https://www.aviasales.ru/search?"
                + urllib.parse.urlencode({"destination": destination, "depart_date": checkin, "return_date": checkout})
            )
    elif encoded_origin:
        links["✈️ Билеты"] = (
            "https://www.aviasales.ru/search?"
            + urllib.parse.urlencode({"origin": origin, "destination": destination})
        )

    return links


def build_links_text(destination: str, dates_text: str, origin: str | None = None) -> str:
    links = build_links_map(destination, dates_text, origin)
    if not links:
        return ""

    lines = [f"{label}: {url}" for label, url in links.items()]
    lines.append(
        "💡 Live-цены по билетам бот может показать через Travelpayouts. "
        "По жилью и экскурсиям ссылки ведут в реальные поиски и каталоги."
    )
    return "\n".join(lines)
