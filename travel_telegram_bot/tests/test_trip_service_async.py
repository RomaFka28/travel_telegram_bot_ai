import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot.trip_service as service_module
from bot.trip_service import TripService
from database import Database
from travel_planner import TravelPlanner


def build_service(tmp_path) -> tuple[Database, TravelPlanner, TripService]:
    database = Database(str(tmp_path / "trip_service.db"))
    database.init_db()
    planner = TravelPlanner()
    service = TripService(database, planner)
    return database, planner, service


def create_trip(database: Database, chat_id: int = 1) -> int:
    return database.create_trip(
        chat_id=chat_id,
        created_by=1,
        payload={
            "title": "Казань • 3 дн.",
            "destination": "Казань",
            "origin": "Томск",
            "dates_text": "12–14 июня",
            "days_count": 3,
            "group_size": 2,
            "budget_text": "средний",
            "interests_text": "город, еда",
            "notes": "заметки",
            "source_prompt": "Хочу в Казань",
            "status": "active",
        },
    )


def test_refresh_weather_for_trip_uses_to_thread(tmp_path) -> None:
    database, _, service = build_service(tmp_path)
    trip_id = create_trip(database)

    async def fake_to_thread(func, *args, **kwargs):
        assert func.__name__ == "fetch_weather_summary"
        assert args == ("Казань", "12–14 июня")
        return "Солнечно"

    with patch("bot.trip_service.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)) as to_thread_mock:
        asyncio.run(service._refresh_weather_for_trip(trip_id))

    trip = database.get_trip_by_id(trip_id)
    assert trip is not None
    assert trip["weather_text"] == "Солнечно"
    assert trip["weather_updated_at"] is not None
    assert to_thread_mock.await_count == 1


def test_refresh_weather_for_trip_clears_stale_weather_when_summary_is_missing(tmp_path) -> None:
    database, _, service = build_service(tmp_path)
    trip_id = create_trip(database)
    database.update_trip_fields(
        trip_id,
        {
            "weather_text": "stale forecast",
            "weather_updated_at": "2026-01-01T00:00:00",
        },
    )

    async def fake_to_thread(func, *args, **kwargs):
        assert func.__name__ == "fetch_weather_summary"
        return None

    with patch("bot.trip_service.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)):
        asyncio.run(service._refresh_weather_for_trip(trip_id))

    trip = database.get_trip_by_id(trip_id)
    assert trip is not None
    assert trip["weather_text"] is None
    assert trip["weather_updated_at"] is None


def test_refresh_weather_for_trip_clears_stale_weather_on_weather_error(tmp_path) -> None:
    database, _, service = build_service(tmp_path)
    trip_id = create_trip(database)
    database.update_trip_fields(
        trip_id,
        {
            "weather_text": "stale forecast",
            "weather_updated_at": "2026-01-01T00:00:00",
        },
    )

    async def fake_to_thread(func, *args, **kwargs):
        raise service_module.WeatherError("boom")

    with patch("bot.trip_service.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)):
        asyncio.run(service._refresh_weather_for_trip(trip_id))

    trip = database.get_trip_by_id(trip_id)
    assert trip is not None
    assert trip["weather_text"] is None
    assert trip["weather_updated_at"] is None


def test_rebuild_trip_uses_to_thread_for_plan_and_payload(tmp_path) -> None:
    database, planner, service = build_service(tmp_path)
    trip_id = create_trip(database)
    plan = SimpleNamespace()

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(planner, "generate_plan", return_value=plan) as generate_mock, patch.object(
        service,
        "_build_trip_payload",
        return_value={"notes": "пересобрано"},
    ) as payload_mock, patch("bot.trip_service.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)) as to_thread_mock:
        asyncio.run(service._rebuild_trip(trip_id))

    generate_mock.assert_called_once()
    payload_mock.assert_called_once()
    assert to_thread_mock.await_count == 2
    rebuilt_trip = database.get_trip_by_id(trip_id)
    assert rebuilt_trip is not None
    assert rebuilt_trip["notes"] == "пересобрано"


def test_auto_draft_from_signal_uses_to_thread_and_awaits_weather_refresh(tmp_path) -> None:
    database, planner, service = build_service(tmp_path)
    signal = SimpleNamespace(
        destination="Казань",
        group_size=4,
        participants_mentioned=["a", "b", "c", "d"],
        days_count=3,
        budget_hint="средний",
        interests=["еда"],
        raw_text="Едем в Казань",
        origin="Томск",
        dates_text="12–14 июня",
    )
    plan = SimpleNamespace()

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with patch.object(planner, "generate_plan", return_value=plan) as generate_mock, patch.object(
        service,
        "_build_trip_payload",
        return_value={"title": "Казань • 3 дн.", "destination": "Казань"},
    ) as payload_mock, patch.object(
        service,
        "_refresh_weather_for_trip",
        new=AsyncMock(),
    ) as refresh_mock, patch("bot.trip_service.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)) as to_thread_mock:
        trip_id = asyncio.run(service.auto_draft_from_signal(chat_id=2, created_by=7, signal=signal))

    assert trip_id is not None
    generate_mock.assert_called_once()
    payload_mock.assert_called_once()
    refresh_mock.assert_awaited_once_with(trip_id)
    assert to_thread_mock.await_count == 2


def test_build_entry_notice_is_short_and_requests_only_basic_identity(tmp_path) -> None:
    _, planner, service = build_service(tmp_path)
    request = planner.build_request_from_fields(
        title="Стамбул",
        destination="Стамбул",
        origin="Тбилиси",
        dates_text="12 июня",
        days_count=3,
        group_size=1,
        budget_text="бизнес",
        interests_text="еда",
        notes="",
        source_prompt="Хочу в Стамбул",
        language_code="ru",
    )

    notice = service._build_entry_notice(request)

    assert "гражданство" in notice
    assert "по какому документу" in notice
    assert "срок действия паспорта" not in notice
    assert "дополнительные документы" not in notice
