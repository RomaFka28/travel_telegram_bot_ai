from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import Database
from travel_planner import TravelPlanner


def main() -> None:
    planner = TravelPlanner()
    request = planner.parse_trip_request(
        "Хочу поехать с друзьями на 5 дней во Владивосток, нас 4, из Новосибирска, бюджет средний, любим море и еду"
    )
    plan = planner.generate_plan(request)

    database = Database("data/_smoke.db")
    database.init_db()
    trip_id = database.create_trip(
        chat_id=1,
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
    print("OK", trip_id)


if __name__ == "__main__":
    main()

