from __future__ import annotations

import json
from datetime import datetime
import urllib.parse
import urllib.request
from dataclasses import dataclass

from travel_result_models import TravelSearchResult, trim_results
from travel_planner import BUDGET_HINTS
from travelpayouts_partner_links import TravelpayoutsPartnerLinksClient
from weather_service import _parse_dates_range


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
    ) -> str:
        if not self.enabled:
            return ""
        if not destination or destination == "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e":
            return ""
        if not origin or origin == "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e":
            return (
                "\u0411\u0438\u043b\u0435\u0442\u044b: \u0447\u0442\u043e\u0431\u044b \u043f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0446\u0435\u043d\u044b \u0447\u0435\u0440\u0435\u0437 Travelpayouts, \u043d\u0443\u0436\u0435\u043d \u0433\u043e\u0440\u043e\u0434 \u0432\u044b\u043b\u0435\u0442\u0430.\n"
                "\u041f\u043e\u0434\u0441\u043a\u0430\u0437\u043a\u0430: \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u0432 \u0447\u0430\u0442\u0435 \u0447\u0442\u043e-\u0442\u043e \u0432\u0440\u043e\u0434\u0435 \u00ab\u043b\u0435\u0442\u0438\u043c \u0438\u0437 \u0422\u043e\u043c\u0441\u043a\u0430\u00bb \u0438\u043b\u0438 \u043e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u043f\u043e\u0435\u0437\u0434\u043a\u0443 \u0447\u0435\u0440\u0435\u0437 /plan."
            )

        results = self.search_results(
            origin=origin,
            destination=destination,
            dates_text=dates_text,
            budget_text=budget_text,
            group_size=group_size,
        )
        if not results:
            try:
                origin_match = self._resolve_place(origin)
                destination_match = self._resolve_place(destination)
                date_range = _parse_dates_range(dates_text)
                search_url = self._build_search_url(
                    origin_code=origin_match.code,
                    destination_code=destination_match.code,
                    start_date=date_range[0].isoformat() if date_range else None,
                    end_date=date_range[1].isoformat() if date_range else None,
                )
            except TravelpayoutsError:
                search_url = "https://www.aviasales.ru"
            return (
                f"\u0411\u0438\u043b\u0435\u0442\u044b: Travelpayouts \u043f\u043e\u043a\u0430 \u043d\u0435 \u0432\u0435\u0440\u043d\u0443\u043b \u0441\u0432\u0435\u0436\u0438\u0445 \u0446\u0435\u043d \u043f\u043e \u043d\u0430\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044e {origin} -> {destination}.\n"
                f"\u041f\u043e\u0438\u0441\u043a / \u043f\u043e\u043a\u0443\u043f\u043a\u0430: {search_url}"
            )

        lines = [f"Travelpayouts / Aviasales: \u0441\u0432\u0435\u0436\u0438\u0435 \u0446\u0435\u043d\u044b \u0434\u043b\u044f {origin} -> {destination}"]
        for index, result in enumerate(results[:3], start=1):
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
    ) -> list[TravelSearchResult]:
        if not self.enabled:
            return []
        if not destination or destination == "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e":
            return []
        if not origin or origin == "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e":
            return []

        try:
            origin_match = self._resolve_place(origin)
            destination_match = self._resolve_place(destination)
            date_range = _parse_dates_range(dates_text)
            if date_range:
                offers = self._search_prices_for_dates(
                    origin_code=origin_match.code,
                    destination_code=destination_match.code,
                    start_date=date_range[0].isoformat(),
                    end_date=date_range[1].isoformat(),
                )
            else:
                offers = self._search_latest_prices(
                    origin_code=origin_match.code,
                    destination_code=destination_match.code,
                    dates_text=dates_text,
                )
            search_url = self._build_search_url(
                origin_code=origin_match.code,
                destination_code=destination_match.code,
                start_date=date_range[0].isoformat() if date_range else None,
                end_date=date_range[1].isoformat() if date_range else None,
            )
        except TravelpayoutsError as exc:
            return [
                TravelSearchResult(
                    title=f"\u0410\u0432\u0438\u0430\u0431\u0438\u043b\u0435\u0442\u044b {origin} -> {destination}",
                    price_text="\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0446\u0435\u043d\u044b \u043f\u0440\u044f\u043c\u043e \u0441\u0435\u0439\u0447\u0430\u0441.",
                    url="https://www.aviasales.ru",
                    source="Travelpayouts / Aviasales",
                    note=f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0430: {exc}",
                )
            ]

        results: list[TravelSearchResult] = []
        for offer in offers[:3]:
            transfers = "\u043f\u0440\u044f\u043c\u043e\u0439" if offer.number_of_changes == 0 else f"{offer.number_of_changes} \u043f\u0435\u0440\u0435\u0441."
            score = self._score_offer(offer.value, offer.number_of_changes, budget_text)
            results.append(
                TravelSearchResult(
                    title=f"{origin_match.name} -> {destination_match.name}",
                    price_text=f"{offer.value:,} \u20bd/\u0447\u0435\u043b.".replace(",", " "),
                    url=search_url,
                    source="Travelpayouts / Aviasales",
                    score=score,
                    budget_fit="",
                    dates=self._format_offer_dates(offer.depart_date, offer.return_date),
                    note=f"{transfers}, \u043e\u0446\u0435\u043d\u043a\u0430 {score}/10",
                )
            )
        return trim_results(results)

    def _build_search_url(
        self,
        *,
        origin_code: str,
        destination_code: str,
        start_date: str | None,
        end_date: str | None,
    ) -> str:
        route_origin = origin_code.strip().upper()
        route_destination = destination_code.strip().upper()
        if start_date and end_date:
            base_url = (
                "https://www.aviasales.ru/search/"
                f"{route_origin}{start_date[8:10]}{start_date[5:7]}"
                f"{route_destination}{end_date[8:10]}{end_date[5:7]}1"
            )
        else:
            base_url = f"https://www.aviasales.ru/search/{route_origin}{route_destination}1"
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
        params = [
            ("term", term.strip()),
            ("locale", "ru"),
            ("types[]", "city"),
            ("types[]", "airport"),
        ]
        url = "https://autocomplete.travelpayouts.com/places2?" + urllib.parse.urlencode(params)
        payload = self._get_json(url)
        if not isinstance(payload, list) or not payload:
            raise TravelpayoutsError(f"\u043d\u0435 \u043d\u0430\u0448\u0451\u043b IATA-\u043a\u043e\u0434 \u0434\u043b\u044f '{term}'")

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
    ) -> list[FlightOffer]:
        params = [
            ("origin", origin_code),
            ("destination", destination_code),
            ("departure_at", start_date),
            ("return_at", end_date),
            ("sorting", "price"),
            ("direct", "false"),
            ("currency", "rub"),
            ("limit", "5"),
            ("page", "1"),
            ("one_way", "false"),
            ("market", "ru"),
            ("token", self._api_key),
        ]
        url = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates?" + urllib.parse.urlencode(params)
        payload = self._get_json(url)
        data = payload.get("data") if isinstance(payload, dict) else None
        return self._parse_offers(data, origin_code, destination_code)

    def _search_latest_prices(self, *, origin_code: str, destination_code: str, dates_text: str) -> list[FlightOffer]:
        params: list[tuple[str, str]] = [
            ("origin", origin_code),
            ("destination", destination_code),
            ("currency", "rub"),
            ("page", "1"),
            ("limit", "5"),
            ("sorting", "price"),
            ("one_way", "false"),
            ("market", "ru"),
            ("token", self._api_key),
        ]
        date_range = _parse_dates_range(dates_text)
        if date_range:
            start, end = date_range
            params.extend(
                [
                    ("period_type", "month"),
                    ("beginning_of_period", start.replace(day=1).isoformat()),
                    ("trip_duration", str(max(1, (end - start).days))),
                ]
            )

        url = "https://api.travelpayouts.com/aviasales/v3/get_latest_prices?" + urllib.parse.urlencode(params)
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
                    )
                )
            except (TypeError, ValueError):
                continue
        return [offer for offer in offers if offer.value > 0]

    @staticmethod
    def _normalize_budget_level(budget_text: str) -> str:
        lowered = (budget_text or "").lower()
        for label, keywords in BUDGET_HINTS.items():
            if any(keyword in lowered for keyword in keywords):
                return label
        digits = [int(value) for value in "".join(ch if ch.isdigit() else " " for ch in lowered).split()]
        if digits:
            if digits[0] <= 40000:
                return "\u044d\u043a\u043e\u043d\u043e\u043c"
            if digits[0] >= 120000:
                return "\u043a\u043e\u043c\u0444\u043e\u0440\u0442"
        return "\u0441\u0440\u0435\u0434\u043d\u0438\u0439"

    @classmethod
    def _budget_fit_text(cls, price_per_person: int, budget_text: str) -> str:
        level = cls._normalize_budget_level(budget_text)
        thresholds = {"\u044d\u043a\u043e\u043d\u043e\u043c": 18000, "\u0441\u0440\u0435\u0434\u043d\u0438\u0439": 32000, "\u043a\u043e\u043c\u0444\u043e\u0440\u0442": 55000}
        limit = thresholds[level]
        if price_per_person <= int(limit * 0.75):
            return f"\u0445\u043e\u0440\u043e\u0448\u043e \u0432\u043f\u0438\u0441\u044b\u0432\u0430\u0435\u0442\u0441\u044f \u0432 {level} \u0431\u044e\u0434\u0436\u0435\u0442"
        if price_per_person <= limit:
            return f"\u0432\u043f\u0438\u0441\u044b\u0432\u0430\u0435\u0442\u0441\u044f \u0432 {level} \u0431\u044e\u0434\u0436\u0435\u0442"
        if price_per_person <= int(limit * 1.25):
            return f"\u043d\u0430 \u0433\u0440\u0430\u043d\u0438 \u0434\u043b\u044f {level} \u0431\u044e\u0434\u0436\u0435\u0442\u0430"
        return f"\u0434\u043e\u0440\u043e\u0436\u0435 \u043e\u0436\u0438\u0434\u0430\u0435\u043c\u043e\u0433\u043e \u0434\u043b\u044f {level} \u0431\u044e\u0434\u0436\u0435\u0442\u0430"

    @classmethod
    def _score_offer(cls, price_per_person: int, changes: int, budget_text: str) -> int:
        level = cls._normalize_budget_level(budget_text)
        thresholds = {"\u044d\u043a\u043e\u043d\u043e\u043c": 18000, "\u0441\u0440\u0435\u0434\u043d\u0438\u0439": 32000, "\u043a\u043e\u043c\u0444\u043e\u0440\u0442": 55000}
        limit = thresholds[level]
        score = 10
        if price_per_person > limit:
            score -= 3
        if price_per_person > int(limit * 1.25):
            score -= 2
        score -= min(changes, 3)
        return max(1, min(score, 10))

    def _get_json(self, url: str) -> object:
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"X-Access-Token": self._api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise TravelpayoutsError(str(exc)) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TravelpayoutsError("\u043f\u0440\u0438\u0448\u0451\u043b \u043d\u0435\u0432\u0430\u043b\u0438\u0434\u043d\u044b\u0439 JSON") from exc
