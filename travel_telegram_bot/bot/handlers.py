from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from typing import Final

from telegram import Message, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.error import Conflict
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
from config import (
    GROUP_AUTO_DRAFT_ERROR_COOLDOWN,
    GROUP_AUTO_UPDATE_COOLDOWN,
    GROUP_CLARIFY_COOLDOWN,
    GROUP_REPLY_COOLDOWN,
    MAX_GROUP_SIZE,
    MAX_RECENT_MESSAGES,
    MAX_TRIP_DAYS,
    MIN_TEXT_LENGTH_FOR_AUTO_PLAN,
    MIN_TRIP_DAYS,
)
from database import Database
from housing_search import HousingSearchProvider
from i18n import tr
from llm_travel_planner import LLMTravelPlanner
from rate_limiter import get_llm_limiter
from travel_planner import TripPlan, TravelPlanner
from trip_request_extractor import TripRequestExtraction, TripRequestExtractor
from travelpayouts_flights import TravelpayoutsFlightProvider
from travel_result_models import deserialize_needs
from value_normalization import truncate_source_prompt

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
        request_extractor: TripRequestExtractor | None = None,
    ) -> None:
        self.db = database
        self.planner = planner
        self.formatter = formatter
        self.service = service
        self.housing_provider = housing_provider
        self.flight_provider = flight_provider
        self.request_extractor = request_extractor or TripRequestExtractor(planner)
        # Cache GroupChatAnalyzer instance (issue #6)
        from bot.group_chat_analyzer import GroupChatAnalyzer
        self._group_analyzer = GroupChatAnalyzer(planner=self.planner, request_extractor=self.request_extractor)

    @staticmethod
    def _get_or_create_async_lock(chat_data: dict, lock_key: str) -> asyncio.Lock:
        """Lazily create asyncio.Lock inside async context to avoid loop binding issues (issue #5)."""
        if lock_key not in chat_data:
            chat_data[lock_key] = asyncio.Lock()
        return chat_data[lock_key]

    async def _generate_plan(self, request) -> "TripPlan":
        """Generate a trip plan using the appropriate planner (LLM or heuristic)."""
        if isinstance(self.planner, LLMTravelPlanner):
            return await self.planner.generate_plan_async(request)
        return await asyncio.to_thread(self.planner.generate_plan, request)

    async def _send_trip_summary(self, message: Message, trip_id: int, lang: str) -> None:
        """Send a formatted trip summary message via reply."""
        await message.reply_text(
            self.formatter._build_summary_html(trip_id),
            parse_mode=ParseMode.HTML,
            reply_markup=trip_summary_keyboard(trip_id, lang),
            disable_web_page_preview=True,
        )

    async def _edit_message_to_summary(self, query, trip_id: int, lang: str) -> None:
        """Edit an existing callback message to show trip summary."""
        if query.message:
            await query.edit_message_text(
                self.formatter._build_summary_html(trip_id),
                parse_mode=ParseMode.HTML,
                reply_markup=trip_summary_keyboard(trip_id, lang),
                disable_web_page_preview=True,
            )

    async def _delete_trip_and_activate_next(self, chat_id: int, trip_id: int, message, query=None, *, show_list_if_remaining=False) -> bool:
        """Delete a trip, activate the next available one, and show its summary.
        Returns True if trip was deleted, False otherwise."""
        from bot.keyboards import trips_list_keyboard

        deleted = self.db.delete_trip(chat_id, trip_id)
        if not deleted:
            return False
        remaining_trips = self.db.list_trips(chat_id)
        if remaining_trips:
            next_trip_id = int(remaining_trips[0]["id"])
            self.db.activate_trip(chat_id, next_trip_id)
            if query and query.message:
                if show_list_if_remaining:
                    await query.edit_message_text(
                        self.formatter.build_trip_list_text(chat_id),
                        parse_mode=ParseMode.HTML,
                        reply_markup=trips_list_keyboard(remaining_trips, self._chat_language(chat_id)),
                    )
                else:
                    await query.edit_message_text("Поездку удалил. Ниже открыл следующую доступную поездку.")
                    await self._send_trip_summary(query.message, next_trip_id, self._chat_language(chat_id))
            elif message:
                await self._send_trip_summary(message, next_trip_id, self._chat_language(chat_id))
        else:
            if query and query.message:
                await query.edit_message_text("Поездка удалена. В этом чате больше нет сохранённых поездок.")
            elif message:
                await message.reply_text("Поездка удалена. В этом чате больше нет сохранённых поездок.")
        return True

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
    async def _parse_required_trip_id_arg(
        message: Message,
        args: list[str],
        *,
        usage_text: str,
    ) -> int | None:
        if not args:
            await message.reply_text(usage_text)
            return None
        try:
            return int(args[0])
        except ValueError:
            await message.reply_text("Нужен числовой ID поездки. Посмотрите его через /trips.")
            return None

    @staticmethod
    def _scoped_chat_state_key(update: Update, base_key: str) -> str:
        user = update.effective_user
        if user is None:
            return base_key
        return f"{base_key}:{user.id}"

    @staticmethod
    def _build_plan_prompt_text(language_code: str = "ru") -> str:
        return tr(language_code, "plan_prompt")

    def _build_plan_followup_state(
        self,
        extraction: TripRequestExtraction,
        *,
        source_prompt: str,
    ) -> dict[str, object] | None:
        if not extraction.missing_fields:
            return None
        request = extraction.to_trip_request(self.planner, source_prompt=source_prompt)
        return {
            "title": request.title,
            "destination": request.destination,
            "origin": request.origin,
            "dates_text": request.dates_text,
            "days_count": request.days_count,
            "group_size": request.group_size,
            "budget_text": request.budget_text,
            "interests_text": request.interests_text,
            "notes": request.notes,
            "source_prompt": request.source_prompt,
            "language_code": request.language_code,
            "fields": list(extraction.missing_fields),
            "index": 0,
        }

    def _plan_followup_question(self, field: str, language_code: str) -> str:
        if language_code == "en":
            mapping = {
                "destination": "What destination do you want to go to?",
                "origin": "What city are you flying from?",
                "dates_text": "What exact date or date range do you need?",
                "route_type": "Do you need a one-way ticket or a round trip?",
            }
        else:
            mapping = {
                "destination": "Куда хотите поехать?",
                "origin": "Из какого города нужен вылет?",
                "dates_text": "Какая точная дата или диапазон дат нужны?",
                "route_type": "Нужен билет в одну сторону или туда-обратно?",
            }
        return mapping[field]

    async def _replace_or_remove_progress_message(
        self,
        progress_message,
        text: str,
        *,
        parse_mode=None,
        reply_markup=None,
        disable_web_page_preview: bool = True,
    ) -> bool:
        if progress_message is None:
            return False
        try:
            await progress_message.edit_text(
                text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            return True
        except Exception as exc:
            logger.info("Could not edit progress message into final plan: %s", exc)
        try:
            await progress_message.delete()
        except Exception as exc:
            logger.info("Could not delete progress message before sending final plan: %s", exc)
        return False

    async def _finalize_trip_request(self, update: Update, request, *, notes_override: str = "") -> bool:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not message or not chat:
            return False
        replaced_trip = self.db.get_active_trip(chat.id) is not None
        progress_message = None
        if isinstance(self.planner, LLMTravelPlanner):
            progress_message = await message.reply_text(
                "Thinking over the trip with AI, this may take up to a minute..."
                if self._chat_language(chat.id) == "en"
                else "Думаю над поездкой с помощью ИИ, это может занять до минуты..."
            )
        await chat.send_action("typing")

        logger.info(
            "trip_generation_start chat_id=%s destination=%s llm=%s",
            chat.id, request.destination, isinstance(self.planner, LLMTravelPlanner),
        )

        # Heartbeat: update progress after 15s so user knows bot is alive
        async def _heartbeat():
            await asyncio.sleep(15)
            if progress_message:
                try:
                    lang = self._chat_language(chat.id)
                    await progress_message.edit_message_text(
                        "Ещё думаю... Подождите немного." if lang == "ru" else "Still thinking... Please wait a bit."
                    )
                except Exception:
                    pass

        heartbeat_task = asyncio.create_task(_heartbeat()) if progress_message else None
        try:
            plan = await self._generate_plan(request)
        except Exception as exc:
            logger.warning("Plan generation failed for %s: %s", request.destination, exc)
            if progress_message:
                lang = self._chat_language(chat.id)
                await progress_message.edit_message_text(
                    "ИИ сейчас перегружен. Создаю поездку по шаблону — позже можно будет улучшить через /edit."
                    if lang == "ru"
                    else "AI is busy. Creating trip from template — you can improve it later via /edit."
                )
                progress_message = None
            plan = await asyncio.to_thread(self.planner.generate_plan_heuristic, request)
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()

        payload = await asyncio.to_thread(
            self.service._build_trip_payload,
            request,
            plan,
            notes_override=notes_override,
        )
        trip_id = self.db.create_trip(chat.id, user.id if user else None, payload)
        self.db.set_selected_trip(chat.id, trip_id)
        await self.service._refresh_weather_for_trip(trip_id)

        logger.info(
            "trip_created trip_id=%s chat_id=%s replaced=%s",
            trip_id, chat.id, replaced_trip,
        )

        await self._send_trip_summary(message, trip_id, self._chat_language(chat.id))
        replaced_status_message = await self._replace_or_remove_progress_message(
            progress_message,
            self.formatter._build_summary_html(trip_id),
            parse_mode=ParseMode.HTML,
            reply_markup=trip_summary_keyboard(trip_id, self._chat_language(chat.id)),
            disable_web_page_preview=True,
        )
        if not replaced_status_message:
            await message.reply_text(
                self.formatter.build_trip_created_text(
                    replaced_trip=replaced_trip,
                    chat_type=getattr(chat, "type", None),
                    language_code=self._chat_language(chat.id),
                )
            )
            await self._send_trip_summary(message, trip_id, self._chat_language(chat.id))
        entry_notice = self.formatter.build_entry_notice_text(trip_id)
        if entry_notice:
            await message.reply_text(entry_notice)
        return True

    async def _continue_plan_followup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        state_key = self._scoped_chat_state_key(update, "plan_followup")
        state = context.chat_data.get(state_key)
        message = update.effective_message
        if not state or not message:
            return False

        field = state["fields"][state["index"]]
        answer = (message.text or "").strip()
        if not answer:
            await message.reply_text(self._plan_followup_question(field, state["language_code"]))
            return True

        if field == "route_type":
            state["notes"] = f"{state.get('notes', '').strip()}\n{answer}".strip()
            state["source_prompt"] = truncate_source_prompt(
                f"{state.get('source_prompt', '').strip()}\n{answer}"
            )
        else:
            state[field] = answer

        state["index"] += 1
        if state["index"] < len(state["fields"]):
            await message.reply_text(self._plan_followup_question(state["fields"][state["index"]], state["language_code"]))
            return True

        context.chat_data.pop(state_key, None)
        request = self.planner.build_request_from_fields(
            title=str(state["title"]),
            destination=str(state["destination"]),
            origin=str(state["origin"]),
            dates_text=str(state["dates_text"]),
            days_count=int(state["days_count"]),
            group_size=int(state["group_size"]),
            budget_text=str(state["budget_text"]),
            interests_text=str(state["interests_text"]),
            notes=str(state["notes"]),
            source_prompt=str(state["source_prompt"]),
            language_code=str(state["language_code"]),
        )
        return await self._finalize_trip_request(update, request, notes_override=str(state["notes"]))

    async def _create_trip_from_text(self, update: Update, raw_text: str, context: ContextTypes.DEFAULT_TYPE | None = None) -> bool:
        message = update.effective_message
        if not message:
            return False
        text = (raw_text or "").strip()
        if not text:
            await message.reply_text("Need trip text. After /plan, send the description in the next message." if self._chat_language(update.effective_chat.id if update.effective_chat else None) == "en" else "Нужен текст поездки. После /plan отправьте описание следующим сообщением.")
            return False
        language_code = self._chat_language(update.effective_chat.id if update.effective_chat else None)
        extraction = await self.request_extractor.extract_async(
            text,
            language_code=language_code,
            planner=self.planner,
            allow_llm=True,
        )
        if not extraction.destination:
            await message.reply_text(self._build_plan_prompt_text(language_code))
            return False

        followup_state = self._build_plan_followup_state(extraction, source_prompt=text)
        if followup_state and context is not None:
            context.chat_data[self._scoped_chat_state_key(update, "plan_followup")] = followup_state
            await message.reply_text(self._plan_followup_question(followup_state["fields"][0], followup_state["language_code"]))
            return True

        request = extraction.to_trip_request(self.planner, source_prompt=text)
        return await self._finalize_trip_request(update, request)

    async def _should_send_group_reply(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        key: str,
        *,
        cooldown_seconds: int,
    ) -> bool:
        lock_key = f"_lock_{key}"
        lock = self._get_or_create_async_lock(context.chat_data, lock_key)
        async with lock:
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
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        lang = self._chat_language(int(trip["chat_id"]))
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
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
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
        trip_id = await self._parse_required_trip_id_arg(
            message,
            context.args,
            usage_text="Использование: /select_trip 12",
        )
        if trip_id is None:
            return

        activated = self.db.activate_trip(chat.id, trip_id)
        if not activated:
            await message.reply_text("Не удалось сделать поездку активной. Проверьте ID через /trips.")
            return

        trip = self.db.get_trip_by_id(trip_id)
        if trip:
            self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        await message.reply_text(f"Поездка {trip_id} снова активна.")
        await self._send_trip_summary(message, trip_id, self._chat_language(chat.id))

    async def plan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
        chat = update.effective_chat
        user = update.effective_user
        lang = self._chat_language(chat.id if chat else None)
        
        # Rate limiting для LLM-запросов
        limiter = get_llm_limiter()
        user_key = f"plan:{user.id}:{chat.id}" if user and chat else f"plan:{chat.id if chat else 0}"
        if not limiter.is_allowed(user_key):
            await update.effective_message.reply_text(
                tr(lang, "plan_rate_limited", default="Слишком много запросов. Подождите минуту и попробуйте снова.")
            )
            return
        
        pending_key = self._scoped_chat_state_key(update, "pending_plan_prompt")
        followup_key = self._scoped_chat_state_key(update, "plan_followup")
        if not context.args:
            context.chat_data[pending_key] = True
            context.chat_data.pop(followup_key, None)
            await update.effective_message.reply_text(self._build_plan_prompt_text(lang))
            return

        raw_text = " ".join(context.args).strip()
        context.chat_data.pop(pending_key, None)
        context.chat_data.pop(followup_key, None)
        await self._create_trip_from_text(update, raw_text, context)

    async def new_trip_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user = update.effective_user
        chat = update.effective_chat
        
        # Rate limiting для создания поездок
        limiter = get_llm_limiter()
        user_key = f"newtrip:{user.id}:{chat.id}" if user and chat else f"newtrip:{chat.id if chat else 0}"
        if not limiter.is_allowed(user_key):
            lang = self._chat_language(chat.id if chat else None)
            await update.effective_message.reply_text(
                tr(lang, "newtrip_rate_limited", default="Слишком много запросов. Подождите минуту и попробуйте снова.")
            )
            return ConversationHandler.END
        
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
            days_count = max(MIN_TRIP_DAYS, min(int(raw_value), MAX_TRIP_DAYS))
        except ValueError:
            await update.effective_message.reply_text(
                f"Please enter a number from {MIN_TRIP_DAYS} to {MAX_TRIP_DAYS}. For example: 5"
                if self._chat_language(update.effective_chat.id if update.effective_chat else None) == "en"
                else f"Нужно число от {MIN_TRIP_DAYS} до {MAX_TRIP_DAYS}. Например: 5"
            )
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
            group_size = max(MIN_GROUP_SIZE, min(int(raw_value), MAX_GROUP_SIZE))
        except ValueError:
            await update.effective_message.reply_text(
                f"Please enter a number from {MIN_GROUP_SIZE} to {MAX_GROUP_SIZE}. For example: 4"
                if self._chat_language(update.effective_chat.id if update.effective_chat else None) == "en"
                else f"Нужно число от {MIN_GROUP_SIZE} до {MAX_GROUP_SIZE}. Например: 4"
            )
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
            origin_default = "not specified" if lang == "en" else "не указано"
            dates_default = "not specified" if lang == "en" else "не указаны"
            budget_default = "Business" if lang == "en" else "Бизнес"
            interests_default = "city, food" if lang == "en" else "город, еда"
            brief_label = "New brief" if lang == "en" else "Новый бриф"
            days_label = "days" if lang == "en" else "дн"
            request = self.planner.build_request_from_fields(
                title=draft.get("title", ""),
                destination=draft.get("destination", ""),
                origin=draft.get("origin", origin_default),
                dates_text=draft.get("dates_text", dates_default),
                days_count=int(draft.get("days_count", 3)),
                group_size=int(draft.get("group_size", 2)),
                budget_text=draft.get("budget_text", budget_default),
                interests_text=draft.get("interests_text", interests_default),
                notes=notes,
                source_prompt=truncate_source_prompt(f"{brief_label}: {draft.get('destination', '')}, {draft.get('days_count', 3)} {days_label}."),
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

        try:
            plan = await self._generate_plan(request)
        except Exception as exc:
            logger.warning("Plan generation failed in wizard for %s: %s", request.destination, exc)
            plan = await asyncio.to_thread(self.planner.generate_plan_heuristic, request)
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
        await self._send_trip_summary(update.effective_message, trip_id, self._chat_language(chat.id))
        entry_notice = self.formatter.build_entry_notice_text(trip_id)
        if entry_notice:
            await update.effective_message.reply_text(entry_notice)
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
        await self._send_trip_summary(
            update.effective_message, int(trip["id"]), self._chat_language(int(trip["chat_id"]))
        )

    async def brief_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._remember_chat_member(update)
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
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        lang = self._chat_language(int(trip["chat_id"]))
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
        if await self._continue_plan_followup(update, context):
            return
        pending_key = self._scoped_chat_state_key(update, "pending_plan_prompt")
        pending_plan = context.chat_data.pop(pending_key, False)
        if pending_plan:
            await self._create_trip_from_text(update, update.effective_message.text if update.effective_message else "", context)
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
            await message.reply_text("Активный план для изменения не найден.")
            return
        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        edit_text = (message.text or "").strip()
        if not edit_text:
            await message.reply_text("Нужен текст запроса для изменения плана.")
            return
        try:
            request = self.service._merge_edit_request(trip, edit_text)
        except ValueError as exc:
            await message.reply_text(str(exc))
            await message.reply_text("Подсказка: сначала укажите направление, например: «добавь Казань».")
            return
        try:
            plan = await self._generate_plan(request)
        except Exception as exc:
            logger.warning("Plan generation failed in edit for %s: %s", trip.get("destination"), exc)
            await message.reply_text("ИИ сейчас перегружен. Попробуйте повторить запрос чуть позже.")
            return
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
        await message.reply_text("План обновлён.")
        await self._send_trip_summary(message, int(trip_id), self._chat_language(int(trip["chat_id"])))
        entry_notice = self.formatter.build_entry_notice_text(int(trip_id))
        if entry_notice:
            await message.reply_text(entry_notice)
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
        if await self._continue_plan_followup(update, context):
            return
        pending_key = self._scoped_chat_state_key(update, "pending_plan_prompt")
        pending_plan = context.chat_data.pop(pending_key, False)
        if pending_plan:
            await self._create_trip_from_text(update, message.text or "", context)
            return
        settings = self.db.get_or_create_settings(chat.id)
        if not self._bool_from_db(settings.get("autodraft_enabled")):
            return
        text = (message.text or "").strip()
        if len(text) < MIN_TEXT_LENGTH_FOR_AUTO_PLAN:
            return

        list_lock_key = "_lock_recent_messages"
        list_lock = self._get_or_create_async_lock(context.chat_data, list_lock_key)
        async with list_lock:
            recent_messages = context.chat_data.get("recent_group_messages", [])
            if not isinstance(recent_messages, list):
                recent_messages = []
            recent_messages.append(text)
            recent_messages = recent_messages[-MAX_RECENT_MESSAGES:]
            context.chat_data["recent_group_messages"] = recent_messages

        signal = self._group_analyzer.analyze_messages(recent_messages)
        active_trip = self.db.get_active_trip(chat.id)
        if active_trip and any(
            [
                signal.dates_text,
                signal.budget_hint,
                signal.origin,
                signal.interests,
                signal.detected_needs,
            ]
        ):
            signal.has_travel_intent = True
            if not signal.destination:
                signal.destination = active_trip.get("destination")
        if not signal.has_travel_intent:
            return

        user = update.effective_user

        if not signal.destination:
            if signal.destination_votes:
                if await self._should_send_group_reply(context, "last_destination_vote_reply", cooldown_seconds=GROUP_REPLY_COOLDOWN):
                    await message.reply_text(
                        self.formatter.build_group_destination_vote_text(
                            signal.destination_votes,
                            self._chat_language(chat.id),
                        ),
                        parse_mode=ParseMode.HTML,
                    )
            elif await self._should_send_group_reply(context, "last_clarify_reply", cooldown_seconds=GROUP_REPLY_COOLDOWN):
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
                    updates["source_prompt"] = truncate_source_prompt(
                        f"{previous_prompt}\n{signal.raw_text}"
                    )
                if updates:
                    trip_id = int(active_trip["id"])
                    # Serialize updates per-trip to avoid race between concurrent messages (issue #2)
                    trip_lock_key = f"_trip_lock_{trip_id}"
                    trip_lock = self._get_or_create_async_lock(context.chat_data, trip_lock_key)
                    async with trip_lock:
                        self.db.update_trip_fields(trip_id, updates)
                        try:
                            await self.service._rebuild_trip(trip_id)
                            if "dates_text" in updates:
                                await self.service._refresh_weather_for_trip(trip_id)
                        except Exception:
                            logger.exception(
                                "Group trip auto-update failed: chat_id=%s message_id=%s user_id=%s trip_id=%s updates=%s text=%r",
                                chat.id,
                                getattr(message, "message_id", None),
                                user.id if user else None,
                                trip_id,
                                sorted(updates.keys()),
                                text,
                            )
                            await message.reply_text(
                                "Не удалось обновить поездку автоматически. Попробуйте повторить сообщение чуть позже."
                            )
                            return
                    if await self._should_send_group_reply(context, "last_auto_update_reply", cooldown_seconds=GROUP_AUTO_UPDATE_COOLDOWN):
                        refreshed_trip = self.db.get_trip_by_id(trip_id)
                        if refreshed_trip:
                            await message.reply_text(
                                self.formatter.build_group_autodraft_reply(refreshed_trip),
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=True,
                            )
            return

        if not await self._should_send_group_reply(context, "last_auto_reply", cooldown_seconds=GROUP_AUTO_UPDATE_COOLDOWN):
            return

        try:
            trip_id = await self.service.auto_draft_from_signal(
                chat_id=chat.id,
                created_by=user.id if user else None,
                signal=signal,
            )
        except Exception:
            logger.exception(
                "Group auto-draft failed: chat_id=%s message_id=%s user_id=%s destination=%s",
                chat.id,
                getattr(message, "message_id", None),
                user.id if user else None,
                signal.destination,
            )
            if await self._should_send_group_reply(context, "last_auto_draft_error_reply", cooldown_seconds=GROUP_REPLY_COOLDOWN):
                await message.reply_text(
                    "Не удалось создать поездку автоматически. Попробуйте начать через /plan или /newtrip."
                )
            return

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
            await query.answer("Не удалось распознать действие.", show_alert=True)
            return

        trip = self.db.get_trip_by_id(trip_id)
        if not trip:
            await query.answer("Эта поездка уже недоступна.", show_alert=True)
            return

        self._remember_chat_member(update, chat_id=int(trip["chat_id"]))
        user = query.from_user
        chat = update.effective_chat
        trip_lang = self._chat_language(int(trip["chat_id"]))
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
                        await self._send_trip_summary(query.message, trip_id, trip_lang)
                    await query.answer("Поездка открыта.")
                    return

                if action == "delete_confirm":
                    if query.message:
                        await query.edit_message_text(
                            text=self.formatter.build_trip_delete_confirm_text(trip),
                            parse_mode=ParseMode.HTML,
                            reply_markup=trip_delete_confirm_keyboard(trip_id, trip_lang),
                        )
                    await query.answer("Нужно подтверждение.")
                    return

                if action == "delete_cancel":
                    if query.message:
                        if trip["status"] == "active":
                            await self._edit_message_to_summary(query, trip_id, trip_lang)
                        else:
                            await query.edit_message_text("Удаление отменено. Откройте /trips, чтобы продолжить работу с архивом.")
                    await query.answer("Удаление отменено.")
                    return

                if action == "delete_now":
                    chat_id = int(trip["chat_id"])
                    deleted = await self._delete_trip_and_activate_next(chat_id, trip_id, None, query, show_list_if_remaining=True)
                    if not deleted:
                        await query.answer("Не удалось удалить поездку.", show_alert=True)
                        return
                    await query.answer("Поездка удалена.")
                    return

            if trip["status"] != "active":
                await query.answer("Эта поездка уже неактивна.", show_alert=True)
                return

            if action in {"going", "interested", "not_going"}:
                self._set_participant_status(trip_id, update, action)
                await query.answer(self.formatter.build_status_updated_text(action, trip_lang))
                if query.message:
                    await self._edit_message_to_summary(query, trip_id, trip_lang)
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
                        reply_markup=route_section_keyboard(trip_id, trip_lang),
                        disable_web_page_preview=True,
                    )
                return

            if action == "show_summary":
                await query.answer()
                if query.message:
                    await self._edit_message_to_summary(query, trip_id, trip_lang)
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
                        "Напишите одним сообщением, что изменить. Например: «сделай 4 дня, бюджет средний, добавь море и еду»."
                    )
                await query.answer("Жду текст для обновления плана.")
                self._log_trip_action(
                    "success",
                    action=action,
                    trip_id=trip_id,
                    user_id=user.id,
                    chat_id=chat.id if chat else None,
                    elapsed_ms=int((time.perf_counter() - started_at) * 1000),
                )
                return

            await query.answer("Неизвестное действие.", show_alert=True)
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
                start_text = self.formatter.build_start_text_for_language(language_code)
                await query.edit_message_text(
                    text=start_text,
                    parse_mode=ParseMode.HTML if "<b>" in start_text else None,
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
        trip_id = await self._parse_required_trip_id_arg(
            message,
            context.args,
            usage_text="Использование: /delete_trip 12",
        )
        if trip_id is None:
            return

        deleted = await self._delete_trip_and_activate_next(chat.id, trip_id, message)
        if not deleted:
            await message.reply_text("Не удалось удалить поездку. Проверьте ID через /trips.")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, Conflict):
            logger.warning(
                "Telegram polling conflict detected. Another bot instance is already consuming updates; stopping this instance."
            )
            context.application.stop_running()
            return

        error = context.error
        error_type = type(error).__name__ if error else "Unknown"
        
        if isinstance(update, Update):
            callback_data = update.callback_query.data if update.callback_query else None
            chat_id = update.effective_chat.id if update.effective_chat else None
            user_id = update.effective_user.id if update.effective_user else None
            
            logger.error(
                "update_error chat_id=%s user_id=%s callback_data=%s error_type=%s",
                chat_id,
                user_id,
                callback_data,
                error_type,
            )
        else:
            logger.error("update_error update_type=%s error_type=%s", type(update).__name__, error_type)
            
        logger.exception("Unhandled error while processing update", exc_info=error)
        
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Произошла внутренняя ошибка. Попробуйте ещё раз через несколько секунд."
                )
            except Exception:
                logger.exception("Failed to notify user about handler error")
