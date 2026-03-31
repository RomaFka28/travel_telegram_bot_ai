from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from travel_planner import BUDGET_HINTS
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
    def __init__(self, api_key: str) -> None:
        self._api_key = (api_key or "").strip()

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
        if not destination or destination == "не указано":
            return ""
        if not origin or origin == "не указано":
            return (
                "Билеты: чтобы показать цены через Travelpayouts, нужен город вылета.\n"
                "Подсказка: напишите в чате что-то вроде «летим из Томска» или обновите поездку через /plan."
            )

        try:
            origin_match = self._resolve_place(origin)
            destination_match = self._resolve_place(destination)
            offers = self._search_latest_prices(
                origin_code=origin_match.code,
                destination_code=destination_match.code,
                dates_text=dates_text,
            )
        except TravelpayoutsError as exc:
            return f"Билеты: не удалось получить данные Travelpayouts ({exc})."

        if not offers:
            return (
                f"Билеты: Travelpayouts пока не вернул свежих цен по направлению {origin} -> {destination}. "
                "Откройте ссылку на поиск, чтобы проверить актуальные варианты."
            )

        lines = [
            f"Travelpayouts / Aviasales: свежие цены для {origin_match.name} -> {destination_match.name}",
        ]
        for index, offer in enumerate(offers[:3], start=1):
            budget_fit = self._budget_fit_text(offer.value, budget_text)
            transfers = "прямой" if offer.number_of_changes == 0 else f"{offer.number_of_changes} перес."
            total = offer.value * max(1, group_size)
            score = self._score_offer(offer.value, offer.number_of_changes, budget_text)
            lines.append(
                f"{index}. {offer.value:,} ₽/чел. ({total:,} ₽ на {max(1, group_size)} чел.), "
                f"{offer.depart_date} -> {offer.return_date or 'one-way'}, {transfers}, "
                f"оценка {score}/10, {budget_fit}".replace(",", " ")
            )
        lines.append("Цены из кэша Aviasales, для покупки переходите по ссылке на поиск ниже.")
        return "\n".join(lines)

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
            raise TravelpayoutsError(f"не нашёл IATA-код для '{term}'")

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
        raise TravelpayoutsError(f"не нашёл IATA-код для '{term}'")

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
        if not isinstance(data, list):
            return []

        offers: list[FlightOffer] = []
        for item in data:
            try:
                offers.append(
                    FlightOffer(
                        origin=str(item.get("origin") or origin_code),
                        destination=str(item.get("destination") or destination_code),
                        depart_date=str(item.get("depart_date") or ""),
                        return_date=str(item.get("return_date") or ""),
                        value=int(float(item.get("value") or 0)),
                        number_of_changes=int(item.get("number_of_changes") or 0),
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
                return "эконом"
            if digits[0] >= 120000:
                return "комфорт"
        return "средний"

    @classmethod
    def _budget_fit_text(cls, price_per_person: int, budget_text: str) -> str:
        level = cls._normalize_budget_level(budget_text)
        thresholds = {
            "эконом": 18000,
            "средний": 32000,
            "комфорт": 55000,
        }
        limit = thresholds[level]
        if price_per_person <= int(limit * 0.75):
            return f"хорошо вписывается в {level} бюджет"
        if price_per_person <= limit:
            return f"вписывается в {level} бюджет"
        if price_per_person <= int(limit * 1.25):
            return f"на грани для {level} бюджета"
        return f"дороже ожидаемого для {level} бюджета"

    @classmethod
    def _score_offer(cls, price_per_person: int, changes: int, budget_text: str) -> int:
        level = cls._normalize_budget_level(budget_text)
        thresholds = {
            "эконом": 18000,
            "средний": 32000,
            "комфорт": 55000,
        }
        limit = thresholds[level]
        score = 10
        if price_per_person > limit:
            score -= 3
        if price_per_person > int(limit * 1.25):
            score -= 2
        score -= min(changes, 3)
        return max(1, min(score, 10))

    @staticmethod
    def _get_json(url: str) -> object:
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise TravelpayoutsError(str(exc)) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TravelpayoutsError("пришёл невалидный JSON") from exc
