from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import Database
from travel_planner import TravelPlanner


def test_planner_and_database_smoke() -> None:
    planner = TravelPlanner()
    request = planner.parse_trip_request(
        "Хочу поехать с друзьями на 5 дней во Владивосток, нас 4, из Новосибирска, бюджет средний, любим море и еду"
    )
    assert request.destination == "Владивосток"
    assert request.days_count == 5
    assert request.group_size == 4

    plan = planner.generate_plan(request)
    assert "День 1." in plan.itinerary_text
    assert "Итого ориентир" in plan.budget_breakdown_text

    db_path = ROOT / "data" / "test_suite.db"
    db_path.unlink(missing_ok=True)
    database = Database(str(db_path))
    database.init_db()

    trip_id = database.create_trip(
        chat_id=101,
        created_by=1,
        payload={
            "title": request.title,
            "destination": request.destination,
            "origin": request.origin,
            "dates_text": request.dates_text,
            "days_count": request.days_count,
            "group_size": request.group_size,
            "budget_text": request.budget_text,
            "interests_text": request.interests_text,
            "notes": "",
            "source_prompt": request.source_prompt,
            "context_text": plan.context_text,
            "itinerary_text": plan.itinerary_text,
            "logistics_text": plan.logistics_text,
            "stay_text": plan.stay_text,
            "alternatives_text": plan.alternatives_text,
            "budget_breakdown_text": plan.budget_breakdown_text,
            "budget_total_text": plan.budget_total_text,
        },
    )
    trip = database.get_trip_by_id(trip_id)
    assert trip is not None
    assert trip["destination"] == "Владивосток"
