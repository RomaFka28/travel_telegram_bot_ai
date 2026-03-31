from __future__ import annotations

import html

from bot.keyboards import STATUS_LABELS
from database import Database
from housing_search import HousingSearchResponse


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
            "• цены смотрятся по ссылкам на внешних сайтах\n"
            "• для чтения обычных сообщений в группе нужен выключенный privacy mode в BotFather"
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
            return "Собрал новый план и сделал его активным. Предыдущий сохранён в истории."
        return "Собрал новый план для этого чата."

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

    def build_group_clarifying_question(self) -> str:
        return "Похоже, вы обсуждаете поездку. Куда хотите поехать?"

    def build_group_autodraft_reply(self, trip: dict) -> str:
        weather_text = (trip.get("weather_text") or "").strip()
        tickets_text = (trip.get("tickets_text") or "").strip().splitlines()
        links_text = (trip.get("links_text") or "").strip().splitlines()
        useful_links = "\n".join(links_text[:5]) if links_text else "Ссылки пока не собраны."
        tickets_preview = "\n".join(tickets_text[:2]) if tickets_text else ""
        return (
            f"🧭 Собрал черновик поездки\n"
            f"Куда: <b>{html.escape(trip['destination'] or 'не указано')}</b>\n"
            f"Когда: <b>{html.escape(trip['dates_text'] or 'уточняется')}</b>\n"
            f"Людей: <b>{int(trip['group_size'] or 0)}</b>\n"
            f"Бюджет: <b>{html.escape(trip['budget_text'] or 'не указан')}</b>\n"
            + (f"\n\n<b>Билеты</b>\n{html.escape(tickets_preview)}" if tickets_preview else "")
            + (f"\n\n<b>Погода</b>\n{html.escape(weather_text)}" if weather_text else "")
            + f"\n\n<b>Где искать</b>\n{html.escape(useful_links)}"
            + "\n\nОткройте /summary, если нужен полный план."
        )

    def build_housing_search_text(self, trip: dict, response: HousingSearchResponse) -> str:
        lines = [
            f"<b>Жильё для {html.escape(trip['destination'] or 'поездки')}</b>",
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

        itinerary_preview = self._escape_block(self._preview_multiline(trip["itinerary_text"] or "", max_blocks=1))
        stay_preview = self._escape_block(self._preview_multiline(trip["stay_text"] or "", max_blocks=1))
        context_preview = self._escape_block(self._preview_multiline(trip["context_text"] or "", max_blocks=1))
        notes_text = self._escape_block(trip["notes"] or "—")
        weather_text = (trip["weather_text"] or "").strip()
        weather_block = f"\n\n<b>Погода</b>\n{html.escape(weather_text)}" if weather_text else ""
        tickets_text = (trip.get("tickets_text") or "").strip()
        tickets_block = f"\n\n<b>Билеты</b>\n{html.escape(tickets_text)}" if tickets_text else ""
        links_text = (trip.get("links_text") or "").strip()
        links_block = f"\n\n<b>Полезные ссылки</b>\n{html.escape(links_text)}" if links_text else ""

        return (
            f"<b>🧭 {html.escape(trip['title'])}</b>\n"
            f"📍 Направление: <b>{html.escape(trip['destination'] or 'не указано')}</b>\n"
            f"🛫 Откуда: <b>{html.escape(trip['origin'] or 'не указано')}</b>\n"
            f"📅 Даты: <b>{html.escape(trip['dates_text'] or 'не указаны')}</b> · <b>{int(trip['days_count'] or 0)} дн.</b>\n"
            f"👥 Группа: <b>{int(trip['group_size'] or 0)} чел.</b>\n"
            f"💸 Целевой бюджет: <b>{html.escape(trip['budget_text'] or 'не указан')}</b>\n"
            f"🎯 Интересы: <b>{html.escape(trip['interests_text'] or 'не указаны')}</b>\n\n"
            f"<b>Коротко о направлении</b>\n{context_preview}\n\n"
            f"<b>Маршрут</b>\n{itinerary_preview}\n\n"
            f"<b>Где жить</b>\n{stay_preview}\n\n"
            f"<b>Ориентир по бюджету</b>\n{html.escape(trip['budget_total_text'] or 'не рассчитан')}\n\n"
            f"<b>Участники</b>\n"
            + "\n".join(self._participant_lines(trip_id))
            + "\n\n"
            + "<b>Варианты дат</b>\n"
            + "\n".join(self._date_lines(trip_id))
            + "\n\n"
            + f"<b>Заметки / открытые вопросы</b>\n{notes_text}"
            + tickets_block
            + links_block
            + weather_block
        )
