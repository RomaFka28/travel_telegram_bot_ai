from travel_planner import TravelPlanner


def test_fallback_context_for_foreign_destination_mentions_api_key() -> None:
    planner = TravelPlanner()
    request = planner.build_request_from_fields(
        title="Paris trip",
        destination="Paris",
        origin="Berlin",
        dates_text="12-14 June",
        days_count=3,
        group_size=2,
        budget_text="mid-range",
        interests_text="city, food",
        notes="",
        source_prompt="Plan Paris",
        language_code="en",
    )

    plan = planner.generate_plan(request)

    assert "OPENROUTER_API_KEY" in plan.context_text
    assert "реальных мест" in plan.context_text
