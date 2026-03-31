from __future__ import annotations

import html

from bot.keyboards import STATUS_LABELS
from database import Database
from housing_search import HousingSearchResponse
from travel_result_models import deserialize_needs, deserialize_results
from value_normalization import normalized_search_value


class TripFormatter:
    def __init__(self, database: Database) -> None:
        self._db = database

    def _participant_lines(self, trip_id: int) -> list[str]:
        trip = self._db.get_trip_by_id(trip_id)
        participants = self._db.list_participants(trip_id)
        known_members = self._db.count_chat_members(int(trip["chat_id"])) if trip else 0
        responded = len(participants)
        if not participants:
            if known_members:
                return [f"⏳ Пока никто не отметил статус. Прогресс: 0/{known_members}."]
            return ["⏳ Пока никто не отметил статус."]

        labels = {
            "going": "✅ Едут",
            "interested": "🤔 Думают",
            "not_going": "❌ Не едут",
        }
        lines: list[str] = [f"Прогресс ответов: <b>{responded}/{max(known_members, responded)}</b>"]
        for status in ("going", "interested", "not_going"):
            names = [participant["full_name"] for participant in participants if participant["status"] == status]
            if names:
                lines.append(f"{labels[status]} ({len(names)}): {html.escape(', '.join(names))}")
            else:
                lines.append(f"{labels[status]} (0): —")
        return lines

    def _date_lines(self, trip_id: int) -> list[str]:
        date_options = self._db.list_date_options(trip_id)
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

    @staticmethod
    def _category_title(key: str) -> str:
        return {
            "flight_results": "Билеты",
            "housing_results": "Жильё",
            "activity_results": "Экскурсии",
            "transport_results": "Дорога",
            "rental_results": "Аренда",
        }.get(key, key)

    def _category_section(self, trip: dict, column: str) -> str:
        results = deserialize_results(trip.get(column))
        if not results:
            return ""
        lines = [f"<b>{self._category_title(column)}</b>"]
        for result in results[:3]:
            detail_parts = [html.escape(result.title)]
            if result.price_text:
                detail_parts.append(html.escape(result.price_text))
            if result.budget_fit:
                detail_parts.append(html.escape(result.budget_fit))
            if result.note:
                detail_parts.append(html.escape(result.note))
            lines.append("• " + " — ".join(detail_parts))
            lines.append(html.escape(result.url))
        return "\n".join(lines)

    def _detected_needs_line(self, trip: dict) -> str:
        detected_needs = deserialize_needs(trip.get("detected_needs"))
        if not detected_needs:
            return ""
        labels = {
            "tickets": "билеты",
            "housing": "жильё",
            "excursions": "экскурсии",
            "road": "дорога",
            "car_rental": "аренда авто",
            "bike_rental": "аренда байка",
            "transfers": "трансферы",
        }
        rendered = ", ".join(labels.get(item, item) for item in detected_needs)
        return f"\n🧩 По переписке вижу: <b>{html.escape(rendered)}</b>"

    @staticmethod
    def _has_housing_type_hint(trip: dict) -> bool:
        combined = " ".join(
            str(trip.get(field) or "")
            for field in ("notes", "source_prompt", "interests_text")
        ).lower()
        return any(keyword in combined for keyword in ("отел", "квартир", "апарт", "дом", "студи", "хостел"))

    def _planning_readiness(self, trip: dict, trip_id: int) -> tuple[str, str]:
        detected_needs = set(deserialize_needs(trip.get("detected_needs")))
        known_members = self._db.count_chat_members(int(trip["chat_id"])) if trip.get("chat_id") else 0
        responded = len(self._db.list_participants(trip_id))
        interests_text = normalized_search_value(trip.get("interests_text"))

        checks: list[tuple[str, bool]] = [
            ("направление", bool(normalized_search_value(trip.get("destination")))),
            ("даты", bool(normalized_search_value(trip.get("dates_text")))),
            ("размер группы", int(trip.get("group_size") or 0) > 0),
            ("бюджет", bool(normalized_search_value(trip.get("budget_text")))),
        ]
        if "tickets" in detected_needs:
            checks.append(("город вылета", bool(normalized_search_value(trip.get("origin")))))
        if "housing" in detected_needs:
            checks.append(("тип жилья", self._has_housing_type_hint(trip)))
        if "excursions" in detected_needs:
            checks.append(("формат экскурсий", bool(interests_text)))
        if "road" in detected_needs:
            checks.append(("день выезда", bool(normalized_search_value(trip.get("dates_text")))))
        if known_members > 1:
            checks.append(("ответы участников", responded > 0))

        ready_count = sum(1 for _, is_ready in checks if is_ready)
        total_count = max(1, len(checks))
        status_lines = [f"Готовность плана: <b>{ready_count}/{total_count}</b>"]
        checklist_lines = [
            f"{'✅' if is_ready else '⏳'} {label}"
            for label, is_ready in checks
        ]
        return "\n".join(status_lines), "\n".join(checklist_lines)

    @staticmethod
    def _has_destination(trip: dict) -> bool:
        return bool(normalized_search_value(trip.get("destination")))

    def build_start_text(self) -> str:
        return (
            "Привет! Добавьте меня в чат поездки, включите авто-анализ в /settings и обсуждайте поездку обычными сообщениями.\n\n"
            "Я быстро соберу черновик: куда, когда, сколько человек, какая погода и где искать билеты и жильё.\n\n"
            "Главное:\n"
            "• /summary — текущий план\n"
            "• /tickets — цены на билеты через Travelpayouts\n"
            "• /status — отметить участие\n"
            "• /settings — включить или выключить авто-анализ\n"
            "• /hotels — быстрый сценарий по жилью\n"
            "• /trips — история поездок\n"
            "• /help — короткая справка"
        )

    def build_help_text(self) -> str:
        return (
            "<b>Как использовать</b>\n"
            "1. Добавьте бота в групповой чат.\n"
            "2. Включите авто-анализ в <code>/settings</code>.\n"
            "3. Обсуждайте поездку обычными сообщениями.\n"
            "4. Открывайте <code>/summary</code>, когда нужен текущий план.\n\n"
            "<b>Основные команды</b>\n"
            "• <code>/summary</code> — сводка по активной поездке\n"
            "• <code>/tickets</code> — цены на билеты и оценка по бюджету\n"
            "• <code>/status</code> — отметить участие\n"
            "• <code>/settings</code> — режим чата\n"
            "• <code>/hotels</code> — жильё и подготовка к более точному поиску\n"
            "• <code>/trips</code> — история поездок\n"
            "• <code>/select_trip ID</code> — вернуть поездку из архива\n\n"
            "<b>Что бот делает сейчас</b>\n"
            "• собирает поездку из переписки\n"
            "• показывает погоду\n"
            "• подтягивает цены на билеты через Travelpayouts, если известен город вылета\n"
            "• даёт русские ссылки на билеты, жильё и экскурсии\n"
            "• хранит активную поездку и историю\n\n"
            "<b>Ограничения</b>\n"
            "• бот не бронирует билеты и жильё\n"
            "• цены смотрятся по ссылкам на внешних сайтах"
        )

    def build_settings_text(self, chat_id: int) -> str:
        settings = self._db.get_or_create_settings(chat_id)
        active_trip = self._db.get_active_trip(chat_id)
        reminders_enabled = bool(settings["reminders_enabled"])
        autodraft_enabled = bool(settings["autodraft_enabled"])

        active_trip_line = (
            f"Активная поездка: <b>{html.escape(active_trip['title'])}</b>"
            if active_trip
            else "Активная поездка: <b>нет</b>"
        )
        return (
            "<b>Настройки чата</b>\n"
            f"{active_trip_line}\n\n"
            f"• Напоминания: <b>{'включены' if reminders_enabled else 'выключены'}</b>\n"
            f"• Авто-черновики из сообщений: <b>{'включены' if autodraft_enabled else 'выключены'}</b>\n\n"
            "Авто-черновики нужны, чтобы бот предлагал план, когда видит обсуждение поездки в группе.\n\n"
            "<b>Как это работает</b>\n"
            "• бот анализирует только те сообщения, которые увидел после добавления в группу и после включения авто-анализа\n"
            "• старую историю чата задним числом Telegram боту не отдаёт\n"
            "• для черновика бот смотрит на недавнее окно сообщений, а потом обновляет активную поездку по новым репликам\n\n"
            "Если хотите только ручной режим через команды, выключите этот флаг."
        )

    def build_trip_created_text(self, *, replaced_trip: bool, chat_type: str | None = None) -> str:
        is_group = chat_type in {"group", "supergroup"}
        if replaced_trip:
            if is_group:
                return "Собрал новый план и сделал его активным для этой группы. Предыдущий сохранён в истории."
            return "Собрал новый план и сделал его активным. Предыдущий сохранён в истории."
        if is_group:
            return "Собрал новый план для этой группы."
        return "Собрал новый план."

    def build_status_updated_text(self, status: str) -> str:
        mapping = {
            "going": "Отметил, что вы едете.",
            "interested": "Отметил, что вы пока думаете.",
            "not_going": "Отметил, что вы не едете.",
        }
        return mapping.get(status, "Статус обновлён.")

    def build_participants_text(self, trip_id: int) -> str:
        return "<b>Статусы участников</b>\n" + "\n".join(self._participant_lines(trip_id))

    def build_status_options_text(self) -> str:
        options = " / ".join(STATUS_LABELS[status] for status in ("going", "interested", "not_going"))
        return f"Выберите статус участия или передайте его командой. Доступно: {options}."

    def build_trip_list_text(self, chat_id: int) -> str:
        trips = self._db.list_trips(chat_id)
        if not trips:
            return "В этом чате пока нет поездок."

        lines = ["<b>Поездки этого чата</b>"]
        for trip in trips[:10]:
            badge = "🟢 active" if trip["status"] == "active" else "📦 archived"
            destination = html.escape(normalized_search_value(trip["destination"]) or "без направления")
            dates_text = html.escape(normalized_search_value(trip["dates_text"]) or "без дат")
            lines.append(
                f"• <b>{int(trip['id'])}</b> — {html.escape(trip['title'])} [{badge}]"
                f"\n  {destination}, {dates_text}"
            )
        lines.append("")
        lines.append("Можно открыть поездку или удалить её кнопками ниже.")
        return "\n".join(lines)

    @staticmethod
    def build_trip_delete_confirm_text(trip: dict) -> str:
        return (
            f"Удалить поездку <b>{html.escape(trip['title'])}</b> навсегда?\n"
            "Это действие удалит её из активных и из архива без возможности восстановления."
        )

    def build_group_clarifying_question(self) -> str:
        return "Похоже, вы обсуждаете поездку. Куда хотите поехать?"

    def build_group_autodraft_reply(self, trip: dict) -> str:
        weather_text = (trip.get("weather_text") or "").strip()
        summary_short = (trip.get("summary_short_text") or "").strip()
        open_questions = (trip.get("open_questions_text") or "").strip()
        destination = normalized_search_value(trip.get("destination")) or "не указано"
        dates_text = normalized_search_value(trip.get("dates_text")) or "уточняется"
        budget_text = normalized_search_value(trip.get("budget_text")) or "не указан"
        readiness_text, checklist_text = self._planning_readiness(trip, int(trip["id"]))
        has_destination = self._has_destination(trip)
        sections = [
            self._category_section(trip, "flight_results"),
            self._category_section(trip, "housing_results"),
            self._category_section(trip, "activity_results"),
            self._category_section(trip, "transport_results"),
            self._category_section(trip, "rental_results"),
        ]
        visible_sections = [section for section in sections if section]
        compact_sections = "\n\n".join(visible_sections[:4])
        direction_block = (
            html.escape(summary_short)
            if summary_short and has_destination
            else "Жду направление поездки, чтобы собрать осмысленный маршрут, жильё и полезные ссылки."
        )
        return (
            f"🧭 Собрал черновик поездки\n"
            f"Куда: <b>{html.escape(destination)}</b>\n"
            f"Когда: <b>{html.escape(dates_text)}</b>\n"
            f"Людей: <b>{int(trip['group_size'] or 0)}</b>\n"
            f"Бюджет: <b>{html.escape(budget_text)}</b>\n"
            + self._detected_needs_line(trip)
            + f"\n\n{readiness_text}\n{html.escape(checklist_text)}"
            + f"\n\n<b>Коротко</b>\n{direction_block}"
            + (f"\n\n<b>Погода</b>\n{html.escape(weather_text)}" if weather_text else "")
            + (f"\n\n{compact_sections}" if compact_sections and has_destination else "")
            + (f"\n\n<b>Что ещё уточнить</b>\n{html.escape(open_questions)}" if open_questions else "")
            + "\n\nОткройте /summary, если нужен полный план."
        )

    def build_housing_search_text(self, trip: dict, response: HousingSearchResponse) -> str:
        destination = normalized_search_value(trip["destination"]) or "поездки"
        lines = [
            f"<b>Жильё для {html.escape(destination)}</b>",
            html.escape(response.summary),
        ]
        if response.results:
            lines.append("")
            lines.append("<b>Что открыть</b>")
            for result in response.results[:5]:
                lines.append(
                    f"• <b>{html.escape(result.source)}</b>: {html.escape(result.title)}\n"
                    f"  {html.escape(result.price_text)}\n"
                    f"  {html.escape(result.url)}"
                )
        else:
            lines.append("")
            lines.append("Пока не нашёл вариантов, попробуйте позже или откройте /summary.")
        return "\n".join(lines)

    def _build_brief_html(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return "<b>Поездка не найдена.</b>"
        destination = normalized_search_value(trip["destination"]) or "не указано"
        origin = normalized_search_value(trip["origin"]) or "не указано"
        dates_text = normalized_search_value(trip["dates_text"]) or "не указаны"
        budget_text = normalized_search_value(trip["budget_text"]) or "не указан"
        interests_text = normalized_search_value(trip["interests_text"]) or "не указаны"
        lines = [
            f"<b>🧾 {html.escape(trip['title'])}</b>",
            f"📍 Направление: <b>{html.escape(destination)}</b>",
            f"🛫 Откуда: <b>{html.escape(origin)}</b>",
            f"📅 Даты: <b>{html.escape(dates_text)}</b>",
            f"⏱ Длительность: <b>{int(trip['days_count'] or 0)} дн.</b>",
            f"👥 Размер группы: <b>{int(trip['group_size'] or 0)} чел.</b>",
            f"💸 Бюджет: <b>{html.escape(budget_text)}</b>",
            f"🎯 Интересы: <b>{html.escape(interests_text)}</b>",
        ]
        if trip["source_prompt"]:
            lines.append("")
            lines.append("<b>Исходный запрос</b>")
            lines.append(html.escape(trip["source_prompt"]))
        return "\n".join(lines)

    def _build_summary_html(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
        if not trip:
            return "<b>Поездка не найдена.</b>"

        destination = normalized_search_value(trip["destination"]) or "не указано"
        origin = normalized_search_value(trip["origin"]) or "не указано"
        dates_text = normalized_search_value(trip["dates_text"]) or "не указаны"
        budget_text = normalized_search_value(trip["budget_text"]) or "не указан"
        interests_text = normalized_search_value(trip["interests_text"]) or "не указаны"
        has_destination = self._has_destination(trip)
        itinerary_text = self._escape_block(
            trip["itinerary_text"] if has_destination else "Маршрут появится после того, как станет понятно направление поездки."
        )
        stay_preview = self._escape_block(
            self._preview_multiline(trip["stay_text"] or "", max_blocks=1)
            if has_destination
            else "Подбор жилья начну после того, как определится направление."
        )
        context_preview = self._escape_block(
            self._preview_multiline(trip["context_text"] or "", max_blocks=1)
            if has_destination
            else "Направление пока не определено, поэтому блок с контекстом ещё не заполнен."
        )
        notes_text = self._escape_block(trip["notes"] or "—")
        weather_text = (trip["weather_text"] or "").strip()
        weather_block = f"\n\n<b>Погода</b>\n{html.escape(weather_text)}" if weather_text else ""
        sections = [
            self._category_section(trip, "flight_results"),
            self._category_section(trip, "housing_results"),
            self._category_section(trip, "activity_results"),
            self._category_section(trip, "transport_results"),
            self._category_section(trip, "rental_results"),
        ]
        structured_block = "\n\n".join(section for section in sections if section) if has_destination else ""
        links_text = (trip.get("links_text") or "").strip()
        links_block = f"\n\n<b>Полезные ссылки</b>\n{html.escape(links_text)}" if links_text and not structured_block and has_destination else ""
        summary_short = (trip.get("summary_short_text") or "").strip()
        short_summary_text = (
            summary_short
            if summary_short and has_destination
            else "Направление пока не определено. Как только появятся город и даты, бот пересоберёт маршрут и ссылки."
        )
        short_block = f"\n\n<b>Быстрый вывод</b>\n{html.escape(short_summary_text)}"
        open_questions = (trip.get("open_questions_text") or "").strip()
        open_questions_block = f"\n\n<b>Открытые вопросы</b>\n{html.escape(open_questions)}" if open_questions else ""
        readiness_text, checklist_text = self._planning_readiness(trip, trip_id)

        return (
            f"<b>🧭 {html.escape(trip['title'])}</b>\n"
            f"📍 Направление: <b>{html.escape(destination)}</b>\n"
            f"🛫 Откуда: <b>{html.escape(origin)}</b>\n"
            f"📅 Даты: <b>{html.escape(dates_text)}</b> · <b>{int(trip['days_count'] or 0)} дн.</b>\n"
            f"👥 Группа: <b>{int(trip['group_size'] or 0)} чел.</b>\n"
            f"💸 Целевой бюджет: <b>{html.escape(budget_text)}</b>\n"
            f"🎯 Интересы: <b>{html.escape(interests_text)}</b>"
            + self._detected_needs_line(trip)
            + "\n"
            + f"\n{readiness_text}\n{html.escape(checklist_text)}"
            + short_block
            + "\n\n"
            f"<b>Коротко о направлении</b>\n{context_preview}\n\n"
            f"<b>Маршрут</b>\n{itinerary_text}\n\n"
            f"<b>Где жить</b>\n{stay_preview}\n\n"
            f"<b>Ориентир по бюджету</b>\n{html.escape(trip['budget_total_text'] or 'не рассчитан')}\n\n"
            f"<b>Участники</b>\n"
            + "\n".join(self._participant_lines(trip_id))
            + "\n\n"
            + "<b>Варианты дат</b>\n"
            + "\n".join(self._date_lines(trip_id))
            + "\n\n"
            + f"<b>Заметки</b>\n{notes_text}"
            + open_questions_block
            + (f"\n\n{structured_block}" if structured_block else "")
            + links_block
            + weather_block
        )
