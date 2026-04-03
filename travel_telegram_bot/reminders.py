"""
JobQueue для напоминаний.

Обеспечивает:
- Планирование напоминаний о поездках
- Периодические проверки статуса поездок
- Интеграцию с python-telegram-bot JobQueue

Примечание: При деплое на Render с ephemeral storage напоминания
пропадают при рестарте. Для production нужен внешний планировщик
(Celery, APScheduler с PostgreSQL backend, или Render Cron Jobs).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from metrics import get_metrics

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)


@dataclass
class ReminderJob:
    """Одно задание напоминания."""
    chat_id: int
    trip_id: int
    message: str
    remind_at: datetime
    created_at: datetime = field(default_factory=datetime.utcnow)
    sent: bool = False
    job_id: str | None = None


class ReminderScheduler:
    """
    Планировщик напоминаний.
    
    Поддерживает:
    - Однократные напоминания
    - Периодические напоминания (daily, weekly)
    - Проверку статуса поездок
    
    TODO: Для production использовать внешний планировщик.
    """
    
    def __init__(self) -> None:
        self._jobs: list[ReminderJob] = []
        self._application: Application | None = None
    
    def set_application(self, app: Application) -> None:
        """Устанавливает ссылку на Telegram Application для JobQueue."""
        self._application = app
        logger.info("ReminderScheduler linked to Telegram Application")
    
    def add_reminder(
        self,
        chat_id: int,
        trip_id: int,
        message: str,
        delay_minutes: int,
    ) -> ReminderJob | None:
        """
        Добавляет однократное напоминание.
        
        Args:
            chat_id: ID чата
            trip_id: ID поездки
            message: Текст напоминания
            delay_minutes: Задержка в минутах
        
        Returns:
            ReminderJob или None если JobQueue недоступен
        """
        remind_at = datetime.utcnow() + timedelta(minutes=delay_minutes)
        job = ReminderJob(
            chat_id=chat_id,
            trip_id=trip_id,
            message=message,
            remind_at=remind_at,
        )
        self._jobs.append(job)
        
        if self._application and self._application.job_queue:
            try:
                queue_job = self._application.job_queue.run_once(
                    self._execute_reminder,
                    when=timedelta(minutes=delay_minutes),
                    data={"chat_id": chat_id, "trip_id": trip_id, "message": message},
                    name=f"reminder_{chat_id}_{trip_id}",
                )
                job.job_id = queue_job.id if hasattr(queue_job, 'id') else str(id(queue_job))
                logger.info(
                    "Reminder scheduled: chat=%d trip=%d in %d min (job_id=%s)",
                    chat_id, trip_id, delay_minutes, job.job_id,
                )
            except Exception as e:
                logger.warning(
                    "Failed to schedule reminder: chat=%d trip=%d error=%s",
                    chat_id, trip_id, e,
                )
                return None
        else:
            logger.warning(
                "JobQueue not available, reminder stored in-memory only: chat=%d trip=%d",
                chat_id, trip_id,
            )
        
        get_metrics().increment("reminders.scheduled")
        return job
    
    def add_periodic_reminder(
        self,
        chat_id: int,
        trip_id: int,
        message: str,
        interval_hours: int,
    ) -> ReminderJob | None:
        """
        Добавляет периодическое напоминание.
        
        Args:
            chat_id: ID чата
            trip_id: ID поездки
            message: Текст напоминания
            interval_hours: Интервал в часах
        
        Returns:
            ReminderJob или None если JobQueue недоступен
        """
        if not self._application or not self._application.job_queue:
            logger.warning(
                "JobQueue not available, periodic reminders not supported: chat=%d",
                chat_id,
            )
            return None
        
        job = ReminderJob(
            chat_id=chat_id,
            trip_id=trip_id,
            message=message,
            remind_at=datetime.utcnow(),
        )
        self._jobs.append(job)
        
        try:
            queue_job = self._application.job_queue.run_repeating(
                self._execute_reminder,
                interval=timedelta(hours=interval_hours),
                first=timedelta(hours=1),
                data={"chat_id": chat_id, "trip_id": trip_id, "message": message},
                name=f"periodic_reminder_{chat_id}_{trip_id}",
            )
            job.job_id = queue_job.id if hasattr(queue_job, 'id') else str(id(queue_job))
            logger.info(
                "Periodic reminder scheduled: chat=%d trip=%d every %d h (job_id=%s)",
                chat_id, trip_id, interval_hours, job.job_id,
            )
            get_metrics().increment("reminders.scheduled_periodic")
            return job
        except Exception as e:
            logger.warning(
                "Failed to schedule periodic reminder: chat=%d trip=%d error=%s",
                chat_id, trip_id, e,
            )
            return None
    
    async def _execute_reminder(self, context: Any) -> None:
        """Выполняет напоминание."""
        data = context.job.data if hasattr(context, 'job') and hasattr(context.job, 'data') else {}
        chat_id = data.get("chat_id")
        trip_id = data.get("trip_id")
        message = data.get("message", "Напоминание о поездке")
        
        if not chat_id or not trip_id:
            logger.warning("Reminder job missing data: %s", data)
            return
        
        logger.info("Executing reminder: chat=%d trip=%d", chat_id, trip_id)
        get_metrics().increment("reminders.executed")
        
        # Пометить как отправленное
        for job in self._jobs:
            if job.chat_id == chat_id and job.trip_id == trip_id and not job.sent:
                job.sent = True
                break
        
        # Отправить сообщение (через application bot)
        if self._application:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                )
                get_metrics().increment("reminders.sent")
            except Exception as e:
                logger.warning(
                    "Failed to send reminder: chat=%d trip=%d error=%s",
                    chat_id, trip_id, e,
                )
                get_metrics().increment("reminders.failed")
    
    def cancel_reminder(self, chat_id: int, trip_id: int) -> bool:
        """Отменяет напоминание."""
        cancelled = False
        for job in self._jobs[:]:
            if job.chat_id == chat_id and job.trip_id == trip_id and not job.sent:
                job.sent = True  # Пометить как обработанное
                cancelled = True
                logger.info("Reminder cancelled: chat=%d trip=%d", chat_id, trip_id)
        
        # TODO: Отменить job в JobQueue если нужно
        # if self._application and self._application.job_queue:
        #     ...
        
        get_metrics().increment("reminders.cancelled")
        return cancelled
    
    def get_pending_reminders(self, chat_id: int) -> list[ReminderJob]:
        """Получает ожидающие напоминания для чата."""
        return [
            job for job in self._jobs
            if job.chat_id == chat_id and not job.sent
        ]
    
    def get_stats(self) -> dict[str, int]:
        """Получает статистику напоминаний."""
        return {
            "total": len(self._jobs),
            "sent": sum(1 for j in self._jobs if j.sent),
            "pending": sum(1 for j in self._jobs if not j.sent),
        }


# Глобальный планировщик
_reminder_scheduler: ReminderScheduler | None = None


def get_reminder_scheduler() -> ReminderScheduler:
    """Получает глобальный планировщик напоминаний."""
    global _reminder_scheduler
    if _reminder_scheduler is None:
        _reminder_scheduler = ReminderScheduler()
    return _reminder_scheduler


def reset_reminder_scheduler() -> None:
    """Сбрасывает глобальный планировщик (для тестов)."""
    global _reminder_scheduler
    _reminder_scheduler = None
