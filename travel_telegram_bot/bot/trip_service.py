from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from database import Database
from travel_locale import RouteLocale, detect_route_locale
from travel_links import build_links_text, build_structured_link_results, detect_link_needs, _parse_date_range
from travel_result_models import TravelSearchResult, serialize_needs, serialize_results, trim_results
from travelpayouts_flights import TravelpayoutsFlightProvider
from travel_planner import TravelPlanner
from trip_utils import has_budget_hint, has_dates_hint, has_days_hint
from value_normalization import normalized_search_value, truncate_source_prompt
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
            "budget_text": trip["budget_text"] or "\u0411\u0438\u0437\u043d\u0435\u0441",
            "interests_text": trip["interests_text"] or "\u0433\u043e\u0440\u043e\u0434, \u0435\u0434\u0430",
            "notes": trip["notes"] or "",
            "source_prompt": trip["source_prompt"] or "",
        }

    def _build_short_summary(
        self,
        request,
        detected_needs: list[str],
        flight_results: list[TravelSearchResult],
        housing_results: list[TravelSearchResult],
    ) -> str:
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
        if housing_results:
            lines.append(f"\u041b\u0443\u0447\u0448\u0435\u0435 \u0436\u0438\u043b\u044c\u0451: {housing_results[0].price_text}")
        return "\n".join(lines)

    def _collect_structured_results(self, request) -> tuple[list[str], dict[str, list[TravelSearchResult]], str, str]:
        context_text = f"{request.source_prompt}\n{request.notes}".strip()
        detected_needs = sorted(list(detect_link_needs(context_text)))
        structured = build_structured_link_results(
            request.destination,
            request.dates_text,
            request.origin,
            days_count=request.days_count,
            group_size=request.group_size,
            context_text=context_text,
            budget_text=request.budget_text,
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
        if not flight_results:
            flight_results = trim_results(structured.get("tickets", []))
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
            days_count=request.days_count,
            group_size=request.group_size,
            context_text=context_text,
            budget_text=request.budget_text,
        )
        return detected_needs, {
            "flight_results": trim_results(flight_results),
            "housing_results": housing_results,
            "activity_results": activity_results,
            "transport_results": transport_results,
            "rental_results": rental_results,
        }, tickets_text, links_text

    def _build_open_questions(
        self,
        request,
        detected_needs: list[str],
        structured_results: dict[str, list[TravelSearchResult]],
        *,
        route_locale: RouteLocale | None = None,
    ) -> str:
        questions: list[str] = []
        resolved_route_locale = route_locale or detect_route_locale(request.destination, request.origin)
        normalized_interests = (request.interests_text or "").strip().lower()
        has_specific_interests = normalized_interests not in {"", "не указаны", "не указано"}
        if not normalized_search_value(request.destination):
            questions.append("Уточнить направление поездки.")
        if not normalized_search_value(request.dates_text):
            questions.append("Выбрать точные даты, чтобы собрать жильё и дорогу точнее.")
        if "tickets" in detected_needs and not normalized_search_value(request.origin):
            questions.append("Уточнить город вылета для билетов.")
        if "housing" in detected_needs:
            # Check if housing type is already specified in the request
            context_lower = (request.source_prompt + " " + (request.notes or "")).lower()
            has_housing_type = any(
                kw in context_lower
                for kw in ("отел", "квартир", "апарт", "дом", "студи", "хостел", "гостиниц")
            )
            if not has_housing_type:
                questions.append("Подтвердить формат жилья: отель, квартира или дом.")
        if "car_rental" in detected_needs:
            questions.append("Нужна ли аренда авто на все дни или только на 1 день.")
        if "bike_rental" in detected_needs:
            questions.append("Понять, нужен ли байк/скутер всем или только части группы.")
        if "road" in detected_needs and not normalized_search_value(request.dates_text):
            questions.append("Подтвердить день выезда, чтобы подобрать дорогу без лишних пересадок.")
        if "excursions" in detected_needs and not has_specific_interests:
            questions.append("Выбрать тип экскурсий: прогулки, музеи, гастро или природа.")
        if resolved_route_locale.is_international:
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

    def _build_entry_notice(self, request, *, route_locale: RouteLocale | None = None) -> str:
        resolved_route_locale = route_locale or detect_route_locale(request.destination, request.origin)
        if not resolved_route_locale.is_international:
            return ""
        origin_country = resolved_route_locale.origin_country or "страны выезда"
        destination_country = resolved_route_locale.destination_country or "страны назначения"
        return "\n".join(
            [
                f"По въезду на маршруте {origin_country} → {destination_country} нужен один базовый ответ.",
                "Напишите, какое у вас гражданство и по какому документу планируете лететь.",
                "Этого хватит, чтобы понять, можно ли въезжать сейчас и что ещё понадобится.",
            ]
        )

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
            language_code=getattr(request, "language_code", "ru"),
        )
        detected_needs, structured_results, tickets_text, links_text = self._collect_structured_results(effective_request)
        route_locale = detect_route_locale(effective_request.destination, effective_request.origin)
        entry_requirements_text = self._build_entry_notice(effective_request, route_locale=route_locale)
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
            "summary_short_text": self._build_short_summary(
                effective_request,
                detected_needs,
                structured_results["flight_results"],
                structured_results["housing_results"],
            ),
            "flight_results": serialize_results(structured_results["flight_results"]),
            "housing_results": serialize_results(structured_results["housing_results"]),
            "activity_results": serialize_results(structured_results["activity_results"]),
            "transport_results": serialize_results(structured_results["transport_results"]),
            "rental_results": serialize_results(structured_results["rental_results"]),
            "detected_needs": serialize_needs(detected_needs),
            "results_updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "open_questions_text": self._build_open_questions(
                effective_request,
                detected_needs,
                structured_results,
                route_locale=route_locale,
            ),
            "status": "active",
        }

    def _merge_edit_request(self, trip: dict, edit_text: str):
        current = self._request_from_trip_row(trip)
        destination = self._planner.extract_destination(edit_text) or str(current["destination"])
        origin = self._planner.extract_origin(edit_text) or str(current["origin"])
        days_count = self._planner.extract_days_count(edit_text) if has_days_hint(edit_text) else int(current["days_count"])
        dates_text = self._planner.extract_dates(edit_text) if has_dates_hint(edit_text) else str(current["dates_text"])
        budget_text = self._planner.extract_budget(edit_text) if has_budget_hint(edit_text) else str(current["budget_text"])
        interests = self._planner.extract_interests(edit_text)
        interests_text = ", ".join(interests) if interests else str(current["interests_text"])
        source_prompt = truncate_source_prompt(
            f"{current['source_prompt']}\n\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435: {edit_text}"
        )
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
            language_code=self._db.get_chat_language(int(trip["chat_id"])),
        )

    async def _rebuild_trip(self, trip_id: int) -> None:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return
        request = self._planner.build_request_from_fields(
            **self._request_from_trip_row(trip),
            language_code=self._db.get_chat_language(int(trip["chat_id"])),
        )
        plan = await asyncio.to_thread(self._planner.generate_plan, request)
        payload = await asyncio.to_thread(
            self._build_trip_payload,
            request,
            plan,
            notes_override=trip["notes"] or "",
        )
        self._db.update_trip_fields(trip_id, payload)

    async def _refresh_weather_for_trip(self, trip_id: int) -> None:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return
        destination = trip["destination"] or ""
        dates_text = trip["dates_text"] or ""
        try:
            summary = await asyncio.to_thread(fetch_weather_summary, destination, dates_text)
        except (WeatherError, OSError):
            # WeatherError: city not found
            # OSError: network/DNS timeout — don't propagate to user
            summary = None
        except Exception:
            # Catch-all to prevent weather errors from crashing trip operations
            summary = None
        self._db.update_trip_fields(
            trip_id,
            {
                "weather_text": summary,
                "weather_updated_at": datetime.now(UTC).isoformat(timespec="seconds") if summary else None,
            },
        )

    async def auto_draft_from_signal(
        self,
        chat_id: int,
        created_by: int | None,
        signal: "ChatSignal",
    ) -> int | None:
        if not signal.destination:
            return None
        inferred_group_size = signal.group_size or (max(2, len(signal.participants_mentioned)) if signal.participants_mentioned else 2)
        inferred_days_count = signal.days_count or 3
        inferred_budget = signal.budget_hint or "Эконом"
        interests_text = ", ".join(signal.interests) if signal.interests else "город, еда"
        request = self._planner.build_request_from_fields(
            title=f"{signal.destination} • {inferred_days_count} дн. • {inferred_group_size} чел.",
            destination=signal.destination,
            origin=signal.origin or "не указано",
            dates_text=signal.dates_text or "не указаны",
            days_count=inferred_days_count,
            group_size=inferred_group_size,
            budget_text=inferred_budget,
            interests_text=interests_text,
            notes=signal.raw_text,
            source_prompt=truncate_source_prompt(signal.raw_text),
            language_code=self._db.get_chat_language(chat_id),
        )
        plan = await asyncio.to_thread(self._planner.generate_plan, request)
        payload = await asyncio.to_thread(self._build_trip_payload, request, plan)
        trip_id = self._db.create_trip(chat_id, created_by, payload)
        self._db.set_selected_trip(chat_id, trip_id)
        self._auto_add_date_options(trip_id, request.dates_text, created_by)
        await self._refresh_weather_for_trip(trip_id)
        return trip_id

    def _auto_add_date_options(self, trip_id: int, dates_text: str, created_by: int | None) -> None:
        """Parse dates_text and auto-create a date option if a valid range is found."""
        if not dates_text or dates_text in ("не указаны", "не указано"):
            return
        # First try numeric parsing via _parse_date_range
        start, end = _parse_date_range(dates_text)
        if start and end:
            label = f"{start} — {end}"
            self._db.add_date_option(trip_id, label, created_by or 0)
            return
        # Fallback: use raw dates_text if it looks like a meaningful date description
        raw = dates_text.strip()
        if len(raw) > 2 and raw not in ("-", "—"):
            self._db.add_date_option(trip_id, raw, created_by or 0)
