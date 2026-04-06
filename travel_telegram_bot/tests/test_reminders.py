import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from reminders import _load_weather_text, restore_reminders_on_startup, run_scheduled_reminder, schedule_trip_reminders


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append({"chat_id": chat_id, "text": text})


class FakeJob:
    def __init__(self, *, callback, when, name: str, data: dict[str, object]) -> None:
        self.callback = callback
        self.when = when
        self.name = name
        self.data = data
        self.removed = False

    def schedule_removal(self) -> None:
        self.removed = True


class FakeJobQueue:
    def __init__(self) -> None:
        self.jobs: list[FakeJob] = []

    def get_jobs_by_name(self, name: str) -> list[FakeJob]:
        return [job for job in self.jobs if job.name == name and not job.removed]

    def run_once(self, callback, when, name: str, data: dict[str, object]) -> FakeJob:
        job = FakeJob(callback=callback, when=when, name=name, data=data)
        self.jobs.append(job)
        return job


class FakeDB:
    def __init__(self) -> None:
        self.trips: dict[int, dict[str, object]] = {}

    async def _run(self, func, *args):
        return func(*args)

    def get_trip_by_id(self, trip_id: int) -> dict[str, object] | None:
        trip = self.trips.get(trip_id)
        return dict(trip) if trip else None

    def update_reminders_sent(self, trip_id: int, reminders_sent_json: str) -> bool:
        trip = self.trips.get(trip_id)
        if not trip:
            return False
        trip["reminders_sent"] = reminders_sent_json
        return True

    def get_all_active_trips_with_reminders(self) -> list[dict[str, object]]:
        return [dict(trip) for trip in self.trips.values() if trip.get("status") == "active"]


class FakeApplication:
    def __init__(self, db: FakeDB) -> None:
        self.bot = FakeBot()
        self.job_queue = FakeJobQueue()
        self.bot_data = {"db": db, "bot_timezone": "Asia/Tomsk"}


def _create_trip(db: FakeDB, *, trip_id: int = 42, notes: str, reminders_sent: str = "[]") -> int:
    db.trips[trip_id] = {
        "id": trip_id,
        "chat_id": 777,
        "title": "Томск • 3 дн.",
        "destination": "Томск",
        "origin": "Иркутск",
        "dates_text": "12 июня",
        "days_count": 3,
        "group_size": 2,
        "budget_text": "эконом",
        "interests_text": "не указаны",
        "notes": notes,
        "source_prompt": notes,
        "reminders_sent": reminders_sent,
        "status": "active",
        "language_code": "ru",
    }
    return trip_id


def test_schedule_trip_reminders_queues_future_jobs_without_immediate_send(monkeypatch) -> None:
    db = FakeDB()
    app = FakeApplication(db)

    monkeypatch.setattr(
        "reminders._current_local_datetime",
        lambda application: datetime(2026, 4, 6, 9, 0, tzinfo=ZoneInfo("Asia/Tomsk")),
    )

    queued = asyncio.run(
        schedule_trip_reminders(
            app,
            chat_id=777,
            trip_id=42,
            trip_title="Томск • 3 дн.",
            destination="Томск",
            dates_text="12 июня",
            days_count=3,
            source_text="туда-обратно, квартира",
            lang="ru",
        )
    )

    assert queued == ["pre_3d", "pre_1d", "return_day", "post_1d"]
    assert app.bot.sent_messages == []
    assert len(app.job_queue.jobs) == 4
    assert app.job_queue.jobs[2].data["end_date"] == "2026-06-14"
    assert app.job_queue.jobs[3].when.date().isoformat() == "2026-06-15"


def test_run_scheduled_reminder_marks_reminder_as_sent_after_success(monkeypatch) -> None:
    db = FakeDB()
    app = FakeApplication(db)
    trip_id = _create_trip(db, notes="туда-обратно, квартира")

    monkeypatch.setattr("reminders._load_weather_text", lambda destination, start_iso: asyncio.sleep(0, result=""))

    asyncio.run(
        schedule_trip_reminders(
            app,
            chat_id=777,
            trip_id=trip_id,
            trip_title="Томск • 3 дн.",
            destination="Томск",
            dates_text="12 июня",
            days_count=3,
            source_text="туда-обратно, квартира",
            lang="ru",
        )
    )

    job = app.job_queue.jobs[0]
    context = SimpleNamespace(job=job, application=app, bot=app.bot)
    asyncio.run(run_scheduled_reminder(context))

    trip = db.get_trip_by_id(trip_id)
    assert trip is not None
    assert len(app.bot.sent_messages) == 1
    assert "pre_3d" in str(trip["reminders_sent"])


def test_restore_reminders_sends_only_today_missed_and_marks_old_ones(monkeypatch) -> None:
    db = FakeDB()
    app = FakeApplication(db)
    trip_id = _create_trip(db, notes="туда-обратно, квартира")

    monkeypatch.setattr(
        "reminders._current_local_datetime",
        lambda application: datetime(2026, 6, 14, 15, 0, tzinfo=ZoneInfo("Asia/Tomsk")),
    )
    monkeypatch.setattr("reminders._load_weather_text", lambda destination, start_iso: asyncio.sleep(0, result=""))

    sent_count = asyncio.run(restore_reminders_on_startup(app, db))

    trip = db.get_trip_by_id(trip_id)
    assert trip is not None
    assert sent_count == 1
    assert len(app.bot.sent_messages) == 1
    assert "Сегодня возвращаетесь" in str(app.bot.sent_messages[0]["text"])
    assert "pre_3d" in str(trip["reminders_sent"])
    assert "pre_1d" in str(trip["reminders_sent"])
    assert "return_day" in str(trip["reminders_sent"])
    assert "post_1d" not in str(trip["reminders_sent"])
    assert len(app.job_queue.jobs) == 1
    assert app.job_queue.jobs[0].data["reminder_type"] == "post_1d"


def test_load_weather_text_hides_weather_service_error_text() -> None:
    with patch(
        "services.weather.get_forecast_for_city",
        new=AsyncMock(return_value="❌ Сервис погоды недоступен. Попробуйте позже."),
    ):
        weather_text = asyncio.run(_load_weather_text("Томск", "2026-06-12"))

    assert weather_text == ""
