"""Trip reminder scheduling and recovery."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from date_utils import is_one_way_trip_text, resolve_trip_dates
from metrics import get_metrics

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)

DEFAULT_BOT_TIMEZONE = "Asia/Tomsk"
REMINDER_SEND_HOUR = 10

REMINDER_TYPES: dict[str, dict[str, object]] = {
    "pre_3d": {"label": "За 3 дня", "days_offset": -3, "ref": "start"},
    "pre_1d": {"label": "За 1 день", "days_offset": -1, "ref": "start"},
    "return_day": {"label": "День возврата", "days_offset": 0, "ref": "end"},
    "post_1d": {"label": "После поездки", "days_offset": 1, "ref": "end"},
}


def _parse_reminders_sent(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return set()
    if isinstance(data, list):
        return {str(item) for item in data}
    return set()


def _serialize_reminders_sent(sent: set[str]) -> str:
    return json.dumps(sorted(sent), ensure_ascii=False)


def _calc_reminder_date(start_date: date, end_date: date | None, reminder_type: str) -> date | None:
    config = REMINDER_TYPES[reminder_type]
    ref = str(config.get("ref", "start"))
    if ref == "end":
        if end_date is None:
            return None
        base_date = end_date
    else:
        base_date = start_date
    return base_date + timedelta(days=int(config["days_offset"]))


def _format_reminder_text(
    trip_title: str,
    destination: str,
    reminder_type: str,
    start_date: date,
    end_date: date | None,
    lang: str,
    *,
    weather_text: str = "",
) -> str:
    """Format reminder text for the given trip event."""
    days_left = (start_date - date.today()).days
    weather_block = f"\n\n{weather_text}" if weather_text else ""

    messages_ru = {
        "pre_3d": (
            f"⏰ Напоминание: через 3 дня поездка «{trip_title}» в {destination}!\n\n"
            f"📋 Чек-лист:\n"
            f"• Проверьте билеты и брони жилья\n"
            f"• Подтвердите документы/загранпаспорта\n"
            f"• Сохраните скриншоты бронирований\n\n"
            f"📅 Вылет: {start_date.strftime('%d.%m.%Y')}\n"
            f"📍 {destination} • {days_left} дн.{weather_block}"
        ),
        "pre_1d": (
            f"🧳 Завтра вылет в {destination}!\n\n"
            f"Не забудьте:\n"
            f"• Документы (паспорт, загран)\n"
            f"• Билеты и брони (скриншоты)\n"
            f"• Зарядки и адаптеры\n"
            f"• Лекарства\n\n"
            f"✈️ Вылет: {start_date.strftime('%d.%m.%Y')}\n"
            f"🏨 Проживание: {'забронировано' if end_date else 'проверьте ссылки из плана'}\n\n"
            f"📍 /summary — полный план поездки{weather_block}"
        ),
        "return_day": (
            f"🏠 Сегодня возвращаетесь из {destination}!\n\n"
            f"📅 Возврат: {(end_date or start_date).strftime('%d.%m.%Y')}\n\n"
            f"Не забудьте:\n"
            f"• Чекины в отеле\n"
            f"• Сувениры\n"
            f"• Проверить ничего ли не забыли\n\n"
            f"Как доберётесь — отметьте статус через /status"
        ),
        "post_1d": (
            f"📝 Надеемся, поездка в {destination} прошла отлично!\n\n"
            f"Расскажите как всё прошло:\n"
            f"• /status — обновите свой ответ\n"
            f"• /summary — посмотрите итоговый план\n"
            f"• /trips — история поездок\n\n"
            f"Если хотите спланировать следующую — /plan ✈️"
        ),
    }
    messages_en = {
        "pre_3d": (
            f"⏰ Reminder: 3 days until \"{trip_title}\" to {destination}!\n\n"
            f"📋 Checklist:\n"
            f"• Check flights and accommodation\n"
            f"• Confirm documents/passports\n"
            f"• Save booking screenshots\n\n"
            f"📅 Departure: {start_date.strftime('%d.%m.%Y')}\n"
            f"📍 {destination} • {days_left} days{weather_block}"
        ),
        "pre_1d": (
            f"🧳 Tomorrow: flight to {destination}!\n\n"
            f"Don't forget:\n"
            f"• Documents (passport)\n"
            f"• Tickets and bookings (screenshots)\n"
            f"• Chargers and adapters\n"
            f"• Medications\n\n"
            f"✈️ Departure: {start_date.strftime('%d.%m.%Y')}\n"
            f"📍 /summary — full trip plan{weather_block}"
        ),
        "return_day": (
            f"🏠 Heading back from {destination} today!\n\n"
            f"📅 Return: {(end_date or start_date).strftime('%d.%m.%Y')}\n\n"
            f"Don't forget:\n"
            f"• Hotel checkout\n"
            f"• Souvenirs\n"
            f"• Double-check you haven't left anything behind\n\n"
            f"Use /status to update your reply when you're back"
        ),
        "post_1d": (
            f"📝 Hope your trip to {destination} was great!\n\n"
            f"Share how it went:\n"
            f"• /status — update your reply\n"
            f"• /summary — view the final plan\n"
            f"• /trips — trip history\n\n"
            f"Want to plan the next one? /plan ✈️"
        ),
    }
    messages = messages_ru if lang == "ru" else messages_en
    return messages.get(reminder_type, f"Напоминание о поездке: {trip_title}")


def _job_name(trip_id: int, reminder_type: str) -> str:
    return f"trip-reminder:{trip_id}:{reminder_type}"


def _resolve_timezone(application: Any) -> ZoneInfo:
    timezone_name = (
        application.bot_data.get("bot_timezone")
        if application and getattr(application, "bot_data", None)
        else None
    ) or DEFAULT_BOT_TIMEZONE
    try:
        return ZoneInfo(str(timezone_name))
    except ZoneInfoNotFoundError:
        logger.warning("Unknown BOT_TIMEZONE=%s, falling back to %s", timezone_name, DEFAULT_BOT_TIMEZONE)
        return ZoneInfo(DEFAULT_BOT_TIMEZONE)


def _current_local_datetime(application: Any) -> datetime:
    return datetime.now(_resolve_timezone(application))


def _combine_run_at(application: Any, reminder_date: date) -> datetime:
    return datetime.combine(
        reminder_date,
        time(hour=REMINDER_SEND_HOUR, minute=0, tzinfo=_resolve_timezone(application)),
    )


def _resolve_trip_window(
    dates_text: str | None,
    days_count: int | None,
    source_text: str = "",
) -> tuple[date | None, date | None]:
    start_date, end_date = resolve_trip_dates(dates_text, days_count)
    if is_one_way_trip_text(source_text, dates_text):
        return start_date, None
    return start_date, end_date


def _build_job_payload(
    *,
    chat_id: int,
    trip_id: int,
    trip_title: str,
    destination: str,
    reminder_type: str,
    start_date: date,
    end_date: date | None,
    lang: str,
) -> dict[str, object]:
    return {
        "chat_id": chat_id,
        "trip_id": trip_id,
        "trip_title": trip_title,
        "destination": destination,
        "reminder_type": reminder_type,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat() if end_date else None,
        "lang": lang,
    }


def _remove_existing_jobs(job_queue: Any, name: str) -> None:
    if not hasattr(job_queue, "get_jobs_by_name"):
        return
    for job in job_queue.get_jobs_by_name(name):
        try:
            job.schedule_removal()
        except Exception:
            logger.debug("Failed to remove existing reminder job %s", name, exc_info=True)


async def _load_weather_text(destination: str, start_iso: str | None) -> str:
    if not start_iso:
        return ""
    try:
        from services.weather import get_forecast_for_city

        text = (await get_forecast_for_city(destination, start_iso) or "").strip()
        if text.startswith("❌"):
            logger.debug(
                "Suppressing weather error text in reminder for %r on %s: %s",
                destination,
                start_iso,
                text,
            )
            return ""
        return text
    except Exception as exc:
        logger.debug("Forecast fetch for reminders failed for %r on %s: %s", destination, start_iso, exc)
        return ""


async def _send_reminder_message(bot: "Bot", payload: dict[str, object]) -> bool:
    chat_id = int(payload["chat_id"])
    trip_title = str(payload["trip_title"])
    destination = str(payload["destination"])
    reminder_type = str(payload["reminder_type"])
    start_date = date.fromisoformat(str(payload["start_date"]))
    end_raw = payload.get("end_date")
    end_date = date.fromisoformat(str(end_raw)) if end_raw else None
    lang = str(payload.get("lang") or "ru")
    weather_text = await _load_weather_text(destination, str(payload.get("start_date") or ""))
    text = _format_reminder_text(
        trip_title,
        destination,
        reminder_type,
        start_date,
        end_date,
        lang,
        weather_text=weather_text,
    )
    await bot.send_message(chat_id=chat_id, text=text)
    return True


async def _mark_reminder_sent(db: Any, trip_id: int, reminder_type: str) -> None:
    trip = await db._run(db.get_trip_by_id, trip_id)
    if not trip:
        return
    already_sent = _parse_reminders_sent(trip.get("reminders_sent"))
    if reminder_type in already_sent:
        return
    already_sent.add(reminder_type)
    await db._run(lambda: db.update_reminders_sent(trip_id, _serialize_reminders_sent(already_sent)))


async def run_scheduled_reminder(context) -> None:
    """PTB JobQueue callback for scheduled trip reminders."""
    payload = dict(getattr(context.job, "data", {}) or {})
    trip_id = int(payload.get("trip_id", 0))
    reminder_type = str(payload.get("reminder_type", ""))
    db = context.application.bot_data.get("db") if getattr(context, "application", None) else None

    try:
        await _send_reminder_message(context.bot, payload)
    except Exception as exc:
        logger.warning("Failed to send reminder: trip=%s type=%s error=%s", trip_id, reminder_type, exc)
        get_metrics().increment("reminders.failed")
        return

    get_metrics().increment("reminders.sent")
    if db and trip_id and reminder_type:
        try:
            await _mark_reminder_sent(db, trip_id, reminder_type)
        except Exception:
            logger.warning("Failed to update reminders_sent for trip %d", trip_id, exc_info=True)


async def schedule_trip_reminders(
    application: Any,
    *,
    chat_id: int,
    trip_id: int,
    trip_title: str,
    destination: str,
    dates_text: str,
    days_count: int | None,
    source_text: str = "",
    lang: str = "ru",
    already_sent: set[str] | None = None,
) -> list[str]:
    """
    Schedule future reminders for a trip via PTB JobQueue.

    Returns the reminder types that were queued.
    """
    if not application or not getattr(application, "job_queue", None):
        logger.warning("JobQueue unavailable, skipping reminder scheduling for trip %d", trip_id)
        return []

    start_date, end_date = _resolve_trip_window(dates_text, days_count, source_text)
    if start_date is None:
        logger.debug("No dates for trip %d, skipping reminders", trip_id)
        return []

    sent = already_sent or set()
    now = _current_local_datetime(application)
    queued: list[str] = []

    for reminder_type in REMINDER_TYPES:
        if reminder_type in sent:
            continue
        reminder_date = _calc_reminder_date(start_date, end_date, reminder_type)
        if reminder_date is None:
            continue
        run_at = _combine_run_at(application, reminder_date)
        if run_at <= now:
            continue

        job_name = _job_name(trip_id, reminder_type)
        _remove_existing_jobs(application.job_queue, job_name)
        payload = _build_job_payload(
            chat_id=chat_id,
            trip_id=trip_id,
            trip_title=trip_title,
            destination=destination,
            reminder_type=reminder_type,
            start_date=start_date,
            end_date=end_date,
            lang=lang,
        )
        application.job_queue.run_once(
            run_scheduled_reminder,
            when=run_at,
            name=job_name,
            data=payload,
        )
        queued.append(reminder_type)

    return queued


async def restore_reminders_on_startup(application: Any, db: Any) -> int:
    """
    Rebuild reminder jobs on startup and send only today's missed reminders once.

    Older missed reminders are marked as handled without sending to avoid spam.
    """
    if not application:
        return 0

    try:
        trips = await db._run(lambda: db.get_all_active_trips_with_reminders())
    except Exception as exc:
        logger.warning("Failed to load trips for reminder restore: %s", exc)
        return 0

    now = _current_local_datetime(application)
    sent_count = 0

    for trip in trips:
        trip_id = int(trip["id"])
        already_sent = _parse_reminders_sent(trip.get("reminders_sent"))
        source_text = "\n".join(
            part for part in (trip.get("source_prompt"), trip.get("notes")) if part
        )
        start_date, end_date = _resolve_trip_window(
            trip.get("dates_text"),
            trip.get("days_count"),
            source_text,
        )
        if start_date is None:
            continue

        updated_sent = set(already_sent)
        trip_lang = str(trip.get("language_code") or "ru")

        for reminder_type in REMINDER_TYPES:
            if reminder_type in updated_sent:
                continue

            reminder_date = _calc_reminder_date(start_date, end_date, reminder_type)
            if reminder_date is None:
                continue

            run_at = _combine_run_at(application, reminder_date)
            if run_at > now:
                await schedule_trip_reminders(
                    application,
                    chat_id=int(trip["chat_id"]),
                    trip_id=trip_id,
                    trip_title=str(trip.get("title") or f"Поездка #{trip_id}"),
                    destination=str(trip.get("destination") or ""),
                    dates_text=str(trip.get("dates_text") or ""),
                    days_count=int(trip.get("days_count") or 0) or None,
                    source_text=source_text,
                    lang=trip_lang,
                    already_sent=updated_sent,
                )
                continue

            updated_sent.add(reminder_type)
            if run_at.date() == now.date():
                payload = _build_job_payload(
                    chat_id=int(trip["chat_id"]),
                    trip_id=trip_id,
                    trip_title=str(trip.get("title") or f"Поездка #{trip_id}"),
                    destination=str(trip.get("destination") or ""),
                    reminder_type=reminder_type,
                    start_date=start_date,
                    end_date=end_date,
                    lang=trip_lang,
                )
                try:
                    await _send_reminder_message(application.bot, payload)
                    get_metrics().increment("reminders.sent")
                    sent_count += 1
                except Exception as exc:
                    updated_sent.discard(reminder_type)
                    logger.warning(
                        "Failed to send restored reminder: trip=%d type=%s error=%s",
                        trip_id,
                        reminder_type,
                        exc,
                    )
                    get_metrics().increment("reminders.failed")

        if updated_sent != already_sent:
            try:
                await db._run(lambda: db.update_reminders_sent(trip_id, _serialize_reminders_sent(updated_sent)))
            except Exception:
                logger.warning("Failed to update reminders_sent for trip %d", trip_id, exc_info=True)

    if sent_count:
        logger.info("Restored %d reminders on startup", sent_count)
    return sent_count
