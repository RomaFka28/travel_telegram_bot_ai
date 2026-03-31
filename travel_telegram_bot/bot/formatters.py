from __future__ import annotations

import html

from bot.keyboards import STATUS_LABELS
from database import Database


class TripFormatter:
    def __init__(self, database: Database) -> None:
        self._db = database

    def _participant_lines(self, trip_id: int) -> list[str]:
        participants = self._db.list_participants(trip_id)
        if not participants:
            return ["⏳ Пока никто не отметил статус."]

        labels = {
            "going": "✅ Едут",
            "interested": "🤔 Думают",
            "not_going": "❌ Не едут",
        }
        lines: list[str] = []
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

    def build_start_text(self) -> str:
        return (
            "Привет! Я Telegram-бот для совместного планирования поездок.\n\n"
            "Что умею уже сейчас:\n"
            "• собираю поездку из свободного текста через /plan\n"
            "• провожу по пошаговому мастеру через /newtrip\n"
            "• показываю summary, маршрут, бюджет и логистику\n"
            "• помогаю группе отметить участие и проголосовать за даты\n"
            "• умею собирать авто-черновик из обсуждения в группе, если это разрешено в настройках\n\n"
            "Быстрый старт:\n"
            "• /plan Хочу на 4 дня в Казань, нас 5, бюджет средний, важны еда и прогулки\n"
            "• /newtrip\n"
            "• /summary\n"
            "• /trips\n"
            "• /help"
        )

    def build_help_text(self) -> str:
        return (
            "<b>Что умеет бот</b>\n"
            "• создать поездку из свободного текста: <code>/plan ...</code>\n"
            "• провести по мастеру: <code>/newtrip</code>\n"
            "• показать структуру поездки: <code>/brief</code>, <code>/summary</code>\n"
            "• показать детали: <code>/itinerary</code>, <code>/budget</code>, <code>/route</code>, <code>/stay</code>, <code>/alternatives</code>\n"
            "• координировать группу: <code>/status</code>, <code>/participants</code>, <code>/adddate</code>\n"
            "• управлять поведением чата: <code>/settings</code>\n"
            "• посмотреть историю и вернуть архивную поездку: <code>/trips</code>, <code>/select_trip ID</code>\n\n"
            "<b>Поддерживаемые сценарии MVP</b>\n"
            "• короткие поездки друзей или коллег на 2-10 дней\n"
            "• одна активная поездка на чат\n"
            "• согласование состава, дат и базового travel-brief внутри Telegram\n\n"
            "<b>Что важно знать</b>\n"
            "• бот не бронирует билеты и жильё\n"
            "• бюджет и логистика здесь ориентировочные, без live-цен\n"
            "• авто-анализ групповых сообщений работает только если включён в /settings\n"
            "• для чтения обычных сообщений в группе у бота должен быть отключён privacy mode в BotFather\n\n"
            "<b>Полезные команды</b>\n"
            "• <code>/setdestination</code>, <code>/setdates</code>, <code>/interests</code>, <code>/notes</code> для быстрых правок\n"
            "• <code>/share</code> чтобы отправить ссылку на текущий план\n"
            "• <code>/archive_trip</code> чтобы закрыть активную поездку без удаления истории"
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
            "Авто-черновики нужны, чтобы бот предлагал план, когда видит обсуждение поездки в группе. "
            "Если хотите только ручной режим через команды, выключите этот флаг."
        )

    def build_trip_created_text(self, *, replaced_trip: bool) -> str:
        if replaced_trip:
            return (
                "Новая поездка создана и стала активной. Предыдущая активная поездка переведена в архив, "
                "история сохранена."
            )
        return "Поездка создана и сохранена как активная для этого чата."

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
            destination = html.escape(trip["destination"] or "без направления")
            lines.append(
                f"• <b>{int(trip['id'])}</b> — {html.escape(trip['title'])} [{badge}]"
                f"\n  {destination}, {html.escape(trip['dates_text'] or 'без дат')}"
            )
        lines.append("")
        lines.append("Чтобы сделать поездку активной, используйте <code>/select_trip ID</code>.")
        return "\n".join(lines)

    def _build_brief_html(self, trip_id: int) -> str:
        trip = self._db.get_trip_by_id(trip_id)
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
        trip = self._db.get_trip_by_id(trip_id)
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
