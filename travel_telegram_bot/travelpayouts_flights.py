from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from urllib import parse as urllib_parse

from date_utils import parse_dates_range
from http_utils import safe_http_get
from travel_result_models import TravelSearchResult, trim_results
from travel_planner import BUDGET_HINTS
from travelpayouts_partner_links import TravelpayoutsPartnerLinksClient
from value_normalization import normalized_search_value


class TravelpayoutsError(RuntimeError):
    pass


@dataclass(slots=True)
class PlaceMatch:
    code: str
    name: str
    type: str


@dataclass(slots=True)
class FlightOffer:
    origin: str
    destination: str
    depart_date: str
    return_date: str
    value: int
    number_of_changes: int
    actual: bool
    trip_class: int = 0


class TravelpayoutsFlightProvider:
    def __init__(
        self,
        api_key: str,
        partner_links: TravelpayoutsPartnerLinksClient | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._partner_links = partner_links

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def build_ticket_snapshot(
        self,
        *,
        origin: str,
        destination: str,
        dates_text: str,
        budget_text: str,
        group_size: int,
        source_text: str = "",
    ) -> str:
        if not self.enabled:
            return ""
        normalized_destination = normalized_search_value(destination)
        normalized_origin = normalized_search_value(origin)
        if not normalized_destination:
            return ""
        if not normalized_origin:
            return (
                "\u0411\u0438\u043b\u0435\u0442\u044b: \u0447\u0442\u043e\u0431\u044b \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0446\u0435\u043d\u044b \u0447\u0435\u0440\u0435\u0437 Travelpayouts, \u043d\u0443\u0436\u0435\u043d \u0433\u043e\u0440\u043e\u0434 \u0432\u044b\u043b\u0435\u0442\u0430.\n"
                "\u041f\u043e\u0434\u0441\u043a\u0430\u0437\u043a\u0430: \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u0432 \u0447\u0430\u0442\u0435 \u0447\u0442\u043e-\u0442\u043e \u0432\u0440\u043e\u0434\u0435 \u00ab\u043b\u0435\u0442\u0438\u043c \u0438\u0437 \u0422\u043e\u043c\u0441\u043a\u0430\u00bb \u0438\u043b\u0438 \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u043f\u043e\u0435\u0437\u0434\u043a\u0443 \u0447\u0435\u0440\u0435\u0437 /plan."
            )

        results = self.search_results(
            origin=normalized_origin,
            destination=normalized_destination,
            dates_text=dates_text,
            budget_text=budget_text,
            group_size=group_size,
            source_text=source_text,
        )
        if not results:
            try:
                origin_match = self._resolve_place(normalized_origin)
                destination_match = self._resolve_place(normalized_destination)
                date_range = parse_dates_range(dates_text)
                one_way = self._is_one_way(source_text, dates_text)
                search_url = self._build_search_url(
                    origin_code=origin_match.code,
                    destination_code=destination_match.code,
                    start_date=date_range[0].isoformat() if date_range else None,
                    end_date=date_range[1].isoformat() if date_range and not one_way else None,
                    one_way=one_way,
                    adults=group_size,
                )
            except TravelpayoutsError:
                search_url = "https://www.aviasales.ru"
            return (
                f"\u0411\u0438\u043b\u0435\u0442\u044b: Travelpayouts \u043f\u043e\u043a\u0430 \u043d\u0435 \u0432\u0435\u0440\u043d\u0443\u043b \u0441\u0432\u0435\u0436\u0438\u0445 \u0446\u0435\u043d \u043f\u043e \u043d\u0430\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044e {normalized_origin} -> {normalized_destination}.\n"
                f"\u041f\u043e\u0438\u0441\u043a / \u043f\u043e\u043a\u0443\u043f\u043a\u0430: {search_url}"
            )

        one_way = self._is_one_way(source_text, dates_text)
        route_label = (
            f"Travelpayouts / Aviasales: \u0441\u0432\u0435\u0436\u0438\u0435 \u0446\u0435\u043d\u044b \u0432 \u043e\u0434\u043d\u0443 \u0441\u0442\u043e\u0440\u043e\u043d\u0443 \u0434\u043b\u044f {normalized_origin} -> {normalized_destination}"
            if one_way
            else f"Travelpayouts / Aviasales: \u0441\u0432\u0435\u0436\u0438\u0435 \u0446\u0435\u043d\u044b \u0434\u043b\u044f {normalized_origin} -> {normalized_destination}"
        )
        lines = [route_label]
        for index, result in enumerate(results[:4], start=1):
            parts = [result.price_text]
            if result.dates:
                parts.append(result.dates)
            if result.note:
                parts.append(result.note)
            lines.append(f"{index}. {' • '.join(parts)}")
        lines.append(f"\u041f\u043e\u0438\u0441\u043a / \u043f\u043e\u043a\u0443\u043f\u043a\u0430: {results[0].url}")
        return "\n".join(lines)

    def search_results(
        self,
        *,
        origin: str,
        destination: str,
        dates_text: str,
        budget_text: str,
        group_size: int,
        source_text: str = "",
    ) -> list[TravelSearchResult]:
        if not self.enabled:
            return []
        normalized_destination = normalized_search_value(destination)
        normalized_origin = normalized_search_value(origin)
        if not normalized_destination:
            return []
        if not normalized_origin:
            return []

        try:
            origin_match = self._resolve_place(normalized_origin)
            destination_match = self._resolve_place(normalized_destination)
            date_range = parse_dates_range(dates_text)
            one_way = self._is_one_way(source_text, dates_text)
            trip_class = self._budget_trip_class(budget_text)
            if date_range:
                offers = self._search_prices_for_dates(
                    origin_code=origin_match.code,
                    destination_code=destination_match.code,
                    start_date=date_range[0].isoformat(),
                    end_date=date_range[1].isoformat(),
                    one_way=one_way,
                    trip_class=trip_class,
                )
                direct_offers = self._search_prices_for_dates(
                    origin_code=origin_match.code,
                    destination_code=destination_match.code,
                    start_date=date_range[0].isoformat(),
                    end_date=date_range[1].isoformat(),
                    one_way=one_way,
                    direct_only=True,
                    trip_class=trip_class,
                )
            else:
                offers = self._search_latest_prices(
                    origin_code=origin_match.code,
                    destination_code=destination_match.code,
                    dates_text=dates_text,
                    one_way=one_way,
                    trip_class=trip_class,
                )
                direct_offers = self._search_latest_prices(
                    origin_code=origin_match.code,
                    destination_code=destination_match.code,
                    dates_text=dates_text,
                    one_way=one_way,
                    direct_only=True,
                    trip_class=trip_class,
                )
            offers = self._merge_offers(offers, direct_offers)
            search_url = self._build_search_url(
                origin_code=origin_match.code,
                destination_code=destination_match.code,
                start_date=date_range[0].isoformat() if date_range else None,
                end_date=date_range[1].isoformat() if date_range and not one_way else None,
                one_way=one_way,
                adults=group_size,
                trip_class=trip_class,
            )
        except TravelpayoutsError as exc:
            return [
                TravelSearchResult(
                    title=f"\u0410\u0432\u0438\u0430\u0431\u0438\u043b\u0435\u0442\u044b {normalized_origin} -> {normalized_destination}",
                    price_text="\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0446\u0435\u043d\u044b \u043f\u0440\u044f\u043c\u043e \u0441\u0435\u0439\u0447\u0430\u0441.",
                    url="https://www.aviasales.ru",
                    source="Travelpayouts / Aviasales",
                    note=f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0430: {exc}",
                )
            ]

        results: list[TravelSearchResult] = []
        for label, offer in self._prioritize_offers(offers)[:4]:
            cabin_label = self._trip_class_label(offer.trip_class)
            transfers = "без пересадок" if offer.number_of_changes == 0 else f"{offer.number_of_changes} перес."
            price_per_person = offer.value
            total_price = price_per_person * max(1, group_size)
            if group_size > 1:
                price_text = f"{price_per_person:,} ₽/чел. (≈ {total_price:,} ₽ за {group_size} чел.)".replace(",", " ")
            else:
                price_text = f"{price_per_person:,} ₽".replace(",", " ")
            results.append(
                TravelSearchResult(
                    title=label,
                    price_text=price_text,
                    url=search_url,
                    source="Travelpayouts / Aviasales",
                    score=0,
                    budget_fit=self._budget_fit_text(price_per_person, budget_text),
                    dates=self._format_offer_dates(offer.depart_date, offer.return_date),
                    note=transfers if not cabin_label else f"{cabin_label}, {transfers}",
                )
            )
        return trim_results(results, limit=4)

    def _build_search_url(
        self,
        *,
        origin_code: str,
        destination_code: str,
        start_date: str | None,
        end_date: str | None,
        one_way: bool = False,
        adults: int = 1,
        trip_class: int = 0,
    ) -> str:
        route_origin = origin_code.strip().upper()
        route_destination = destination_code.strip().upper()
        passengers = max(1, min(int(adults or 1), 9))
        query_params: dict[str, str | int] = {
            "origin_iata": route_origin,
            "destination_iata": route_destination,
            "adults": passengers,
            "trip_class": max(0, min(int(trip_class or 0), 2)),
        }
        if start_date:
            query_params["depart_date"] = start_date
        if end_date and not one_way:
            query_params["return_date"] = end_date
        if one_way:
            query_params["one_way"] = 1
        base_url = "https://www.aviasales.ru/search?" + urllib_parse.urlencode(query_params)
        if self._partner_links and self._partner_links.enabled:
            try:
                return self._partner_links.convert(base_url, sub_id=f"{route_origin}-{route_destination}")
            except Exception:
                return base_url
        return base_url

    @staticmethod
    def _format_offer_dates(depart_date: str, return_date: str) -> str:
        depart = TravelpayoutsFlightProvider._format_single_date(depart_date)
        back = TravelpayoutsFlightProvider._format_single_date(return_date)
        if depart and back:
            return f"{depart} - {back}"
        if depart:
            return depart
        return ""

    @staticmethod
    def _is_one_way(source_text: str, dates_text: str) -> bool:
        lowered = f"{source_text}\n{dates_text}".lower()
        triggers = (
            "в одну сторону",
            "без обратного",
            "без обратного билета",
            "только туда",
            "one way",
            "one-way",
            "oneway",
        )
        return any(trigger in lowered for trigger in triggers)

    @staticmethod
    def _format_single_date(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        normalized = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.strftime("%d.%m")
        except ValueError:
            return raw[:10]

    def _resolve_place(self, term: str) -> PlaceMatch:
        for locale in ("ru", "en"):
            params = [
                ("term", term.strip()),
                ("locale", locale),
                ("types[]", "city"),
                ("types[]", "airport"),
            ]
            url = "https://autocomplete.travelpayouts.com/places2?" + urllib_parse.urlencode(params)
            payload = self._get_json(url)
            if not isinstance(payload, list) or not payload:
                continue

            for item in payload:
                place_type = str(item.get("type") or "")
                code = str(item.get("code") or "").strip().upper()
                name = str(item.get("name") or term).strip()
                if place_type == "city" and code:
                    return PlaceMatch(code=code, name=name, type=place_type)
            for item in payload:
                place_type = str(item.get("type") or "")
                code = str(item.get("code") or "").strip().upper()
                name = str(item.get("name") or term).strip()
                if code:
                    return PlaceMatch(code=code, name=name, type=place_type or "airport")
        raise TravelpayoutsError(f"\u043d\u0435 \u043d\u0430\u0448\u0451\u043b IATA-\u043a\u043e\u0434 \u0434\u043b\u044f '{term}'")

    def _search_prices_for_dates(
        self,
        *,
        origin_code: str,
        destination_code: str,
        start_date: str,
        end_date: str,
        one_way: bool = False,
        direct_only: bool = False,
        trip_class: int = 0,
    ) -> list[FlightOffer]:
        if trip_class > 0:
            trip_duration = None if one_way else max(1, (datetime.fromisoformat(end_date) - datetime.fromisoformat(start_date)).days)
            return self._search_latest_prices(
                origin_code=origin_code,
                destination_code=destination_code,
                dates_text=start_date,
                one_way=one_way,
                direct_only=direct_only,
                trip_class=trip_class,
                exact_depart_date=start_date,
                trip_duration=trip_duration,
            )
        params = [
            ("origin", origin_code),
            ("destination", destination_code),
            ("departure_at", start_date),
            ("sorting", "price"),
            ("direct", "true" if direct_only else "false"),
            ("currency", "rub"),
            ("limit", "20"),
            ("page", "1"),
            ("one_way", "true" if one_way else "false"),
            ("market", "ru"),
            ("token", self._api_key),
        ]
        if not one_way:
            params.insert(3, ("return_at", end_date))
        url = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates?" + urllib_parse.urlencode(params)
        payload = self._get_json(url)
        data = payload.get("data") if isinstance(payload, dict) else None
        return self._parse_offers(data, origin_code, destination_code)

    def _search_latest_prices(
        self,
        *,
        origin_code: str,
        destination_code: str,
        dates_text: str,
        one_way: bool = False,
        direct_only: bool = False,
        trip_class: int = 0,
        exact_depart_date: str | None = None,
        trip_duration: int | None = None,
    ) -> list[FlightOffer]:
        params: list[tuple[str, str]] = [
            ("origin", origin_code),
            ("destination", destination_code),
            ("currency", "rub"),
            ("page", "1"),
            ("limit", "20"),
            ("sorting", "price"),
            ("direct", "true" if direct_only else "false"),
            ("one_way", "true" if one_way else "false"),
            ("market", "ru"),
            ("trip_class", str(max(0, min(int(trip_class or 0), 2)))),
            ("token", self._api_key),
        ]
        date_range = parse_dates_range(dates_text)
        if exact_depart_date:
            params.extend(
                [
                    ("period_type", "day"),
                    ("beginning_of_period", exact_depart_date),
                ]
            )
            if not one_way and trip_duration:
                params.append(("trip_duration", str(max(1, trip_duration))))
        elif date_range:
            start, end = date_range
            params.extend(
                [
                    ("period_type", "month"),
                    ("beginning_of_period", start.replace(day=1).isoformat()),
                    ("trip_duration", str(max(1, (end - start).days))),
                ]
            )

        url = "https://api.travelpayouts.com/aviasales/v3/get_latest_prices?" + urllib_parse.urlencode(params)
        payload = self._get_json(url)
        data = payload.get("data") if isinstance(payload, dict) else None
        return self._parse_offers(data, origin_code, destination_code)

    def _parse_offers(self, data: object, origin_code: str, destination_code: str) -> list[FlightOffer]:
        if not isinstance(data, list):
            return []
        offers: list[FlightOffer] = []
        for item in data:
            try:
                offers.append(
                    FlightOffer(
                        origin=str(item.get("origin") or origin_code),
                        destination=str(item.get("destination") or destination_code),
                        depart_date=str(item.get("departure_at") or item.get("depart_date") or ""),
                        return_date=str(item.get("return_at") or item.get("return_date") or ""),
                        value=int(float(item.get("price") or item.get("value") or 0)),
                        number_of_changes=int(item.get("transfers") or item.get("number_of_changes") or 0),
                        actual=bool(item.get("actual", True)),
                        trip_class=int(item.get("trip_class") or 0),
                    )
                )
            except (TypeError, ValueError):
                continue
        return sorted(
            [offer for offer in offers if offer.value > 0],
            key=lambda offer: (offer.value, offer.number_of_changes, offer.depart_date, offer.return_date),
        )

    def _merge_offers(self, *offer_groups: list[FlightOffer]) -> list[FlightOffer]:
        merged: list[FlightOffer] = []
        seen: set[tuple[int, int, str, str, int]] = set()
        for offers in offer_groups:
            for offer in offers:
                identity = self._offer_identity(offer)
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(offer)
        return sorted(
            merged,
            key=lambda offer: (offer.value, offer.number_of_changes, offer.depart_date, offer.return_date),
        )

    @staticmethod
    def _offer_identity(offer: FlightOffer) -> tuple[int, int, str, str, int]:
        return (offer.value, offer.number_of_changes, offer.depart_date, offer.return_date, offer.trip_class)

    def _prioritize_offers(self, offers: list[FlightOffer]) -> list[tuple[str, FlightOffer]]:
        if not offers:
            return []

        prioritized: list[tuple[str, FlightOffer]] = []
        seen: set[tuple[int, int, str, str, int]] = set()

        cheapest = offers[0]
        prioritized.append(("Самый дешевый", cheapest))
        seen.add(self._offer_identity(cheapest))

        direct_offer = next((offer for offer in offers if offer.number_of_changes == 0), None)
        if direct_offer and self._offer_identity(direct_offer) not in seen:
            prioritized.append(("Самый дешевый прямой", direct_offer))
            seen.add(self._offer_identity(direct_offer))

        for offer in offers:
            identity = self._offer_identity(offer)
            if identity in seen:
                continue
            label = "Еще вариант" if len(prioritized) == 2 else "Еще вариант 2"
            prioritized.append((label, offer))
            seen.add(identity)
            if len(prioritized) >= 4:
                break
        return prioritized

    @staticmethod
    def _normalize_budget_level(budget_text: str) -> str:
        lowered = (budget_text or "").lower()
        if any(phrase in lowered for phrase in ("первый класс", "first class", "не ограничен", "без ограничений", "без лимита")):
            return "первый класс"
        if any(phrase in lowered for phrase in ("бизнес", "business")):
            return "бизнес"
        for label, keywords in BUDGET_HINTS.items():
            if any(keyword in lowered for keyword in keywords):
                return label
        digits = [int(value) for value in "".join(ch if ch.isdigit() else " " for ch in lowered).split()]
        if digits:
            if digits[0] <= 40000:
                return "эконом"
            if digits[0] >= 120000:
                return "первый класс"
            return "бизнес"
        return "бизнес"

    @classmethod
    def _budget_trip_class(cls, budget_text: str) -> int:
        level = cls._normalize_budget_level(budget_text)
        return {"эконом": 0, "бизнес": 1, "первый класс": 2}.get(level, 0)

    @staticmethod
    def _trip_class_label(trip_class: int) -> str:
        return {
            1: "бизнес-класс",
            2: "первый класс",
        }.get(int(trip_class or 0), "")

    @classmethod
    def _budget_fit_text(cls, price_per_person: int, budget_text: str) -> str:
        level = cls._normalize_budget_level(budget_text)
        thresholds = {"эконом": 18000, "бизнес": 40000, "первый класс": 75000}
        limit = thresholds[level]
        if price_per_person <= int(limit * 0.75):
            return f"хорошо вписывается в {level}"
        if price_per_person <= limit:
            return f"вписывается в {level}"
        if price_per_person <= int(limit * 1.25):
            return f"выше бюджета {level}"
        return f"существенно выше бюджета {level}"

    @classmethod
    def _score_offer(cls, price_per_person: int, changes: int, budget_text: str) -> int:
        level = cls._normalize_budget_level(budget_text)
        thresholds = {"эконом": 18000, "бизнес": 40000, "первый класс": 75000}
        limit = thresholds[level]
        score = 10
        if price_per_person > limit:
            score -= 3
        if price_per_person > int(limit * 1.25):
            score -= 2
        score -= min(changes, 3)
        return max(1, min(score, 10))

    def _get_json(self, url: str) -> object:
        try:
            raw = safe_http_get(
                url,
                headers={"X-Access-Token": self._api_key},
                max_retries=2,
                timeout=20,
            )
            raw_str = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            raise TravelpayoutsError(str(exc)) from exc
        try:
            return json.loads(raw_str)
        except json.JSONDecodeError as exc:
            raise TravelpayoutsError("\u043f\u0440\u0438\u0448\u0451\u043b \u043d\u0435\u0432\u0430\u043b\u0438\u0434\u043d\u044b\u0439 JSON") from exc
