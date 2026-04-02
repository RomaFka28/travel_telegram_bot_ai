import threading

from database import Database
from travel_planner import TravelPlanner


def _sample_payload(destination: str = "Казань") -> dict[str, object]:
    planner = TravelPlanner()
    request = planner.build_request_from_fields(
        title=f"{destination} • 3 дн. • 2 чел.",
        destination=destination,
        origin="Москва",
        dates_text="июнь",
        days_count=3,
        group_size=2,
        budget_text="средний",
        interests_text="еда, прогулки",
        notes="",
        source_prompt=f"Поездка в {destination}",
    )
    plan = planner.generate_plan(request)
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
        "context_text": plan.context_text,
        "itinerary_text": plan.itinerary_text,
        "logistics_text": plan.logistics_text,
        "stay_text": plan.stay_text,
        "alternatives_text": plan.alternatives_text,
        "budget_breakdown_text": plan.budget_breakdown_text,
        "budget_total_text": plan.budget_total_text,
    }


def test_create_trip_archives_previous_without_deleting_history(tmp_path) -> None:
    database = Database(str(tmp_path / "history.db"))
    database.init_db()

    first_trip_id = database.create_trip(chat_id=7, created_by=1, payload=_sample_payload("Казань"))
    second_trip_id = database.create_trip(chat_id=7, created_by=1, payload=_sample_payload("Сочи"))

    active_trip = database.get_active_trip(7)
    all_trips = database.list_trips(7)

    assert active_trip is not None
    assert active_trip["id"] == second_trip_id
    assert len(all_trips) == 2
    archived_trip = next(trip for trip in all_trips if trip["id"] == first_trip_id)
    assert archived_trip["status"] == "archived"


def test_archive_active_trip_keeps_trip_in_database(tmp_path) -> None:
    database = Database(str(tmp_path / "archive.db"))
    database.init_db()

    trip_id = database.create_trip(chat_id=11, created_by=1, payload=_sample_payload("Владивосток"))
    assert database.archive_active_trip(11) is True

    archived_trip = database.get_trip_by_id(trip_id)
    assert archived_trip is not None
    assert archived_trip["status"] == "archived"
    assert database.get_active_trip(11) is None


def test_chat_settings_include_autodraft_toggle(tmp_path) -> None:
    database = Database(str(tmp_path / "settings.db"))
    database.init_db()

    settings = database.get_or_create_settings(42)
    assert bool(settings["reminders_enabled"]) is True
    assert bool(settings["autodraft_enabled"]) is True
    assert settings["language_code"] == "ru"
    assert bool(settings["language_selected"]) is False

    settings = database.toggle_autodraft(42)
    assert bool(settings["autodraft_enabled"]) is False

    settings = database.toggle_reminders(42)
    assert bool(settings["reminders_enabled"]) is False


def test_chat_language_can_be_saved(tmp_path) -> None:
    database = Database(str(tmp_path / "language.db"))
    database.init_db()

    settings = database.set_chat_language(43, "en")

    assert settings["language_code"] == "en"
    assert bool(settings["language_selected"]) is True
    assert database.get_chat_language(43) == "en"


def test_activate_trip_restores_archived_trip(tmp_path) -> None:
    database = Database(str(tmp_path / "activate.db"))
    database.init_db()

    first_trip_id = database.create_trip(chat_id=88, created_by=1, payload=_sample_payload("Казань"))
    second_trip_id = database.create_trip(chat_id=88, created_by=1, payload=_sample_payload("Сочи"))

    assert database.activate_trip(88, first_trip_id) is True

    active_trip = database.get_active_trip(88)
    second_trip = database.get_trip_by_id(second_trip_id)
    assert active_trip is not None
    assert active_trip["id"] == first_trip_id
    assert second_trip is not None
    assert second_trip["status"] == "archived"


def test_trip_schema_keeps_structured_result_columns(tmp_path) -> None:
    database = Database(str(tmp_path / "structured.db"))
    database.init_db()

    trip_id = database.create_trip(
        chat_id=91,
        created_by=1,
        payload={
            **_sample_payload("Казань"),
            "flight_results": '[{"title":"Тест","price_text":"12 300 ₽","url":"https://example.com","source":"demo"}]',
            "detected_needs": '["tickets","housing"]',
            "summary_short_text": "Куда: Казань",
        },
    )

    trip = database.get_trip_by_id(trip_id)
    assert trip is not None
    assert "Тест" in (trip["flight_results"] or "")
    assert "housing" in (trip["detected_needs"] or "")
    assert trip["summary_short_text"] == "Куда: Казань"


def test_chat_member_tracking_counts_known_people(tmp_path) -> None:
    database = Database(str(tmp_path / "members.db"))
    database.init_db()

    database.upsert_chat_member(chat_id=15, user_id=1, username="u1", full_name="User One")
    database.upsert_chat_member(chat_id=15, user_id=2, username="u2", full_name="User Two")
    database.upsert_chat_member(chat_id=15, user_id=1, username="u1", full_name="User One")

    assert database.count_chat_members(15) == 2


def test_delete_trip_removes_trip_completely(tmp_path) -> None:
    database = Database(str(tmp_path / "delete.db"))
    database.init_db()

    trip_id = database.create_trip(chat_id=16, created_by=1, payload=_sample_payload("Казань"))
    assert database.delete_trip(16, trip_id) is True
    assert database.get_trip_by_id(trip_id) is None


def test_sqlite_connection_can_be_used_from_another_thread(tmp_path) -> None:
    database = Database(str(tmp_path / "threadsafe.db"))
    connection = database._connect()
    result: dict[str, object] = {}

    def run_query() -> None:
        try:
            result["value"] = connection.execute("SELECT 1").fetchone()[0]
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=run_query)
    worker.start()
    worker.join()
    connection.close()

    assert result.get("error") is None
    assert result["value"] == 1
