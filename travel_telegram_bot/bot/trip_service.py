from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from database import Database
from travel_planner import TravelPlanner
from weather_service import WeatherError, fetch_weather_summary

if TYPE_CHECKING:
    from bot.group_chat_analyzer import ChatSignal


class TripService:
    def __init__(self, database: Database, planner: TravelPlanner) -> None:
        self._db = database
        self._planner = planner

    def _request_from_trip_row(self, trip) -> dict[str, str | int]:
        return {
            "title": trip["title"] or "Новая поездка",
            "destination": trip["destination"] or "",
            "origin": trip["origin"] or "не указано",
            "dates_text": trip["dates_text"] or "не указаны",
            "days_count": int(trip["days_count"] or 3),
            "group_size": int(trip["group_size"] or 2),
            "budget_text": trip["budget_text"] or "средний",
            "interests_text": trip["interests_text"] or "город, еда",
            "notes": trip["notes"] or "",
            "source_prompt": trip["source_prompt"] or "",
        }

    def _build_trip_payload(self, request, plan, *, notes_override: str | None = None) -> dict[str, object]:
        notes = request.notes if notes_override is None else notes_override
        return {
            "title": request.title,
            "destination": request.destination,
            "origin": request.origin,
            "dates_text": request.dates_text,
            "days_count": request.days_count,
            "group_size": request.group_size,
            "budget_text": request.budget_text,
            "interests_text": request.interests_text,
            "notes": notes,
            "source_prompt": request.source_prompt,
            "context_text": plan.context_text,
            "itinerary_text": plan.itinerary_text,
            "logistics_text": plan.logistics_text,
            "stay_text": plan.stay_text,
            "alternatives_text": plan.alternatives_text,
            "budget_breakdown_text": plan.budget_breakdown_text,
            "budget_total_text": plan.budget_total_text,
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
        source_prompt = f"{current['source_prompt']}\nИзменение: {edit_text}".strip()
        return self._planner.build_request_from_fields(
            title=f"{destination} • {days_count} дн. • {int(current['group_size'])} чел.",
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
        """
        Creates a minimal trip from a ChatSignal.
        Returns trip_id or None if destination is missing.
        """
        if not signal.destination:
            return None
        interests_text = ", ".join(signal.interests) if signal.interests else "город, еда"
        request = self._planner.build_request_from_fields(
            title=f"{signal.destination} • 3 дн. • 2 чел.",
            destination=signal.destination,
            origin="не указано",
            dates_text=signal.dates_text or "не указаны",
            days_count=3,
            group_size=max(2, len(signal.participants_mentioned)) if signal.participants_mentioned else 2,
            budget_text=signal.budget_hint or "средний",
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
                "бюдж",
                "эконом",
                "дешев",
                "средн",
                "комфорт",
                "до ",
            )
        )

    @staticmethod
    def _has_dates_hint(text: str) -> bool:
        import re

        lowered = text.lower()
        return any(
            keyword in lowered
            for keyword in (
                "январ",
                "феврал",
                "март",
                "апрел",
                "май",
                "июн",
                "июл",
                "август",
                "сентябр",
                "октябр",
                "ноябр",
                "декабр",
            )
        ) or bool(re.search(r"\b\d{1,2}\s*(?:-|\u2013|\u2014|\u0434\u043e)\s*\d{1,2}\b", text))
