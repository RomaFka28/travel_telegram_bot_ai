from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Final

from telegram import ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from bot.keyboards import (
    STATUS_LABELS,
    date_vote_keyboard,
    participant_status_keyboard,
    settings_keyboard,
    trip_budget_keyboard,
    trip_days_keyboard,
    trip_group_size_keyboard,
    trip_skip_keyboard,
)
from database import Database
from llm_travel_planner import LLMTravelPlanner
from travel_planner import TravelPlanner
from weather_service import WeatherError, fetch_weather_summary

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
    def __init__(self, database: Database, planner: TravelPlanner) -> None:
        self.db = database
        self.planner = planner

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
            await update.effective_message.reply_text(
                "Пока нет активной поездки. Запусти /plan <запрос> или создай её пошагово через /newtrip."
            )
        return trip

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
                "\u0441\u0440\u0435\u0434\u043d",
                "\u043a\u043e\u043c\u0444\u043e\u0440\u0442",
                "\u0434\u043e ",
            )
        )

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
        request = self.planner.build_request_from_fields(**self._request_from_trip_row(trip))
        plan = self.planner.generate_plan(request)
        self.db.update_trip_fields(trip_id, self._build_trip_payload(request, plan, notes_override=trip["notes"] or ""))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        chat = update.effective_chat
        if context.args and chat:
            raw_payload = (context.args[0] or "").strip()
            if raw_payload.startswith("trip_"):
                token = raw_payload.removeprefix("trip_")
                trip = self.db.get_trip_by_share_token(token)
                if not trip or trip.get("status") != "active":
                    await message.reply_text("Эта ссылка уже неактуальна или план не найден.")
                    return
                self.db.set_selected_trip(chat.id, int(trip["id"]))
                await message.reply_text(
                    "План открыт. Ниже текущая карточка поездки, можно смотреть детали и отмечать участие."
                )
                await message.reply_text(
                    self._build_summary_html(int(trip["id"])),
                    parse_mode=ParseMode.HTML,
                    reply_markup=participant_status_keyboard(int(trip["id"])),
                )
                return
        await message.reply_text(
            "Привет! Я обновлённый travel-бот для Telegram.\n\n"
            "Теперь я не только собираю участников, но и помогаю спланировать саму поездку: понимаю запрос обычным языком, делаю маршрут по дням, даю грубый бюджет, логистику и рекомендации по проживанию.\n\n"
            "Главные команды:\n"
            "/plan <запрос> — собрать поездку из свободного текста\n"
            "/newtrip — создать поездку пошагово\n"
            "/summary — короткая сводка\n"
            "/brief — структура поездки\n"
            "/itinerary — маршрут по дням\n"
            "/budget — ориентир по бюджету\n"
            "/route — логистика\n"
            "/stay — где жить\n"
            "/alternatives — альтернативные направления\n"
            "/status — отметить участие\n"
            "/participants — список участников\n"
            "/settings — настройки чата\n"
            "/archive_trip — закрыть активную поездку"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.start(update, context)

    async def share_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        message = update.effective_message
        if not trip or not message:
            return
        username = context.bot.username
        if not username:
            await message.reply_text("Не удалось получить username бота для ссылки. Попробуйте чуть позже.")
            return
        token = self.db.create_share_token(int(trip["id"]), update.effective_user.id if update.effective_user else None)
        share_link = f"https://t.me/{username}?start=trip_{token}"
        await message.reply_text(
            "Отправьте эту ссылку другим участникам. По ней откроется текущий план и можно будет отметить участие:\n"
            f"{share_link}"
        )

    async def plan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_message.reply_text(
                "Использование:\n"
                "/plan Хочу поехать с друзьями на 5 дней во Владивосток, нас 4, из Новосибирска, бюджет средний, любим море и еду"
            )
            return

        raw_text = " ".join(context.args).strip()
        try:
            request = self.planner.parse_trip_request(raw_text)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return

        request.notes = ""
        chat = update.effective_chat
        user = update.effective_user
        if not chat:
            return

        plan = self.planner.generate_plan(request)
        payload = self._build_trip_payload(request, plan, notes_override="")
        trip_id = self.db.create_trip(chat.id, user.id if user else None, payload)
        self.db.set_selected_trip(chat.id, trip_id)
        self._refresh_weather_for_trip(trip_id)

        await update.effective_message.reply_text(
            "Черновик поездки готов. Я разобрал запрос и собрал travel-brief, маршрут, логистику и бюджетный ориентир."
        )
        await update.effective_message.reply_text(
            self._build_summary_html(trip_id),
            parse_mode=ParseMode.HTML,
            reply_markup=participant_status_keyboard(trip_id),
        )

    async def plan_ai_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_message.reply_text(
                "Использование:\n"
                "/planai Хочу поехать с друзьями на 5 дней во Владивосток, нас 4, из Новосибирска, бюджет средний, любим море и еду"
            )
            return

        raw_text = " ".join(context.args).strip()
        try:
            request = self.planner.parse_trip_request(raw_text)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return

        request.notes = ""
        chat = update.effective_chat
        user = update.effective_user
        if not chat:
            return

        if isinstance(self.planner, LLMTravelPlanner):
            await update.effective_message.reply_text("Думаю над поездкой (LLM)… это может занять до минуты.")
            plan, used_llm, err = self.planner.generate_plan_with_fallback(request)
            if not used_llm and err:
                await update.effective_message.reply_text(
                    "Не получилось получить ответ от LLM, собрал план на встроенных эвристиках.\n"
                    f"Причина: {err}"
                )
        else:
            await update.effective_message.reply_text(
                "LLM не настроена. Добавь OPENROUTER_API_KEY в .env, либо используй /plan (эвристики)."
            )
            return

        payload = self._build_trip_payload(request, plan, notes_override="")
        trip_id = self.db.create_trip(chat.id, user.id if user else None, payload)
        self.db.set_selected_trip(chat.id, trip_id)
        self._refresh_weather_for_trip(trip_id)
        await update.effective_message.reply_text("Готово. Поездка сохранена.")
        await update.effective_message.reply_text(
            self._build_summary_html(trip_id),
            parse_mode=ParseMode.HTML,
            reply_markup=participant_status_keyboard(trip_id),
        )

    async def new_trip_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data["trip_draft"] = {}
        await update.effective_message.reply_text("Как назвать поездку? Можно отправить '-' и я сгенерирую название автоматически.")
        return NEW_TRIP_TITLE

    async def new_trip_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        title = (update.effective_message.text or "").strip()
        context.user_data.setdefault("trip_draft", {})["title"] = "" if title == "-" else title
        await update.effective_message.reply_text("Куда планируете ехать?")
        return NEW_TRIP_DESTINATION

    async def new_trip_destination(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.setdefault("trip_draft", {})["destination"] = (update.effective_message.text or "").strip()
        await update.effective_message.reply_text("Откуда стартуете? Если пока не знаете — отправьте '-'.")
        await update.effective_message.reply_text(
            "Можно пропустить этот шаг кнопкой ниже.",
            reply_markup=trip_skip_keyboard(),
        )
        return NEW_TRIP_ORIGIN

    async def new_trip_origin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        origin = (update.effective_message.text or "").strip()
        context.user_data.setdefault("trip_draft", {})["origin"] = "не указано" if origin == "-" else origin
        await update.effective_message.reply_text("На сколько дней поездка?")
        await update.effective_message.reply_text(
            "Выберите длительность кнопкой или введите своё число.",
            reply_markup=trip_days_keyboard(),
        )
        return NEW_TRIP_DAYS

    async def new_trip_days(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw_value = (update.effective_message.text or "").strip()
        try:
            days_count = max(1, min(int(raw_value), 14))
        except ValueError:
            await update.effective_message.reply_text("Нужно число от 1 до 14. Например: 5")
            return NEW_TRIP_DAYS
        context.user_data.setdefault("trip_draft", {})["days_count"] = days_count
        await update.effective_message.reply_text("Какие ориентировочные даты или сезон? Например: 12–16 июня, майские, август.")
        await update.effective_message.reply_text(
            "Даты можно написать свободно.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NEW_TRIP_DATES

    async def new_trip_dates(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.setdefault("trip_draft", {})["dates_text"] = (update.effective_message.text or "").strip()
        await update.effective_message.reply_text("Сколько человек планируете? Если пока прикидка — всё равно напишите число.")
        await update.effective_message.reply_text(
            "Выберите размер группы кнопкой или введите своё число.",
            reply_markup=trip_group_size_keyboard(),
        )
        return NEW_TRIP_GROUP_SIZE

    async def new_trip_group_size(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        raw_value = (update.effective_message.text or "").strip()
        try:
            group_size = max(1, min(int(raw_value), 20))
        except ValueError:
            await update.effective_message.reply_text("Нужно число от 1 до 20. Например: 4")
            return NEW_TRIP_GROUP_SIZE
        context.user_data.setdefault("trip_draft", {})["group_size"] = group_size
        await update.effective_message.reply_text("Какой бюджет? Например: эконом, средний, комфорт, до 80 000 на человека.")
        await update.effective_message.reply_text(
            "Можно выбрать готовый вариант кнопкой.",
            reply_markup=trip_budget_keyboard(),
        )
        return NEW_TRIP_BUDGET

    async def new_trip_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.setdefault("trip_draft", {})["budget_text"] = (update.effective_message.text or "").strip()
        await update.effective_message.reply_text("Что важно в поездке? Напиши интересы через запятую: еда, природа, история, море, спокойный темп.")
        await update.effective_message.reply_text(
            "Здесь лучше написать текстом: например 'море, еда, спокойный темп'.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NEW_TRIP_INTERESTS

    async def new_trip_interests(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.setdefault("trip_draft", {})["interests_text"] = (update.effective_message.text or "").strip()
        await update.effective_message.reply_text("Есть заметки или открытые вопросы? Если нет — отправьте '-'.")
        await update.effective_message.reply_text(
            "Если заметок нет, нажмите кнопку ниже.",
            reply_markup=trip_skip_keyboard(),
        )
        return NEW_TRIP_NOTES

    async def new_trip_notes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        draft = context.user_data.get("trip_draft", {})
        notes = (update.effective_message.text or "").strip()
        if notes == "-":
            notes = ""

        try:
            request = self.planner.build_request_from_fields(
                title=draft.get("title", ""),
                destination=draft.get("destination", ""),
                origin=draft.get("origin", "не указано"),
                dates_text=draft.get("dates_text", "не указаны"),
                days_count=int(draft.get("days_count", 3)),
                group_size=int(draft.get("group_size", 2)),
                budget_text=draft.get("budget_text", "средний"),
                interests_text=draft.get("interests_text", "город, еда"),
                notes=notes,
                source_prompt=f"Новый бриф: {draft.get('destination', '')}, {draft.get('days_count', 3)} дн.",
            )
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
            return ConversationHandler.END

        chat = update.effective_chat
        user = update.effective_user
        if not chat:
            await update.effective_message.reply_text("Не удалось определить чат.")
            return ConversationHandler.END

        plan = self.planner.generate_plan(request)
        payload = self._build_trip_payload(request, plan, notes_override=notes)
        trip_id = self.db.create_trip(chat.id, user.id if user else None, payload)
        self.db.set_selected_trip(chat.id, trip_id)
        self._refresh_weather_for_trip(trip_id)
        context.user_data.pop("trip_draft", None)

        await update.effective_message.reply_text("Поездка создана. Я сразу собрал маршрут, бюджет и подсказки по проживанию.")
        await update.effective_message.reply_text(
            self._build_summary_html(trip_id),
            parse_mode=ParseMode.HTML,
            reply_markup=participant_status_keyboard(trip_id),
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
        if not (trip["weather_text"] or "").strip():
            self._refresh_weather_for_trip(int(trip["id"]))
        await update.effective_message.reply_text(
            self._build_summary_html(int(trip["id"])),
            parse_mode=ParseMode.HTML,
            reply_markup=participant_status_keyboard(int(trip["id"])),
        )

    async def brief_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        await update.effective_message.reply_text(
            self._build_brief_html(int(trip["id"])),
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
            self._rebuild_trip(int(trip["id"]))
            trip = self.db.get_trip_by_id(int(trip["id"]))
            await update.effective_message.reply_text(f"Бюджет обновлён: {value}")
        await update.effective_message.reply_text(
            f"<b>Бюджетный ориентир</b>\n{html.escape(trip['budget_breakdown_text'] or 'Оценка ещё не собрана.')}",
            parse_mode=ParseMode.HTML,
        )

    async def participants_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        participants = self.db.list_participants(int(trip["id"]))
        if not participants:
            await update.effective_message.reply_text("\u041f\u043e\u043a\u0430 \u043d\u0438\u043a\u0442\u043e \u043d\u0435 \u043d\u0430\u0436\u0430\u043b \u00ab\u0415\u0434\u0443\u00bb. \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 /status.")
            return
        going = [participant for participant in participants if participant["status"] == "going"]
        if not going:
            await update.effective_message.reply_text("\u041f\u043e\u043a\u0430 \u043d\u0438\u043a\u0442\u043e \u043d\u0435 \u043d\u0430\u0436\u0430\u043b \u00ab\u0415\u0434\u0443\u00bb.")
            return
        lines = [f"\u2022 {participant['full_name']} \u2014 \u2705 \u0415\u0434\u0443" for participant in going]
        await update.effective_message.reply_text("\u0415\u0434\u0443\u0442:\n" + "\n".join(lines))
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip = await self._get_active_trip_or_reply(update)
        if not trip:
            return
        await update.effective_message.reply_text(
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435 \u0434\u043b\u044f \u043f\u043e\u0435\u0437\u0434\u043a\u0438:",
            reply_markup=participant_status_keyboard(int(trip["id"])),
        )

    async def handle_trip_edit_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        trip_id = context.user_data.pop("edit_trip_id", None)
        if not trip_id:
            return
        message = update.effective_message
        if not message:
            return
        trip = self.db.get_trip_by_id(int(trip_id))
        if not trip or trip["status"] != "active":
            await message.reply_text("\u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0439 \u043f\u043b\u0430\u043d \u0434\u043b\u044f \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d.")
            return
        edit_text = (message.text or "").strip()
        if not edit_text:
            await message.reply_text("\u041d\u0443\u0436\u0435\u043d \u0442\u0435\u043a\u0441\u0442 \u0437\u0430\u043f\u0440\u043e\u0441\u0430 \u0434\u043b\u044f \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043f\u043b\u0430\u043d\u0430.")
            return
        request = self._merge_edit_request(trip, edit_text)
        plan = self.planner.generate_plan(request)
        self.db.update_trip_fields(
            int(trip_id),
            self._build_trip_payload(request, plan, notes_override=trip["notes"] or ""),
        )
        self._refresh_weather_for_trip(int(trip_id))
        await message.reply_text("\u041f\u043b\u0430\u043d \u043e\u0431\u043d\u043e\u0432\u043b\u0451\u043d.")
        await message.reply_text(
            self._build_summary_html(int(trip_id)),
            parse_mode=ParseMode.HTML,
            reply_markup=participant_status_keyboard(int(trip_id)),
        )
    async def trip_action_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user:
            return
        try:
            _, trip_id_raw, action = (query.data or "").split(":", 2)
            trip_id = int(trip_id_raw)
        except (ValueError, AttributeError):
            await query.answer("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0442\u044c \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435.", show_alert=True)
            return

        trip = self.db.get_trip_by_id(trip_id)
        if not trip or trip["status"] != "active":
            await query.answer("\u042d\u0442\u0430 \u043f\u043e\u0435\u0437\u0434\u043a\u0430 \u0443\u0436\u0435 \u043d\u0435\u0430\u043a\u0442\u0438\u0432\u043d\u0430.", show_alert=True)
            return

        user = query.from_user
        chat = update.effective_chat
        if chat:
            self.db.set_selected_trip(chat.id, trip_id)

        if action == "going":
            full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip() or user.username or str(user.id)
            self.db.upsert_participant(
                trip_id=trip_id,
                user_id=user.id,
                username=user.username,
                full_name=full_name,
                status="going",
            )
            if query.message:
                await query.edit_message_text(
                    text=self._build_summary_html(trip_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=participant_status_keyboard(trip_id),
                )
            await query.answer("\u041e\u0442\u043c\u0435\u0442\u0438\u043b, \u0447\u0442\u043e \u0432\u044b \u0435\u0434\u0435\u0442\u0435.")
            return

        if action == "share":
            username = context.bot.username
            if not username:
                await query.answer("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0441\u0441\u044b\u043b\u043a\u0443.", show_alert=True)
                return
            token = self.db.create_share_token(trip_id, user.id)
            share_link = f"https://t.me/{username}?start=trip_{token}"
            if query.message:
                await query.message.reply_text(f"\u0421\u0441\u044b\u043b\u043a\u0430 \u0434\u043b\u044f \u043f\u0440\u0438\u0433\u043b\u0430\u0448\u0435\u043d\u0438\u044f:\n{share_link}")
            await query.answer("\u0421\u0441\u044b\u043b\u043a\u0443 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u043b \u0432 \u0447\u0430\u0442.")
            return

        if action == "edit":
            context.user_data["edit_trip_id"] = trip_id
            if query.message:
                await query.message.reply_text(
                    "\u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u043e\u0434\u043d\u0438\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c, \u0447\u0442\u043e \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c. \u041d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: \u00ab\u0441\u0434\u0435\u043b\u0430\u0439 4 \u0434\u043d\u044f, \u0431\u044e\u0434\u0436\u0435\u0442 \u0441\u0440\u0435\u0434\u043d\u0438\u0439, \u0434\u043e\u0431\u0430\u0432\u044c \u043c\u043e\u0440\u0435 \u0438 \u0435\u0434\u0443\u00bb."
                )
            await query.answer("\u0416\u0434\u0443 \u0442\u0435\u043a\u0441\u0442 \u0434\u043b\u044f \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u044f \u043f\u043b\u0430\u043d\u0430.")
            return

        await query.answer("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435.", show_alert=True)
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
        self._rebuild_trip(int(trip["id"]))
        self._refresh_weather_for_trip(int(trip["id"]))
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
        self._refresh_weather_for_trip(int(trip["id"]))
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
        self._rebuild_trip(int(trip["id"]))
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
        chat = update.effective_chat
        if not chat:
            return
        settings = self.db.get_or_create_settings(chat.id)
        reminders_enabled = self._bool_from_db(settings["reminders_enabled"])
        await update.effective_message.reply_text(
            "Настройки этого чата:",
            reply_markup=settings_keyboard(reminders_enabled),
        )

    async def settings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        chat = update.effective_chat
        if not query or not chat:
            return
        await query.answer()
        if query.data != "settings:toggle_reminders":
            await query.answer("Неизвестное действие", show_alert=True)
            return
        settings = self.db.toggle_reminders(chat.id)
        reminders_enabled = self._bool_from_db(settings["reminders_enabled"])
        text = (
            "Настройки этого чата:\nНапоминания включены."
            if reminders_enabled
            else "Настройки этого чата:\nНапоминания выключены."
        )
        await query.edit_message_text(text=text, reply_markup=settings_keyboard(reminders_enabled))

    async def archive_trip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if not chat:
            return
        archived = self.db.archive_active_trip(chat.id)
        if archived:
            self.db.set_selected_trip(chat.id, None)
            await update.effective_message.reply_text("Активная поездка закрыта. Можно собрать новую через /plan или /newtrip.")
        else:
            await update.effective_message.reply_text("Сейчас нет активной поездки.")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Unhandled error while processing update", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Произошла внутренняя ошибка. Попробуйте ещё раз через несколько секунд."
                )
            except Exception:
                logger.exception("Failed to notify user about handler error")
