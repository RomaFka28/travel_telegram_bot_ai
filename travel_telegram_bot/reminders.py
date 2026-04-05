"""
Напоминания о поездках.

Типы напоминаний (привязаны к датам поездки):
- pre_3d: за 3 дня до вылета — «Через 3 дня {destination}! Проверьте билеты и жильё»
- pre_1d: за 1 день до вылета — «Завтра вылет в {destination}! Не забудьте документы»
- return_day: в день возврата — «Сегодня возвращаетесь из {destination}!»
- post_1d: на следующий день после возврата — «Как прошла поездка? Оцените через /status»

Хранение:
- reminders_sent в trips — JSON массив отправленных типов: ["pre_3d", "pre_1d"]
- JobQueue — in-memory, пересоздаётся при старте бота из базы
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone

from date_utils import parse_trip_dates
from metrics import get_metrics

if False:  # TYPE_CHECKING
    from telegram import Bot
    from telegram.ext import Application

logger = logging.getLogger(__name__)

REMINDER_TYPES = {
    "pre_3d": {"label": "За 3 дня", "days_offset": -3},
    "pre_1d": {"label": "За 1 день", "days_offset": -1},
    "return_day": {"label": "День возврата", "days_offset": 0, "ref": "end"},
    "post_1d": {"label": "День после", "days_offset": 1, "ref": "end"},
}


def _parse_reminders_sent(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return set(str(x) for x in data)
    except (json.JSONDecodeError, TypeError):
        pass
    return set()


def _serialize_reminders_sent(sent: set[str]) -> str:
    return json.dumps(sorted(sent), ensure_ascii=False)


def _get_reference_date(start_date: date, end_date: date | None, ref: str) -> date:
    """Получить опорную дату (start или end)."""
    if ref == "end" and end_date:
        return end_date
    return start_date


def _calc_reminder_date(start_date: date, end_date: date | None, days_offset: int, ref: str) -> date:
    """Вычислить дату напоминания."""
    ref_date = _get_reference_date(start_date, end_date, ref)
    return ref_date + timedelta(days=days_offset)


def _format_reminder_text(trip_title: str, destination: str, reminder_type: str, start_date: date, end_date: date | None, lang: str) -> str:
    """Сформировать текст напоминания."""
    days_left = (start_date - date.today()).days

    messages_ru = {
        "pre_3d": f"⏰ Напоминание: через 3 дня поездка «{trip_title}» в {destination}!\n\n📋 Чек-лист:\n• Проверьте билеты и брони жилья\n• Подтвердите документы/загранпаспорта\n• Уточните погоду на даты\n\n📅 Вылет: {start_date.strftime('%d.%m.%Y')}",
        "pre_1d": f"🧳 Завтра вылет в {destination}!\n\nНе забудьте:\n• Документы (паспорт, загран)\n• Билеты и брони (скриншоты)\n• Зарядки и адаптеры\n• Лекарства\n\n✈️ Вылет: {start_date.strftime('%d.%m.%Y')}\n🏨 Проживание: {'забронировано' if end_date else 'уточните'}",
        "return_day": f"🏠 Сегодня возвращаетесь из {destination}!\n\n📅 Возврат: {end_date.strftime('%d.%m.%Y') if end_date else 'сегодня'}\n\nНе забудьте:\n• Чекины в отеле\n• Сувениры\n• Проверить ничего ли не забыли\n\nКак доберётесь — отметьте статус через /status",
        "post_1d": f"📝 Надеемся, поездка в {destination} прошла отлично!\n\nРасскажите как всё прошло:\n• /status — обновите свой ответ\n• /summary — посмотрите итоговый план\n• /trips — история поездок\n\nЕсли хотите спланировать следующую — /plan ✈️",
    }

    messages_en = {
        "pre_3d": f"⏰ Reminder: 3 days until \"{trip_title}\" to {destination}!\n\n📋 Checklist:\n• Check flights and accommodation\n• Confirm documents/passports\n• Check weather forecast\n\n📅 Departure: {start_date.strftime('%d.%m.%Y')}",
        "pre_1d": f"🧳 Tomorrow: flight to {destination}!\n\nDon't forget:\n• Documents (passport)\n• Tickets and bookings (screenshots)\n• Chargers and adapters\n• Medications\n\n✈️ Departure: {start_date.strftime('%d.%m.%Y')}",
        "return_day": f"🏠 Heading back from {destination} today!\n\n📅 Return: {end_date.strftime('%d.%m.%Y') if end_date else 'today'}\n\nDon't forget:\n• Hotel checkout\n• Souvenirs\n• Double-check you haven't left anything behind\n\nUse /status to update your reply when you're back",
        "post_1d": f"📝 Hope your trip to {destination} was great!\n\nShare how it went:\n• /status — update your reply\n• /summary — view the final plan\n• /trips — trip history\n\nWant to plan the next one? /plan ✈️",
    }

    messages = messages_ru if lang == "ru" else messages_en
    return messages.get(reminder_type, f"Напоминание о поездке: {trip_title}")


async def schedule_trip_reminders(
    bot: "Bot",
    chat_id: int,
    trip_id: int,
    trip_title: str,
    destination: str,
    dates_text: str,
    lang: str = "ru",
) -> list[str]:
    """
    Запланировать напоминания для поездки.

    Returns список запланиненных типов.
    """
    from travel_links import _parse_date_range

    start_iso, end_iso = _parse_date_range(dates_text)
    if not start_iso:
        logger.debug("No dates for trip %d, skipping reminders", trip_id)
        return []

    try:
        start_date = date.fromisoformat(start_iso)
        end_date = date.fromisoformat(end_iso) if end_iso else None
    except ValueError:
        logger.debug("Invalid dates for trip %d: %s - %s", trip_id, start_iso, end_iso)
        return []

    # Don't schedule for past trips
    today = date.today()
    if start_date < today:
        return []

    scheduled = []
    for reminder_type, config in REMINDER_TYPES.items():
        reminder_date = _calc_reminder_date(start_date, end_date, config["days_offset"], config.get("ref", "start"))

        # Skip if already in the past
        if reminder_date < today:
            continue

        # Schedule at 10:00 local-ish (use UTC for simplicity)
        reminder_datetime = datetime.combine(reminder_date, time(10, 0), tzinfo=timezone.utc)

        text = _format_reminder_text(trip_title, destination, reminder_type, start_date, end_date, lang)

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
            )
            logger.info(
                "Reminder sent immediately: chat=%d trip=%d type=%s for %s",
                chat_id, trip_id, reminder_type, reminder_date,
            )
            scheduled.append(reminder_type)
            get_metrics().increment("reminders.sent")
        except Exception as e:
            logger.warning("Failed to send reminder: chat=%d trip=%d type=%s: %s", chat_id, trip_id, reminder_type, e)
            get_metrics().increment("reminders.failed")

    return scheduled


async def restore_reminders_on_startup(
    bot: "Bot",
    db,
) -> int:
    """
    При старте бота: восстановить напоминания для всех активных поездок.

    Проверяет какие напоминания ещё не были отправлены и отправляет те,
    чья дата уже наступила (пропущенные при рестарте).

    Returns количество отправленных напоминаний.
    """
    sent_count = 0

    try:
        trips = await db._run(
            lambda: db._get_all_active_trips_with_reminders()
        )
    except Exception as e:
        logger.warning("Failed to load trips for reminder restore: %s", e)
        return 0

    for trip in trips:
        chat_id = int(trip["chat_id"])
        trip_id = int(trip["id"])
        title = trip.get("title") or f"Поездка #{trip_id}"
        destination = trip.get("destination") or ""
        dates_text = trip.get("dates_text") or ""
        lang = trip.get("language_code") or "ru"
        reminders_sent_raw = trip.get("reminders_sent")

        already_sent = _parse_reminders_sent(reminders_sent_raw)
        to_send = await schedule_trip_reminders(
            bot=bot,
            chat_id=chat_id,
            trip_id=trip_id,
            trip_title=title,
            destination=destination,
            dates_text=dates_text,
            lang=lang,
        )

        # Mark newly sent reminders
        if to_send:
            new_sent = already_sent | set(to_send)
            try:
                await db._run(
                    lambda: db._update_reminders_sent(trip_id, _serialize_reminders_sent(new_sent))
                )
            except Exception as e:
                logger.warning("Failed to update reminders_sent for trip %d: %s", trip_id, e)

            sent_count += len(to_send)

    if sent_count > 0:
        logger.info("Restored %d reminders on startup", sent_count)

    return sent_count
