from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from database import Database
from travel_locale import build_entry_requirements_text, detect_route_locale
from travel_links import build_links_text, build_structured_link_results, detect_link_needs
from travel_result_models import TravelSearchResult, serialize_needs, serialize_results, trim_results
from travelpayouts_flights import TravelpayoutsFlightProvider
from travel_planner import TravelPlanner
from value_normalization import normalized_search_value
from weather_service import WeatherError, fetch_weather_summary

if TYPE_CHECKING:
    from bot.group_chat_analyzer import ChatSignal


class TripService:
    def __init__(
        self,
        database: Database,
        planner: TravelPlanner,
        flight_provider: TravelpayoutsFlightProvider | None = None,
    ) -> None:
        self._db = database
        self._planner = planner
        self._flight_provider = flight_provider

    def _request_from_trip_row(self, trip) -> dict[str, str | int]:
        return {
            "title": trip["title"] or "\u041d\u043e\u0432\u0430\u044f \u043f\u043e\u0435\u0437\u0434\u043a\u0430",
            "destination": trip["destination"] or "",
            "origin": trip["origin"] or "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e",
            "dates_text": trip["dates_text"] or "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u044b",
            "days_count": int(trip["days_count"] or 3),
            "group_size": int(trip["group_size"] or 2),
            "budget_text": trip["budget_text"] or "\u0441\u0440\u0435\u0434\u043d\u0438\u0439",
            "interests_text": trip["interests_text"] or "\u0433\u043e\u0440\u043e\u0434, \u0435\u0434\u0430",
            "notes": trip["notes"] or "",
            "source_prompt": trip["source_prompt"] or "",
        }

    def _build_short_summary(self, request, detected_needs: list[str], flight_results: list[TravelSearchResult]) -> str:
        lines = [
            f"\u041a\u0443\u0434\u0430: {request.destination or '\u0443\u0442\u043e\u0447\u043d\u044f\u0435\u0442\u0441\u044f'}",
            f"\u041a\u043e\u0433\u0434\u0430: {request.dates_text or '\u0443\u0442\u043e\u0447\u043d\u044f\u0435\u0442\u0441\u044f'}",
            f"\u041b\u044e\u0434\u0435\u0439: {request.group_size}",
            f"\u0411\u044e\u0434\u0436\u0435\u0442: {request.budget_text or '\u0443\u0442\u043e\u0447\u043d\u044f\u0435\u0442\u0441\u044f'}",
        ]
        if detected_needs:
            labels = {
                "tickets": "\u0431\u0438\u043b\u0435\u0442\u044b",
                "housing": "\u0436\u0438\u043b\u044c\u0451",
                "excursions": "\u044d\u043a\u0441\u043a\u0443\u0440\u0441\u0438\u0438",
                "road": "\u0434\u043e\u0440\u043e\u0433\u0430",
                "car_rental": "\u0430\u0440\u0435\u043d\u0434\u0430 \u0430\u0432\u0442\u043e",
                "bike_rental": "\u0430\u0440\u0435\u043d\u0434\u0430 \u0431\u0430\u0439\u043a\u0430",
                "transfers": "\u0442\u0440\u0430\u043d\u0441\u0444\u0435\u0440\u044b",
            }
            lines.append("\u041e\u0431\u0441\u0443\u0436\u0434\u0430\u043b\u0438: " + ", ".join(labels.get(need, need) for need in detected_needs))
        if flight_results:
            lines.append(f"\u041b\u0443\u0447\u0448\u0438\u0439 \u0431\u0438\u043b\u0435\u0442: {flight_results[0].price_text}")
        return "\n".join(lines)

    def _collect_structured_results(self, request) -> tuple[list[str], dict[str, list[TravelSearchResult]], str, str]:
        context_text = f"{request.source_prompt}\n{request.notes}".strip()
        detected_needs = sorted(detect_link_needs(context_text))
        structured = build_structured_link_results(
            request.destination,
            request.dates_text,
            request.origin,
            context_text=context_text,
        )

        flight_results: list[TravelSearchResult] = []
        if self._flight_provider:
            flight_results = self._flight_provider.search_results(
                origin=request.origin,
                destination=request.destination,
                dates_text=request.dates_text,
                budget_text=request.budget_text,
                group_size=request.group_size,
                source_text=context_text,
            )
        if flight_results:
            structured["tickets"] = trim_results(flight_results)

        housing_results = trim_results(structured.get("housing", []))
        activity_results = trim_results(structured.get("excursions", []))
        transport_results = trim_results(structured.get("road", []) + structured.get("transfers", []))
        rental_results = trim_results(structured.get("car_rental", []) + structured.get("bike_rental", []))

        tickets_text = ""
        if self._flight_provider:
            tickets_text = self._flight_provider.build_ticket_snapshot(
                origin=request.origin,
                destination=request.destination,
                dates_text=request.dates_text,
                budget_text=request.budget_text,
                group_size=request.group_size,
                source_text=context_text,
            )
        links_text = build_links_text(
            request.destination,
            request.dates_text,
            request.origin,
            context_text=context_text,
        )
        return detected_needs, {
            "flight_results": trim_results(flight_results),
            "housing_results": housing_results,
            "activity_results": activity_results,
            "transport_results": transport_results,
            "rental_results": rental_results,
        }, tickets_text, links_text

    def _build_open_questions(self, request, detected_needs: list[str], structured_results: dict[str, list[TravelSearchResult]]) -> str:
        questions: list[str] = []
        route_locale = detect_route_locale(request.destination, request.origin)
        normalized_interests = (request.interests_text or "").strip().lower()
        has_specific_interests = normalized_interests not in {"", "не указаны", "не указано"}
        if not normalized_search_value(request.destination):
            questions.append("Уточнить направление поездки.")
        if not normalized_search_value(request.dates_text):
            questions.append("Выбрать точные даты, чтобы собрать жильё и дорогу точнее.")
        if "tickets" in detected_needs and not normalized_search_value(request.origin):
            questions.append("Уточнить город вылета для билетов.")
        if "housing" in detected_needs:
            questions.append("Подтвердить формат жилья: отель, квартира или дом.")
        if "car_rental" in detected_needs:
            questions.append("Нужна ли аренда авто на все дни или только на 1 день.")
        if "bike_rental" in detected_needs:
            questions.append("Понять, нужен ли байк/скутер всем или только части группы.")
        if "road" in detected_needs and not normalized_search_value(request.dates_text):
            questions.append("Подтвердить день выезда, чтобы подобрать дорогу без лишних пересадок.")
        if "excursions" in detected_needs and not has_specific_interests:
            questions.append("Выбрать тип экскурсий: прогулки, музеи, гастро или природа.")
        if route_locale.is_international:
            questions.append("Уточнить гражданство или тип паспорта, чтобы проверить визовые и въездные правила.")

        raw_text = f"{request.source_prompt}\n{request.notes}"
        for line in raw_text.splitlines():
            cleaned = line.strip()
            if "?" in cleaned and cleaned not in questions:
                questions.append(cleaned)
        unique_questions: list[str] = []
        seen: set[str] = set()
        for question in questions:
            key = question.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            unique_questions.append(question)
        return "\n".join(f"• {question}" for question in unique_questions[:6])

    def _build_trip_payload(self, request, plan, *, notes_override: str | None = None) -> dict[str, object]:
        notes = request.notes if notes_override is None else notes_override
        effective_request = self._planner.build_request_from_fields(
            title=request.title,
            destination=request.destination,
            origin=request.origin,
            dates_text=request.dates_text,
            days_count=request.days_count,
            group_size=request.group_size,
            budget_text=request.budget_text,
            interests_text=request.interests_text,
            notes=notes,
            source_prompt=request.source_prompt,
        )
        detected_needs, structured_results, tickets_text, links_text = self._collect_structured_results(effective_request)
        entry_requirements_text = build_entry_requirements_text(effective_request.destination, effective_request.origin)
        return {
            "title": effective_request.title,
            "destination": effective_request.destination,
            "origin": effective_request.origin,
            "dates_text": effective_request.dates_text,
            "days_count": effective_request.days_count,
            "group_size": effective_request.group_size,
            "budget_text": effective_request.budget_text,
            "interests_text": effective_request.interests_text,
            "notes": notes,
            "source_prompt": effective_request.source_prompt,
            "context_text": plan.context_text,
            "itinerary_text": plan.itinerary_text,
            "logistics_text": plan.logistics_text,
            "stay_text": plan.stay_text,
            "alternatives_text": plan.alternatives_text,
            "budget_breakdown_text": plan.budget_breakdown_text,
            "budget_total_text": plan.budget_total_text,
            "tickets_text": tickets_text,
            "links_text": links_text,
            "entry_requirements_text": entry_requirements_text,
            "summary_short_text": self._build_short_summary(effective_request, detected_needs, structured_results["flight_results"]),
            "flight_results": serialize_results(structured_results["flight_results"]),
            "housing_results": serialize_results(structured_results["housing_results"]),
            "activity_results": serialize_results(structured_results["activity_results"]),
            "transport_results": serialize_results(structured_results["transport_results"]),
            "rental_results": serialize_results(structured_results["rental_results"]),
            "detected_needs": serialize_needs(detected_needs),
            "results_updated_at": datetime.utcnow().isoformat(timespec="seconds"),
            "open_questions_text": self._build_open_questions(effective_request, detected_needs, structured_results),
            "status": "active",
        }

    def _merge_edit_request(self, trip: dict, edit_text: str):
        current = self._request_from_trip_row(trip)
        destination = self._planner._extract_destination(edit_text) or str(current["destination"])
        origin = self._planner._extract_origin(edit_text) or str(current["origin"])
        days_count = self._planner._extract_days_count(edit_text) if self._has_days_hint(edit_text) else int(current["days_count"])
        dates_text = self._planner._extract_dates(edit_text) if self._has_dates_hint(edit_text) else str(current["dates_text"])
        budget_text = self._planner._extract_budget(edit_text) if self._has_budget_hint(edit_text) else str(current["budget_text"])
        interests = self._planner._extract_interests(edit_text)
        interests_text = ", ".join(interests) if interests else str(current["interests_text"])
        source_prompt = f"{current['source_prompt']}\n\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435: {edit_text}".strip()
        return self._planner.build_request_from_fields(
            title=f"{destination} \u2022 {days_count} \u0434\u043d. \u2022 {int(current['group_size'])} \u0447\u0435\u043b.",
            destination=destination,
            origin=origin,
            dates_text=dates_text,
            days_count=days_count,
            group_size=int(current["group_size"]),
            budget_text=budget_text,
            interests_text=interests_text,
            notes=str(current["notes"]),
            source_prompt=source_prompt,
        )

    def _rebuild_trip(self, trip_id: int) -> None:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return
        request = self._planner.build_request_from_fields(**self._request_from_trip_row(trip))
        plan = self._planner.generate_plan(request)
        self._db.update_trip_fields(trip_id, self._build_trip_payload(request, plan, notes_override=trip["notes"] or ""))

    def _refresh_weather_for_trip(self, trip_id: int) -> None:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return
        destination = trip["destination"] or ""
        dates_text = trip["dates_text"] or ""
        try:
            summary = fetch_weather_summary(destination, dates_text)
        except WeatherError:
            summary = None
        if summary:
            self._db.update_trip_fields(
                trip_id,
                {
                    "weather_text": summary,
                    "weather_updated_at": datetime.utcnow().isoformat(timespec="seconds"),
                },
            )

    def auto_draft_from_signal(
        self,
        chat_id: int,
        created_by: int | None,
        signal: "ChatSignal",
    ) -> int | None:
        if not signal.destination:
            return None
        interests_text = ", ".join(signal.interests) if signal.interests else "\u0433\u043e\u0440\u043e\u0434, \u0435\u0434\u0430"
        request = self._planner.build_request_from_fields(
            title=f"{signal.destination} \u2022 3 \u0434\u043d. \u2022 2 \u0447\u0435\u043b.",
            destination=signal.destination,
            origin=signal.origin or "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e",
            dates_text=signal.dates_text or "\u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u044b",
            days_count=3,
            group_size=max(2, len(signal.participants_mentioned)) if signal.participants_mentioned else 2,
            budget_text=signal.budget_hint or "\u0441\u0440\u0435\u0434\u043d\u0438\u0439",
            interests_text=interests_text,
            notes=signal.raw_text,
            source_prompt=signal.raw_text,
        )
        plan = self._planner.generate_plan(request)
        payload = self._build_trip_payload(request, plan)
        trip_id = self._db.create_trip(chat_id, created_by, payload)
        self._db.set_selected_trip(chat_id, trip_id)
        self._refresh_weather_for_trip(trip_id)
        return trip_id

    @staticmethod
    def _has_days_hint(text: str) -> bool:
        import re

        return bool(
            re.search(
                r"\b\d{1,2}\s*(?:-|\u2013|\u2014|\u0434\u043e)?\s*\d{0,2}\s*(?:\u0434\u043d(?:\u044f|\u0435\u0439)?|\u0441\u0443\u0442(?:\u043e\u043a)?|\u043d\u043e\u0447(?:\u044c|\u0438|\u0435\u0439)?)",
                text,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_budget_hint(text: str) -> bool:
        lowered = text.lower()
        return any(
            keyword in lowered
            for keyword in (
                "\u0431\u044e\u0434\u0436",
                "\u044d\u043a\u043e\u043d\u043e\u043c",
                "\u0434\u0435\u0448\u0435\u0432",
                "\u0441\u0440\u0435\u0434\u043d",
                "\u043a\u043e\u043c\u0444\u043e\u0440\u0442",
                "\u0434\u043e ",
            )
        )

    @staticmethod
    def _has_dates_hint(text: str) -> bool:
        import re

        lowered = text.lower()
        return any(
            keyword in lowered
            for keyword in (
                "\u044f\u043d\u0432\u0430\u0440",
                "\u0444\u0435\u0432\u0440\u0430\u043b",
                "\u043c\u0430\u0440\u0442",
                "\u0430\u043f\u0440\u0435\u043b",
                "\u043c\u0430\u0439",
                "\u0438\u044e\u043d",
                "\u0438\u044e\u043b",
                "\u0430\u0432\u0433\u0443\u0441\u0442",
                "\u0441\u0435\u043d\u0442\u044f\u0431\u0440",
                "\u043e\u043a\u0442\u044f\u0431\u0440",
                "\u043d\u043e\u044f\u0431\u0440",
                "\u0434\u0435\u043a\u0430\u0431\u0440",
            )
        ) or bool(re.search(r"\b\d{1,2}\s*(?:-|\u2013|\u2014|\u0434\u043e)\s*\d{1,2}\b", text))
