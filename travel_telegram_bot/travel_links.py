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

    ostrovok_url = f"https://ostrovok.ru/hotel/search/?q={encoded_destination}"
    sutochno_url = f"https://sutochno.ru/search?city={encoded_destination}"
    tripster_url = f"https://experience.tripster.ru/search/?query={encoded_destination}"
    yandex_travel_url = f"https://travel.yandex.ru/hotels/search?where={encoded_destination}"
    maps_url = f"https://yandex.ru/maps/?text={encoded_destination}"
    tutu_url = f"https://www.tutu.ru/poezda/order/?to={encoded_destination}"

    date_range = _parse_dates_range(dates_text)
    if date_range:
        start, end = date_range
        checkin = start.isoformat()
        checkout = end.isoformat()
        ostrovok_url = (
            "https://ostrovok.ru/hotel/search/?"
            + urllib.parse.urlencode(
                {
                    "q": destination,
                    "checkin": checkin,
                    "checkout": checkout,
                }
            )
        )
        yandex_travel_url = (
            "https://travel.yandex.ru/hotels/search?"
            + urllib.parse.urlencode({"where": destination, "checkinDate": checkin, "checkoutDate": checkout})
        )
        sutochno_url = (
            "https://sutochno.ru/search?"
            + urllib.parse.urlencode(
                {
                    "q": destination,
                    "datefrom": checkin,
                    "dateto": checkout,
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
    links.append(f"🚆 Поезда / дорога: {tutu_url}")
    links.append(f"🏨 Жильё: {ostrovok_url}")
    links.append(f"🏠 Посуточно: {sutochno_url}")
    links.append(f"🧳 Альтернатива: {yandex_travel_url}")
    links.append(f"🎟 Экскурсии: {tripster_url}")
    links.append(f"🗺 Карта и точки рядом: {maps_url}")
    links.append("💡 Точных live-цен в боте пока нет: ссылки ведут в реальные поиски, где можно увидеть актуальную стоимость.")
    return "\n".join(links)
