from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from datetime import datetime
from typing import Final

from telegram import ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from bot.formatters import TripFormatter
from bot.keyboards import (
    date_vote_keyboard,
    language_keyboard,
    participant_status_keyboard,
    route_section_keyboard,
    settings_keyboard,
    trip_delete_confirm_keyboard,
    trip_budget_keyboard,
    trip_days_keyboard,
    trip_group_size_keyboard,
    trip_summary_keyboard,
    trips_list_keyboard,
    trip_skip_keyboard,
)
from bot.trip_service import TripService
from database import Database
from housing_search import HousingSearchProvider
from i18n import tr
from llm_travel_planner import LLMTravelPlanner
from travelpayouts_flights import TravelpayoutsFlightProvider
from travel_planner import TravelPlanner
from weather_service import WeatherError, fetch_weather_summary
from travel_result_models import deserialize_needs

logger = logging.getLogger(__name__)

NEW_TRIP_TITLE: Final[int] = 1
NEW_TRIP_DESTINATION: Final[int] = 2
NEW_TRIP_ORIGIN: Final[int] = 3
NEW_TRIP_DAYS: Final[int] = 4
NEW_TRIP_DATES: Final[int] = 5
NEW_TRIP_GROUP_SIZE: Final[int] = 6
NEW_TRIP_BUDGET: Final[int] = 7
NEW_TRIP_INTERESTS: Final[int] = 8
NEW_TRIP_NOTES: Final[int] = 9


class BotHandlers:
    def __init__(
        self,
        database: Database,
        planner: TravelPlanner,
        formatter: TripFormatter,
        service: TripService,
        housing_provider: HousingSearchProvider,
        flight_provider: TravelpayoutsFlightProvider | None = None,
    ) -> None:
        self.db = database
        self.planner = planner
        self.formatter = formatter
        self.service = service
        self.housing_provider = housing_provider
        self.flight_provider = flight_provider

    @staticmethod
    def _display_name(update: Update) -> str:
        user = update.effective_user
        if not user:
            return "Неизвестный пользователь"
        full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
        return full_name or user.username or str(user.id)

    @staticmethod
    def _status_bucket(status: str) -> str:
        return {
            "going": "✅ Едут",
            "interested": "🤔 Думают",
            "not_going": "❌ Не едут",
        }.get(status, "⏳ Не ответили")

    @staticmethod
    def _bool_from_db(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return bool(int(value))

    @staticmethod
    def _normalize_status(value: str) -> str | None:
        normalized = (value or "").strip().lower()
        mapping = {
            "going": "going",
            "еду": "going",
            "да": "going",
            "yes": "going",
            "interested": "interested",
            "интересно": "interested",
            "думаю": "interested",
            "thinking": "interested",
            "maybe": "interested",
            "not_going": "not_going",
            "notgoing": "not_going",
            "not going": "not_going",
            "нееду": "not_going",
            "не_еду": "not_going",
            "не-еду": "not_going",
            "нет": "not_going",
            "no": "not_going",
        }
        compact = normalized.replace(" ", "")
        return mapping.get(normalized) or mapping.get(compact)

    def _set_participant_status(self, trip_id: int, update: Update, status: str) -> None:
        user = update.effective_user
        if not user:
            return
        full_name = self._display_name(update)
        self.db.upsert_participant(
            trip_id=trip_id,
            user_id=user.id,
            username=user.username,
            full_name=full_name,
            status=status,
        )

    def _remember_chat_member(self, update: Update, *, chat_id: int | None = None) -> None:
        chat = update.effective_chat
        user = update.effective_user
        target_chat_id = chat_id if chat_id is not None else (chat.id if chat else None)
        if target_chat_id is None or not user:
            return
        self.db.upsert_chat_member(
            chat_id=target_chat_id,
            user_id=user.id,
            username=user.username,
            full_name=self._display_name(update),
        )

    def _chat_language(self, chat_id: int | None) -> str:
        if chat_id is None:
            return "ru"
        return self.db.get_chat_language(chat_id)

    @staticmethod
    def _build_plan_prompt_text(language_code: str = "ru") -> str:
        return tr(language_code, "plan_prompt")

    async def _create_trip_from_text(self, update: Update, raw_text: str) -> bool:
        message = update.effective_message
        if not message:
            return False
        text = (raw_text or "").strip()
        if not text:
            await message.reply_text("Need trip text. After /plan, send the description in the next message." if self._chat_language(update.effective_chat.id if update.effective_chat else None) == "en" else "Нужен текст поездки. После /plan отправьте описание следующим сообщением.")
            return False

        try:
            request = self.planner.parse_trip_request(
                text,
                language_code=self._chat_language(update.effective_chat.id if update.effective_chat else None),
            )
        except ValueError as exc:
            await message.reply_text(str(exc))
            await message.reply_text(self._build_plan_prompt_text(self._chat_language(update.effective_chat.id if update.effective_chat else None)))
            return False

        request.notes = ""
        chat = update.effective_chat
        user = update.effective_user
        if not chat:
            return False
        replaced_trip = self.db.get_active_trip(chat.id) is not None
        if isinstance(self.planner, LLMTravelPlanner):
            await message.reply_text(
                "Thinking over the trip with AI, this may take up to a minute..."
                if self._chat_language(chat.id) == "en"
                else "Думаю над поездкой с помощью ИИ, это может занять до минуты..."
            )
        await chat.send_action("typing")

        if isinstance(self.planner, LLMTravelPlanner):
            plan = await self.planner.generate_plan_llm_async(request)
        else:
            plan = await asyncio.to_thread(self.planner.generate_plan, request)
        payload = await asyncio.to_thread(
            self.service._build_trip_payload,
            request,
            plan,
            notes_override="",
        )
        trip_id = self.db.create_trip(chat.id, user.id if user else None, payload)
        self.db.set_selected_trip(chat.id, trip_id)
        await self.service._refresh_weather_for_trip(trip_id)

        await message.reply_text(
            self.formatter.build_trip_created_text(
                replaced_trip=replaced_trip,
                chat_type=getattr(chat, "type", None),
                language_code=self._chat_language(chat.id),
            )
        )
        await message.reply_text(
            self.formatter._build_summary_html(trip_id),
            parse_mode=ParseMode.HTML,
            reply_markup=trip_summary_keyboard(trip_id, self._chat_language(chat.id)),
            disable_web_page_preview=True,
        )
        return True

    async def _should_send_group_reply(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        key: str,
        *,
        cooldown_seconds: int,
    ) -> bool:
        lock_key = f"_lock_{key}"
        if lock_key not in context.chat_data:
            context.chat_data[lock_key] = asyncio.Lock()
        async with context.chat_data[lock_key]:
            now = time.time()
            last = context.chat_data.get(key, 0)
            if now - last < cooldown_seconds:
                return False
            context.chat_data[key] = now
            return True

    @staticmethod
    def _memory_usage_kb() -> int | None:
        try:
            import resource

            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        except Exception:
            return None

    def _log_trip_action(
        self,
        stage: str,
        *,
        action: str | None = None,
        trip_id: int | None = None,
        user_id: int | None = None,
        chat_id: int | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        parts = [f"stage={stage}"]
        if action is not None:
            parts.append(f"action={action}")
        if trip_id is not None:
            parts.append(f"trip_id={trip_id}")
        if user_id is not None:
            parts.append(f"user_id={user_id}")
        if chat_id is not None:
            parts.append(f"chat_id={chat_id}")
        if elapsed_ms is not None:
            parts.append(f"elapsed_ms={elapsed_ms}")
        memory_kb = self._memory_usage_kb()
        if memory_kb is not None:
            parts.append(f"rss_kb={memory_kb}")
        logger.info("trip_action %s", " ".join(parts))

    async def _get_active_trip_or_reply(self, update: Update):
        chat = update.effective_chat
        if not chat:
            return None
        selected_trip = self.db.get_selected_trip(chat.id)
        if selected_trip and selected_trip.get("status") == "active":
            return selected_trip
        if selected_trip and selected_trip.get("status") != "active":
            self.db.set_selected_trip(chat.id, None)
        trip = self.db.get_active_trip(chat.id)
        if trip:
            self.db.set_selected_trip(chat.id, int(trip["id"]))
        if not trip and update.effective_message:
            await update.effective_message.reply_text(tr(self._chat_language(chat.id), "active_trip_missing"))
        return trip

    def _request_from_trip_row(self, trip) -> dict[str, str | int]:
        return {
            "title": trip["title"] or "Новая поездка",
            "destination": trip["destination"] or "",
            "origin": trip["origin"] or "не указано",
            "dates_text": trip["dates_text"] or "не указаны",
            "days_count": int(trip["days_count"] or 3),
            "group_size": int(trip["group_size"] or 2),
            "budget_text": trip["budget_text"] or "Бизнес",
            "interests_text": trip["interests_text"] or "город, еда",
            "notes": trip["notes"] or "",
            "source_prompt": trip["source_prompt"] or "",
        }

    @staticmethod
    def _has_days_hint(text: str) -> bool:
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
                "\u0434\u0451\u0448\u0435\u0432",
                "\u043f\u043e\u0434\u0435\u0448\u0435\u0432",
                "\u0441\u0440\u0435\u0434\u043d",
                "\u043a\u043e\u043c\u0444\u043e\u0440\u0442",
                "\u0431\u0438\u0437\u043d\u0435\u0441",
                "\u043f\u0435\u0440\u0432\u044b\u0439 \u043a\u043b\u0430\u0441\u0441",
                "\u043d\u0435 \u043e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d",
                "\u0431\u0435\u0437 \u043e\u0433\u0440\u0430\u043d\u0438\u0447\u0435\u043d\u0438\u0439",
                "\u043b\u044e\u0431\u043e\u0439 \u0431\u044e\u0434\u0436\u0435\u0442",
                "\u043f\u0440\u0435\u043c\u0438\u0443\u043c",
                "\u043b\u044e\u043a\u0441",
                "\u0434\u043e ",
                "\u043e\u0442 ",
                "\u043d\u0430 ",
                "\u043e\u043a\u043e\u043b\u043e ",
                "₽",
                "\u0440\u0443\u0431",
            )
        ) or bool(re.search(r"\b\d[\d\s]{3,}\b", lowered))

    @staticmethod
    def _has_dates_hint(text: str) -> bool:
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
    def _merge_edit_request(self, trip: dict, edit_text: str):
        current = self._request_from_trip_row(trip)
        destination = self.planner._extract_destination(edit_text) or str(current["destination"])
        origin = self.planner._extract_origin(edit_text) or str(current["origin"])
        days_count = self.planner._extract_days_count(edit_text) if self._has_days_hint(edit_text) else int(current["days_count"])
        dates_text = self.planner._extract_dates(edit_text) if self._has_dates_hint(edit_text) else str(current["dates_text"])
        budget_text = self.planner._extract_budget(edit_text) if self._has_budget_hint(edit_text) else str(current["budget_text"])
        interests = self.planner._extract_interests(edit_text)
        interests_text = ", ".join(interests) if interests else str(current["interests_text"])
        source_prompt = f"{current['source_prompt']}\n\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435: {edit_text}".strip()
        return self.planner.build_request_from_fields(
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
            language_code=self._chat_language(int(trip.get("chat_id") or 0)),
        )
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

    def _participant_lines(self, trip_id: int) -> list[str]:
        participants = self.db.list_participants(trip_id)
        going_names = [participant["full_name"] for participant in participants if participant["status"] == "going"]
        if not going_names:
            return ["\u2705 \u0415\u0434\u0443\u0442 (0): \u2014"]
        return [f"\u2705 \u0415\u0434\u0443\u0442 ({len(going_names)}): {html.escape(', '.join(going_names))}"]
    def _date_lines(self, trip_id: int) -> list[str]:
        date_options = self.db.list_date_options(trip_id)
        return [
            f"• {html.escape(option['label'])} — <b>{option['votes']}</b> голос(ов)"
            for option in date_options
        ] or ["• пока не добавлены"]

    @staticmethod
    def _preview_multiline(text: str, *, max_blocks: int) -> str:
        blocks = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
        if not blocks:
            return "—"
        return "\n\n".join(blocks[:max_blocks])

    @staticmethod
    def _escape_block(text: str) -> str:
        return html.escape(text or "—")

    def _build_brief_html(self, trip_id: int) -> str:
        trip = self.db.get_trip_by_id(trip_id)
        if not trip:
            return "<b>Поездка не найдена.</b>"
        lines = [
            f"<b>🧾 {html.escape(trip['title'])}</b>",
            f"📍 Направление: <b>{html.escape(trip['destination'] or 'не указано')}</b>",
            f"🛫 Откуда: <b>{html.escape(trip['origin'] or 'не указано')}</b>",
            f"📅 Даты: <b>{html.escape(trip['dates_text'] or 'не указаны')}</b>",
            f"⏱ Длительность: <b>{int(trip['days_count'] or 0)} дн.</b>",
            f"👥 Размер группы: <b>{int(trip['group_size'] or 0)} чел.</b>",
            f"💸 Бюджет: <b>{html.escape(trip['budget_text'] or 'не указан')}</b>",
            f"🎯 Интересы: <b>{html.escape(trip['interests_text'] or 'не указаны')}</b>",
        ]
        if trip["source_prompt"]:
            lines.append("")
            lines.append("<b>Исходный запрос</b>")
            lines.append(html.escape(trip["source_prompt"]))
        return "\n".join(lines)

    def _build_summary_html(self, trip_id: int) -> str:
        trip = self.db.get_trip_by_id(trip_id)
        if not trip:
            return "<b>Поездка не найдена.</b>"

        itinerary_preview = self._escape_block(self._preview_multiline(trip["itinerary_text"] or "", max_blocks=2))
        stay_preview = self._escape_block(self._preview_multiline(trip["stay_text"] or "", max_blocks=1))
        context_preview = self._escape_block(self._preview_multiline(trip["context_text"] or "", max_blocks=1))
        notes_text = self._escape_block(trip["notes"] or "—")
        weather_text = (trip["weather_text"] or "").strip()
        weather_block = f"\n\n<b>Погода</b>\n{html.escape(weather_text)}" if weather_text else ""

        return (
            f"<b>🧭 {html.escape(trip['title'])}</b>\n"
            f"📍 Направление: <b>{html.escape(trip['destination'] or 'не указано')}</b>\n"
            f"🛫 Откуда: <b>{html.escape(trip['origin'] or 'не указано')}</b>\n"
            f"📅 Даты: <b>{html.escape(trip['dates_text'] or 'не указаны')}</b> · <b>{int(trip['days_count'] or 0)} дн.</b>\n"
            f"👥 Группа: <b>{int(trip['group_size'] or 0)} чел.</b>\n"
            f"💸 Целевой бюджет: <b>{html.escape(trip['budget_text'] or 'не указан')}</b>\n"
            f"🎯 Интересы: <b>{html.escape(trip['interests_text'] or 'не указаны')}</b>\n\n"
            f"<b>Коротко о направлении</b>\n{context_preview}\n\n"
            f"<b>Черновик маршрута</b>\n{itinerary_preview}\n\n"
            f"<b>Где жить</b>\n{stay_preview}\n\n"
            f"<b>Ориентир по бюджету</b>\n{html.escape(trip['budget_total_text'] or 'не рассчитан')}\n\n"
            f"<b>Участники</b>\n"
            + "\n".join(self._participant_lines(trip_id))
            + "\n\n"
            + "<b>Варианты дат</b>\n"
            + "\n".join(self._date_lines(trip_id))
            + "\n\n"
            + f"<b>Заметки / открытые вопросы</b>\n{notes_text}"
            + weather_block
        )

    def _refresh_weather_for_trip(self, trip_id: int) -> None:
        trip = self.db.get_trip_by_id(trip_id)
        if not trip:
            return
        destination = trip["destination"] or ""
        dates_text = trip["dates_text"] or ""
        try:
            summary = fetch_weather_summary(destination, dates_text)
        except WeatherError:
            summary = None
        if summary:
            self.db.update_trip_fields(
                trip_id,
                {
                    "weather_text": summary,
                    "weather_updated_at": datetime.utcnow().isoformat(timespec="seconds"),
                },
            )

    def _rebuild_trip(self, trip_id: int) -> None:
        trip = self.db.get_trip_by_id(trip_id)
        if not trip:
            return
        request = self.planner.build_request_from_fields(
            **self._request_from_trip_row(trip),
            language_code=self._chat_language(int(trip["chat_id"])),
        )
        plan = self.planner.generate_plan(request)
        self.db.update_trip_fields(trip_id, self._build_trip_payload(request, plan, notes_override=trip["notes"] or ""))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        self._remember_chat_member(update)
        settings = self.db.get_or_create_settings(chat.id)
        if not bool(settings.get("language_selected")):
            await message.reply_text(
                tr("ru", "language_prompt") + "\n\n" + tr("en", "language_prompt"),
                reply_markup=language_keyboard(settings.get("language_code")),
            )
            return
        language_code = self._chat_language(chat.id)
        await message.reply_text(self.formatter.build_start_text_for_language(language_code))

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        chat = update.effective_chat
        lang = self._chat_language(chat.id if chat else None)
        await update.effective_message.reply_text(
            self.formatter.build_help_text(lang),
            parse_mode=ParseMode.HTML,
        )

    async def tickets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        lang = self._chat_language(int(trip["chat_id"]))
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        if not self.flight_provider or not self.flight_provider.enabled:
            await update.effective_message.reply_text(
                "Travelpayouts is not connected yet. Add TRAVELPAYOUTS_API_KEY in Render and I will be able to fetch ticket prices." if lang == "en" else "Travelpayouts пока не подключён. Добавьте TRAVELPAYOUTS_API_KEY в Render, и я смогу подтягивать цены на билеты."
            )
            return

        await update.effective_message.reply_text("Checking fresh ticket prices via Travelpayouts..." if lang == "en" else "Смотрю свежие цены на билеты через Travelpayouts...")
        tickets_text = self.flight_provider.build_ticket_snapshot(
            origin=trip["origin"] or "не указано",
            destination=trip["destination"] or "",
            dates_text=trip["dates_text"] or "не указаны",
            budget_text=trip["budget_text"] or "Бизнес",
            group_size=int(trip["group_size"] or 1),
            source_text=f"{trip['source_prompt'] or ''}\n{trip['notes'] or ''}",
        )
        self.db.update_trip_fields(int(trip["id"]), {"tickets_text": tickets_text})
        await update.effective_message.reply_text(tickets_text, disable_web_page_preview=True)

    async def hotels_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        trip = await self._get_active_trip_or_reply(update)
        message = update.effective_message
        if not trip or not message:
            return
        lang = self._chat_language(int(trip["chat_id"]))
        await message.reply_text("Looking for housing options. This may take a few seconds." if lang == "en" else "Ищу варианты и русские сценарии по жилью. Это может занять несколько секунд.")
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        response = await self.housing_provider.search(
            destination=trip["destination"] or "",
            dates_text=trip["dates_text"] or "",
            group_size=int(trip["group_size"] or 2),
        )
        await message.reply_text(
            self.formatter.build_housing_search_text(trip, response),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def trips_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        chat = update.effective_chat
        if not chat:
            return
        trips = self.db.list_trips(chat.id)
        await update.effective_message.reply_text(
            self.formatter.build_trip_list_text(chat.id),
            parse_mode=ParseMode.HTML,
            reply_markup=trips_list_keyboard(trips, self._chat_language(chat.id)) if trips else None,
        )

    async def select_trip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        chat = update.effective_chat
        message = update.effective_message
        if not chat or not message:
            return
        if not context.args:
            await message.reply_text("Использование: /select_trip 12")
            return
        try:
            trip_id = int(context.args[0])
        except ValueError:
            await message.reply_text("Нужен числовой ID поездки. Посмотрите его через /trips.")
            return

        activated = self.db.activate_trip(chat.id, trip_id)
        if not activated:
            await message.reply_text("Не удалось сделать поездку активной. Проверьте ID через /trips.")
            return

        trip = self.db.get_trip_by_id(trip_id)
        if trip:
            self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        await message.reply_text(f"Поездка {trip_id} снова активна.")
        await message.reply_text(
            self.formatter._build_summary_html(trip_id),
            parse_mode=ParseMode.HTML,
            reply_markup=trip_summary_keyboard(trip_id, self._chat_language(chat.id)),
            disable_web_page_preview=True,
        )

    async def plan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        chat = update.effective_chat
        lang = self._chat_language(chat.id if chat else None)
        if not context.args:
            context.chat_data["pending_plan_prompt"] = True
            await update.effective_message.reply_text(self._build_plan_prompt_text(lang))
            return

        raw_text = " ".join(context.args).strip()
        context.chat_data.pop("pending_plan_prompt", None)
        await self._create_trip_from_text(update, raw_text)

    async def new_trip_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data["trip_draft"] = {}
        self._remember_chat_member(update)
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        await update.effective_message.reply_text(
            "What should I call this trip? You can send '-' and I will generate the title automatically." if lang == "en" else "Как назвать поездку? Можно отправить '-' и я сгенерирую название автоматически.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NEW_TRIP_TITLE

    async def new_trip_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        title = (update.effective_message.text or "").strip()
        context.user_data.setdefault("trip_draft", {})["title"] = "" if title == "-" else title
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        await update.effective_message.reply_text("Where do you plan to go?" if lang == "en" else "Куда планируете ехать?")
        return NEW_TRIP_DESTINATION

    async def new_trip_destination(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.setdefault("trip_draft", {})["destination"] = (update.effective_message.text or "").strip()
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        await update.effective_message.reply_text("Where are you starting from? If you do not know yet, send '-'." if lang == "en" else "Откуда стартуете? Если пока не знаете — отправьте '-'.")
        await update.effective_message.reply_text(
            "You can skip this step with the button below." if lang == "en" else "Можно пропустить этот шаг кнопкой ниже.",
            reply_markup=trip_skip_keyboard(),
        )
        return NEW_TRIP_ORIGIN

    async def new_trip_origin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        origin = (update.effective_message.text or "").strip()
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        context.user_data.setdefault("trip_draft", {})["origin"] = ("not specified" if lang == "en" else "не указано") if origin == "-" else origin
        await update.effective_message.reply_text("How many days is the trip?" if lang == "en" else "На сколько дней поездка?")
        await update.effective_message.reply_text(
            "Choose duration with the buttons or type your own number." if lang == "en" else "Выберите длительность кнопкой или введите своё число.",
            reply_markup=trip_days_keyboard(),
        )
        return NEW_TRIP_DAYS

    async def new_trip_days(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw_value = (update.effective_message.text or "").strip()
        try:
            days_count = max(1, min(int(raw_value), 14))
        except ValueError:
            await update.effective_message.reply_text("Please enter a number from 1 to 14. For example: 5" if self._chat_language(update.effective_chat.id if update.effective_chat else None) == "en" else "Нужно число от 1 до 14. Например: 5")
            return NEW_TRIP_DAYS
        context.user_data.setdefault("trip_draft", {})["days_count"] = days_count
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        await update.effective_message.reply_text("What approximate dates or season are you thinking about? For example: June 12-16, spring holidays, August." if lang == "en" else "Какие ориентировочные даты или сезон? Например: 12–16 июня, майские, август.")
        await update.effective_message.reply_text(
            "You can write dates in free form." if lang == "en" else "Даты можно написать свободно.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NEW_TRIP_DATES

    async def new_trip_dates(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.setdefault("trip_draft", {})["dates_text"] = (update.effective_message.text or "").strip()
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        await update.effective_message.reply_text("How many people are you planning for? Even an estimate is fine." if lang == "en" else "Сколько человек планируете? Если пока прикидка — всё равно напишите число.")
        await update.effective_message.reply_text(
            "Choose the group size with the buttons or type your own number." if lang == "en" else "Выберите размер группы кнопкой или введите своё число.",
            reply_markup=trip_group_size_keyboard(),
        )
        return NEW_TRIP_GROUP_SIZE

    async def new_trip_group_size(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw_value = (update.effective_message.text or "").strip()
        try:
            group_size = max(1, min(int(raw_value), 20))
        except ValueError:
            await update.effective_message.reply_text("Please enter a number from 1 to 20. For example: 4" if self._chat_language(update.effective_chat.id if update.effective_chat else None) == "en" else "Нужно число от 1 до 20. Например: 4")
            return NEW_TRIP_GROUP_SIZE
        context.user_data.setdefault("trip_draft", {})["group_size"] = group_size
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        await update.effective_message.reply_text("What budget do you have in mind? For example: Economy, Business, First Class, up to 80 000, or around 50 000." if lang == "en" else "Какой бюджет? Например: Эконом, Бизнес, Первый класс, до 80 000 или на 50 000.")
        await update.effective_message.reply_text(
            "You can use the quick buttons." if lang == "en" else "Можно выбрать готовый вариант кнопкой.",
            reply_markup=trip_budget_keyboard(lang),
        )
        return NEW_TRIP_BUDGET

    async def new_trip_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.setdefault("trip_draft", {})["budget_text"] = (update.effective_message.text or "").strip()
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        await update.effective_message.reply_text("What matters most in this trip? Write interests separated by commas: food, nature, history, sea, slow pace." if lang == "en" else "Что важно в поездке? Напиши интересы через запятую: еда, природа, история, море, спокойный темп.")
        await update.effective_message.reply_text(
            "Free text works best here, for example: 'sea, food, slow pace'." if lang == "en" else "Здесь лучше написать текстом: например 'море, еда, спокойный темп'.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NEW_TRIP_INTERESTS

    async def new_trip_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.setdefault("trip_draft", {})["interests_text"] = (update.effective_message.text or "").strip()
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        await update.effective_message.reply_text("Any notes or open questions? If not, send '-'." if lang == "en" else "Есть заметки или открытые вопросы? Если нет — отправьте '-'.")
        await update.effective_message.reply_text(
            "If you have no notes, use the button below." if lang == "en" else "Если заметок нет, нажмите кнопку ниже.",
            reply_markup=trip_skip_keyboard(),
        )
        return NEW_TRIP_NOTES

    async def new_trip_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        draft = context.user_data.get("trip_draft", {})
        notes = (update.effective_message.text or "").strip()
        if notes == "-":
            notes = ""
        lang = self._chat_language(update.effective_chat.id if update.effective_chat else None)

        try:
            request = self.planner.build_request_from_fields(
                title=draft.get("title", ""),
                destination=draft.get("destination", ""),
                origin=draft.get("origin", "не указано"),
                dates_text=draft.get("dates_text", "не указаны"),
                days_count=int(draft.get("days_count", 3)),
                group_size=int(draft.get("group_size", 2)),
                budget_text=draft.get("budget_text", "Бизнес"),
                interests_text=draft.get("interests_text", "город, еда"),
                notes=notes,
                source_prompt=f"Новый бриф: {draft.get('destination', '')}, {draft.get('days_count', 3)} дн.",
                language_code=lang,
            )
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return ConversationHandler.END

        chat = update.effective_chat
        user = update.effective_user
        if not chat:
            await update.effective_message.reply_text("Не удалось определить чат.")
            return ConversationHandler.END
        replaced_trip = self.db.get_active_trip(chat.id) is not None

        if isinstance(self.planner, LLMTravelPlanner):
            plan = await self.planner.generate_plan_llm_async(request)
        else:
            plan = await asyncio.to_thread(self.planner.generate_plan, request)
        payload = await asyncio.to_thread(
            self.service._build_trip_payload,
            request,
            plan,
            notes_override=notes,
        )
        trip_id = self.db.create_trip(chat.id, user.id if user else None, payload)
        self.db.set_selected_trip(chat.id, trip_id)
        await self.service._refresh_weather_for_trip(trip_id)
        context.user_data.pop("trip_draft", None)

        await update.effective_message.reply_text(
            self.formatter.build_trip_created_text(
                replaced_trip=replaced_trip,
                chat_type=getattr(update.effective_chat, "type", None),
                language_code=self._chat_language(chat.id),
            )
        )
        await update.effective_message.reply_text(
            self.formatter._build_summary_html(trip_id),
            parse_mode=ParseMode.HTML,
            reply_markup=trip_summary_keyboard(trip_id, self._chat_language(chat.id)),
            disable_web_page_preview=True,
        )
        await update.effective_message.reply_text(
            "Мастер завершён.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    async def cancel_new_trip(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.pop("trip_draft", None)
        await update.effective_message.reply_text(
            "Создание поездки отменено.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    async def summary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        if not (trip["weather_text"] or "").strip():
            await self.service._refresh_weather_for_trip(int(trip["id"]))
        await update.effective_message.reply_text(
            self.formatter._build_summary_html(int(trip["id"])),
            parse_mode=ParseMode.HTML,
            reply_markup=trip_summary_keyboard(int(trip["id"]), self._chat_language(int(trip["chat_id"]))),
            disable_web_page_preview=True,
        )

    async def brief_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        await update.effective_message.reply_text(
            self.formatter._build_brief_html(int(trip["id"])),
            parse_mode=ParseMode.HTML,
        )

    async def itinerary_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        await update.effective_message.reply_text(
            f"<b>Маршрут по дням</b>\n{html.escape(trip['itinerary_text'] or 'Маршрут пока не собран.')}",
            parse_mode=ParseMode.HTML,
        )

    async def route_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        await update.effective_message.reply_text(
            f"<b>Логистика и как добираться</b>\n{html.escape(trip['logistics_text'] or 'Логистика пока не собрана.')}",
            parse_mode=ParseMode.HTML,
        )

    async def stay_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        await update.effective_message.reply_text(
            f"<b>Где жить</b>\n{html.escape(trip['stay_text'] or 'Рекомендации по проживанию пока не собраны.')}",
            parse_mode=ParseMode.HTML,
        )

    async def alternatives_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        await update.effective_message.reply_text(
            f"<b>Альтернативные направления</b>\n{html.escape(trip['alternatives_text'] or 'Альтернативы пока не подобраны.')}",
            parse_mode=ParseMode.HTML,
        )

    async def budget_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        if context.args:
            value = " ".join(context.args).strip()
            self.db.update_trip_fields(int(trip["id"]), {"budget_text": value})
            await self.service._rebuild_trip(int(trip["id"]))
            trip = self.db.get_trip_by_id(int(trip["id"]))
            await update.effective_message.reply_text(f"Бюджет обновлён: {value}")
        await update.effective_message.reply_text(
            f"<b>Бюджетный ориентир</b>\n{html.escape(trip['budget_breakdown_text'] or 'Оценка ещё не собрана.')}",
            parse_mode=ParseMode.HTML,
        )

    async def participants_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        await update.effective_message.reply_text(
            self.formatter.build_participants_text(int(trip["id"])),
            parse_mode=ParseMode.HTML,
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        lang = self._chat_language(int(trip["chat_id"]))
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        normalized_status = self._normalize_status(" ".join(context.args)) if context.args else None
        if normalized_status:
            self._set_participant_status(int(trip["id"]), update, normalized_status)
            await update.effective_message.reply_text(self.formatter.build_status_updated_text(normalized_status, lang))
            await update.effective_message.reply_text(
                self.formatter.build_participants_text(int(trip["id"])),
                parse_mode=ParseMode.HTML,
            )
            return
        await update.effective_message.reply_text(
            self.formatter.build_status_options_text(int(trip["id"])),
            parse_mode=ParseMode.HTML,
            reply_markup=participant_status_keyboard(int(trip["id"]), lang),
        )

    async def handle_trip_edit_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        pending_plan = context.chat_data.pop("pending_plan_prompt", False)
        if pending_plan:
            await self._create_trip_from_text(update, update.effective_message.text if update.effective_message else "")
            return

        trip_id = context.user_data.pop("edit_trip_id", None)
        if not trip_id:
            return
        started_at = time.perf_counter()
        message = update.effective_message
        if not message:
            return
        chat = update.effective_chat
        user = update.effective_user
        self._log_trip_action(
            "edit_start",
            action="edit",
            trip_id=int(trip_id),
            user_id=user.id if user else None,
            chat_id=chat.id if chat else None,
        )
        trip = self.db.get_trip_by_id(int(trip_id))
        if not trip or trip["status"] != "active":
            await message.reply_text("\u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0439 \u043f\u043b\u0430\u043d \u0434\u043b\u044f \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.")
            return
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        edit_text = (message.text or "").strip()
        if not edit_text:
            await message.reply_text("\u041d\u0443\u0436\u0435\u043d \u0442\u0435\u043a\u0441\u0442 \u0437\u0430\u043f\u0440\u043e\u0441\u0430 \u0434\u043b\u044f \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043f\u043b\u0430\u043d\u0430.")
            return
        try:
            request = self.service._merge_edit_request(trip, edit_text)
        except ValueError as exc:
            await message.reply_text(str(exc))
            await message.reply_text("Подсказка: сначала укажите направление, например: «добавь Казань».")
            return
        if isinstance(self.planner, LLMTravelPlanner):
            plan = await self.planner.generate_plan_llm_async(request)
        else:
            plan = await asyncio.to_thread(self.planner.generate_plan, request)
        payload = await asyncio.to_thread(
            self.service._build_trip_payload,
            request,
            plan,
            notes_override=trip["notes"] or "",
        )
        self.db.update_trip_fields(
            int(trip_id),
            payload,
        )
        await self.service._refresh_weather_for_trip(int(trip_id))
        await message.reply_text("\u041f\u043b\u0430\u043d \u043e\u0431\u043d\u043e\u0432\u043b\u0451\u043d.")
        await message.reply_text(
            self.formatter._build_summary_html(int(trip_id)),
            parse_mode=ParseMode.HTML,
            reply_markup=trip_summary_keyboard(int(trip_id), self._chat_language(int(trip["chat_id"]))),
            disable_web_page_preview=True,
        )
        self._log_trip_action(
            "edit_success",
            action="edit",
            trip_id=int(trip_id),
            user_id=user.id if user else None,
            chat_id=chat.id if chat else None,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )

    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return
        self._remember_chat_member(update)
        pending_plan = context.chat_data.pop("pending_plan_prompt", False)
        if pending_plan:
            await self._create_trip_from_text(update, message.text or "")
            return
        settings = self.db.get_or_create_settings(chat.id)
        if not self._bool_from_db(settings.get("autodraft_enabled")):
            return
        text = (message.text or "").strip()
        if len(text) < 15:
            return

        from bot.group_chat_analyzer import GroupChatAnalyzer

        list_lock_key = "_lock_recent_messages"
        if list_lock_key not in context.chat_data:
            context.chat_data[list_lock_key] = asyncio.Lock()
        async with context.chat_data[list_lock_key]:
            recent_messages = context.chat_data.get("recent_group_messages", [])
            if not isinstance(recent_messages, list):
                recent_messages = []
            recent_messages.append(text)
            recent_messages = recent_messages[-8:]
            context.chat_data["recent_group_messages"] = recent_messages

        analyzer = GroupChatAnalyzer()
        signal = analyzer.analyze_messages(recent_messages)
        if not signal.has_travel_intent:
            return

        active_trip = self.db.get_active_trip(chat.id)
        user = update.effective_user

        if not signal.destination:
            if signal.destination_votes and await self._should_send_group_reply(context, "last_destination_vote_reply", cooldown_seconds=600):
                await message.reply_text(
                    self.formatter.build_group_destination_vote_text(
                        signal.destination_votes,
                        self._chat_language(chat.id),
                    ),
                    parse_mode=ParseMode.HTML,
                )
            elif await self._should_send_group_reply(context, "last_clarify_reply", cooldown_seconds=600):
                await message.reply_text(
                    self.formatter.build_group_clarifying_question(self._chat_language(chat.id))
                )
            return

        if active_trip:
            current_dest = (active_trip.get("destination") or "").strip().lower()
            signal_dest = (signal.destination or "").strip().lower()
            if current_dest and signal_dest and current_dest == signal_dest:
                updates: dict = {}
                if signal.origin:
                    updates["origin"] = signal.origin
                if signal.dates_text:
                    updates["dates_text"] = signal.dates_text
                if signal.budget_hint:
                    updates["budget_text"] = signal.budget_hint
                if signal.interests:
                    updates["interests_text"] = ", ".join(signal.interests)
                existing_needs = set(deserialize_needs(active_trip.get("detected_needs")))
                if set(signal.detected_needs) - existing_needs:
                    previous_prompt = (active_trip.get("source_prompt") or "").strip()
                    updates["source_prompt"] = f"{previous_prompt}\n{signal.raw_text}".strip()[-3000:]
                if updates:
                    self.db.update_trip_fields(int(active_trip["id"]), updates)
                    await self.service._rebuild_trip(int(active_trip["id"]))
                    if "dates_text" in updates:
                        await self.service._refresh_weather_for_trip(int(active_trip["id"]))
                    if await self._should_send_group_reply(context, "last_auto_update_reply", cooldown_seconds=420):
                        refreshed_trip = self.db.get_trip_by_id(int(active_trip["id"]))
                        if refreshed_trip:
                            await message.reply_text(
                                self.formatter.build_group_autodraft_reply(refreshed_trip),
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                            )
            return

        if not await self._should_send_group_reply(context, "last_auto_reply", cooldown_seconds=420):
            return

        trip_id = await self.service.auto_draft_from_signal(
            chat_id=chat.id,
            created_by=user.id if user else None,
            signal=signal,
        )
        if trip_id:
            trip = self.db.get_trip_by_id(int(trip_id))
            if trip:
                await message.reply_text(
                    self.formatter.build_group_autodraft_reply(trip),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )

    async def trip_action_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user:
            return
        self._remember_chat_member(update)
        started_at = time.perf_counter()
        try:
            _, trip_id_raw, action = (query.data or "").split(":", 2)
            trip_id = int(trip_id_raw)
        except (ValueError, AttributeError):
            await query.answer("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0442\u044c \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435.", show_alert=True)
            return

        trip = self.db.get_trip_by_id(trip_id)
        if not trip:
            await query.answer("\u042d\u0442\u0430 \u043f\u043e\u0435\u0437\u0434\u043a\u0430 \u0443\u0436\u0435 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430.", show_alert=True)
            return

        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        user = query.from_user
        chat = update.effective_chat
        self._log_trip_action(
            "start",
            action=action,
            trip_id=trip_id,
            user_id=user.id,
            chat_id=chat.id if chat else None,
        )
        if chat:
            self.db.set_selected_trip(chat.id, trip_id)

        try:
            if action in {"open_trip", "delete_confirm", "delete_cancel", "delete_now"}:
                if action == "open_trip":
                    activated = self.db.activate_trip(int(trip["chat_id"]), trip_id)
                    if not activated:
                        await query.answer("Не удалось открыть поездку.", show_alert=True)
                        return
                    refreshed_trip = self.db.get_trip_by_id(trip_id)
                    if query.message and refreshed_trip:
                        await query.message.reply_text(f"Поездка {trip_id} снова активна.")
                        await query.message.reply_text(
                            self.formatter._build_summary_html(trip_id),
                            parse_mode=ParseMode.HTML,
                            reply_markup=trip_summary_keyboard(trip_id, self._chat_language(int(trip["chat_id"]))),
                            disable_web_page_preview=True,
                        )
                    await query.answer("Поездка открыта.")
                    return

                if action == "delete_confirm":
                    if query.message:
                        await query.edit_message_text(
                            text=self.formatter.build_trip_delete_confirm_text(trip),
                            parse_mode=ParseMode.HTML,
                            reply_markup=trip_delete_confirm_keyboard(trip_id, self._chat_language(int(trip["chat_id"]))),
                        )
                    await query.answer("Нужно подтверждение.")
                    return

                if action == "delete_cancel":
                    if query.message:
                        if trip["status"] == "active":
                            await query.edit_message_text(
                                text=self.formatter._build_summary_html(trip_id),
                                parse_mode=ParseMode.HTML,
                                reply_markup=trip_summary_keyboard(trip_id, self._chat_language(int(trip["chat_id"]))),
                                disable_web_page_preview=True,
                            )
                        else:
                            await query.edit_message_text("Удаление отменено. Откройте /trips, чтобы продолжить работу с архивом.")
                    await query.answer("Удаление отменено.")
                    return

                if action == "delete_now":
                    chat_id = int(trip["chat_id"])
                    deleted = self.db.delete_trip(chat_id, trip_id)
                    if not deleted:
                        await query.answer("Не удалось удалить поездку.", show_alert=True)
                        return
                    if query.message:
                        remaining_trips = self.db.list_trips(chat_id)
                        if trip["status"] == "active" and remaining_trips:
                            next_trip_id = int(remaining_trips[0]["id"])
                            self.db.activate_trip(chat_id, next_trip_id)
                            await query.edit_message_text("Поездку удалил. Ниже открыл следующую доступную поездку.")
                            await query.message.reply_text(
                                self.formatter._build_summary_html(next_trip_id),
                                parse_mode=ParseMode.HTML,
                                reply_markup=trip_summary_keyboard(next_trip_id, self._chat_language(chat_id)),
                                disable_web_page_preview=True,
                            )
                        elif remaining_trips:
                            await query.edit_message_text(
                                self.formatter.build_trip_list_text(chat_id),
                                parse_mode=ParseMode.HTML,
                                reply_markup=trips_list_keyboard(remaining_trips, self._chat_language(chat_id)),
                            )
                        else:
                            await query.edit_message_text("Поездка удалена. В этом чате больше нет сохранённых поездок.")
                    await query.answer("Поездка удалена.")
                    return

            if trip["status"] != "active":
                await query.answer("\u042d\u0442\u0430 \u043f\u043e\u0435\u0437\u0434\u043a\u0430 \u0443\u0436\u0435 \u043d\u0435\u0430\u043a\u0442\u0438\u0432\u043d\u0430.", show_alert=True)
                return

            if action in {"going", "interested", "not_going"}:
                self._set_participant_status(trip_id, update, action)
                await query.answer(self.formatter.build_status_updated_text(action, self._chat_language(int(trip["chat_id"]))))
                if query.message:
                    await query.edit_message_text(
                        text=self.formatter._build_summary_html(trip_id),
                        parse_mode=ParseMode.HTML,
                        reply_markup=trip_summary_keyboard(trip_id, self._chat_language(int(trip["chat_id"]))),
                        disable_web_page_preview=True,
                    )
                self._log_trip_action(
                    "success",
                    action=action,
                    trip_id=trip_id,
                    user_id=user.id,
                    chat_id=chat.id if chat else None,
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                )
                return

            if action == "show_route":
                await query.answer("Открываю маршрут.")
                if query.message:
                    await query.message.reply_text(
                        self.formatter.build_route_section_text(trip_id),
                        parse_mode=ParseMode.HTML,
                        reply_markup=route_section_keyboard(trip_id, self._chat_language(int(trip["chat_id"]))),
                        disable_web_page_preview=True,
                    )
                return

            if action == "show_summary":
                await query.answer()
                if query.message:
                    await query.edit_message_text(
                        text=self.formatter._build_summary_html(trip_id),
                        parse_mode=ParseMode.HTML,
                        reply_markup=trip_summary_keyboard(trip_id, self._chat_language(int(trip["chat_id"]))),
                        disable_web_page_preview=True,
                    )
                return

            if action == "show_tickets":
                await query.answer("Открываю билеты.")
                if query.message:
                    await query.message.reply_text(
                        self.formatter.build_tickets_section_text(trip_id),
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                return

            if action == "show_housing":
                await query.answer("Открываю жильё.")
                if query.message:
                    await query.message.reply_text(
                        self.formatter.build_housing_section_text(trip_id),
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                return

            if action == "edit":
                context.user_data["edit_trip_id"] = trip_id
                if query.message:
                    await query.message.reply_text(
                        "\u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u043e\u0434\u043d\u0438\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c, \u0447\u0442\u043e \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c. \u041d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: \u00ab\u0441\u0434\u0435\u043b\u0430\u0439 4 \u0434\u043d\u044f, \u0431\u044e\u0434\u0436\u0435\u0442 \u0441\u0440\u0435\u0434\u043d\u0438\u0439, \u0434\u043e\u0431\u0430\u0432\u044c \u043c\u043e\u0440\u0435 \u0438 \u0435\u0434\u0443\u00bb."
                    )
                await query.answer("\u0416\u0434\u0443 \u0442\u0435\u043a\u0441\u0442 \u0434\u043b\u044f \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f \u043f\u043b\u0430\u043d\u0430.")
                self._log_trip_action(
                    "success",
                    action=action,
                    trip_id=trip_id,
                    user_id=user.id,
                    chat_id=chat.id if chat else None,
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                )
                return

            await query.answer("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435.", show_alert=True)
            self._log_trip_action(
                "unknown_action",
                action=action,
                trip_id=trip_id,
                user_id=user.id,
                chat_id=chat.id if chat else None,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )
        except Exception:
            self._log_trip_action(
                "error",
                action=action,
                trip_id=trip_id,
                user_id=user.id,
                chat_id=chat.id if chat else None,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )
            logger.exception("trip_action failed")
            raise
    async def add_date_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        if not context.args:
            await update.effective_message.reply_text("Использование: /adddate 12–16 июня")
            return
        label = " ".join(context.args).strip()
        option_id = self.db.add_date_option(int(trip["id"]), label, update.effective_user.id if update.effective_user else 0)
        await update.effective_message.reply_text(
            f"Добавлен вариант дат: <b>{html.escape(label)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=date_vote_keyboard(option_id=option_id, votes=0),
        )

    async def date_vote_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user:
            return
        try:
            _, option_id_raw = (query.data or "").split(":", 1)
            option_id = int(option_id_raw)
        except (ValueError, AttributeError):
            await query.answer("Не удалось распознать голосование.", show_alert=True)
            return

        option = self.db.get_date_option(option_id)
        if not option:
            await query.answer("Вариант дат уже удалён.", show_alert=True)
            return

        added, total_votes = self.db.toggle_date_vote(option_id=option_id, user_id=query.from_user.id)
        label = html.escape(option["label"])
        await query.answer("Голос учтён" if added else "Голос снят")

        if query.message:
            await query.edit_message_text(
                text=f"Вариант дат: <b>{label}</b>\nТекущих голосов: <b>{total_votes}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=date_vote_keyboard(option_id=option_id, votes=total_votes),
            )

    async def set_destination_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        if not context.args:
            await update.effective_message.reply_text("Использование: /setdestination Владивосток")
            return
        value = " ".join(context.args).strip()
        self.db.update_trip_fields(int(trip["id"]), {"destination": value})
        await self.service._rebuild_trip(int(trip["id"]))
        await self.service._refresh_weather_for_trip(int(trip["id"]))
        await update.effective_message.reply_text(f"Направление обновлено: {value}")

    async def set_dates_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        if not context.args:
            await update.effective_message.reply_text("Использование: /setdates 12–16 июня")
            return
        value = " ".join(context.args).strip()
        self.db.update_trip_fields(int(trip["id"]), {"dates_text": value})
        await self.service._refresh_weather_for_trip(int(trip["id"]))
        await update.effective_message.reply_text(f"Даты обновлены: {value}")

    async def interests_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        if not context.args:
            await update.effective_message.reply_text("Использование: /interests природа, еда, история")
            return
        value = " ".join(context.args).strip()
        self.db.update_trip_fields(int(trip["id"]), {"interests_text": value})
        await self.service._rebuild_trip(int(trip["id"]))
        await update.effective_message.reply_text(f"Интересы обновлены: {value}")

    async def notes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        if not context.args:
            current = trip["notes"] or "—"
            await update.effective_message.reply_text(f"Текущие заметки: {current}")
            return
        value = " ".join(context.args).strip()
        self.db.update_trip_fields(int(trip["id"]), {"notes": value})
        await update.effective_message.reply_text("Заметки обновлены.")

    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        chat = update.effective_chat
        if not chat:
            return
        settings = self.db.get_or_create_settings(chat.id)
        reminders_enabled = self._bool_from_db(settings["reminders_enabled"])
        autodraft_enabled = self._bool_from_db(settings["autodraft_enabled"])
        lang = self._chat_language(chat.id)
        await update.effective_message.reply_text(
            self.formatter.build_settings_text(chat.id),
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard(reminders_enabled, autodraft_enabled, lang),
        )

    async def settings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        query = update.callback_query
        chat = update.effective_chat
        if not query or not chat:
            return
        await query.answer()
        if query.data == "settings:toggle_reminders":
            settings = self.db.toggle_reminders(chat.id)
        elif query.data == "settings:toggle_autodraft":
            settings = self.db.toggle_autodraft(chat.id)
        elif query.data == "settings:show_language":
            settings = self.db.get_or_create_settings(chat.id)
            lang = self._chat_language(chat.id)
            await query.edit_message_text(
                text=tr(lang, "language_prompt"),
                reply_markup=language_keyboard(settings.get("language_code")),
            )
            return
        else:
            await query.answer(tr(self._chat_language(chat.id), "settings_toggle_unknown"), show_alert=True)
            return
        reminders_enabled = self._bool_from_db(settings["reminders_enabled"])
        autodraft_enabled = self._bool_from_db(settings["autodraft_enabled"])
        lang = self._chat_language(chat.id)
        await query.edit_message_text(
            text=self.formatter.build_settings_text(chat.id),
            parse_mode=ParseMode.HTML,
            reply_markup=settings_keyboard(reminders_enabled, autodraft_enabled, lang),
        )

    async def language_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        chat = update.effective_chat
        if not query or not chat:
            return
        parts = (query.data or "").split(":")
        if len(parts) != 3 or parts[0] != "language" or parts[1] != "set":
            await query.answer(tr("ru", "language_unknown_action"), show_alert=True)
            return
        language_code = "en" if parts[2] == "en" else "ru"
        previous_settings = self.db.get_or_create_settings(chat.id)
        self.db.set_chat_language(chat.id, language_code)
        await query.answer(tr(language_code, "language_saved"))
        if query.message:
            if bool(previous_settings.get("language_selected")):
                settings = self.db.get_or_create_settings(chat.id)
                await query.edit_message_text(
                    text=self.formatter.build_settings_text(chat.id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=settings_keyboard(
                        self._bool_from_db(settings["reminders_enabled"]),
                        self._bool_from_db(settings["autodraft_enabled"]),
                        language_code,
                    ),
                )
            else:
                await query.edit_message_text(
                    text=self.formatter.build_start_text_for_language(language_code),
                    parse_mode=ParseMode.HTML if "<b>" in self.formatter.build_start_text_for_language(language_code) else None,
                )

    async def archive_trip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if not chat:
            return
        archived = self.db.archive_active_trip(chat.id)
        if archived:
            self.db.set_selected_trip(chat.id, None)
            await update.effective_message.reply_text(
                "Активная поездка переведена в архив. История сохранена, можно собирать новую через /plan или /newtrip."
            )
        else:
            await update.effective_message.reply_text("Сейчас нет активной поездки.")

    async def delete_trip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if not chat or not message:
            return
        if not context.args:
            await message.reply_text("Использование: /delete_trip 12")
            return
        try:
            trip_id = int(context.args[0])
        except ValueError:
            await message.reply_text("Нужен числовой ID поездки. Посмотрите его через /trips.")
            return

        deleted = self.db.delete_trip(chat.id, trip_id)
        if deleted:
            remaining_trips = self.db.list_trips(chat.id)
            if remaining_trips:
                next_trip_id = int(remaining_trips[0]["id"])
                self.db.activate_trip(chat.id, next_trip_id)
                await message.reply_text("Поездку удалил. Ниже открыл следующую доступную поездку.")
                await message.reply_text(
                    self.formatter._build_summary_html(next_trip_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=trip_summary_keyboard(next_trip_id, self._chat_language(chat.id)),
                    disable_web_page_preview=True,
                )
            else:
                await message.reply_text("Поездка удалена. В этом чате больше нет сохранённых поездок.")
        else:
            await message.reply_text("Не удалось удалить поездку. Проверьте ID через /trips.")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(update, Update):
            callback_data = update.callback_query.data if update.callback_query else None
            logger.error(
                "update_error chat_id=%s user_id=%s callback_data=%s",
                update.effective_chat.id if update.effective_chat else None,
                update.effective_user.id if update.effective_user else None,
                callback_data,
            )
        logger.exception("Unhandled error while processing update", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Произошла внутренняя ошибка. Попробуйте ещё раз через несколько секунд."
                )
            except Exception:
                logger.exception("Failed to notify user about handler error")
