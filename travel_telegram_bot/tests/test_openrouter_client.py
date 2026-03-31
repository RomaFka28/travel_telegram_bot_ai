from openrouter_client import OpenRouterConfig, build_trip_plan_payload
from travel_planner import TripRequest


def make_request() -> TripRequest:
    return TripRequest(
        destination="Казань",
        origin="Томск",
        dates_text="12-14 июня",
        days_count=3,
        group_size=4,
        budget_text="средний",
        interests_text="еда, прогулки, история",
        notes="нужен быстрый черновик поездки",
    )


def test_build_trip_plan_payload_enables_web_search_plugin() -> None:
    payload = build_trip_plan_payload(
        OpenRouterConfig(
            api_key="test-key",
            model="stepfun/step-3.5-flash:free",
            use_web_search=True,
            web_max_results=4,
        ),
        make_request(),
    )

    assert payload["model"] == "stepfun/step-3.5-flash:free"
    assert payload["plugins"] == [{"id": "web", "max_results": 4}]
    assert payload["messages"][0]["role"] == "system"
    assert "Return only valid JSON" in payload["messages"][0]["content"]
    assert "destination: Казань" in payload["messages"][1]["content"]


def test_build_trip_plan_payload_can_disable_web_search_plugin() -> None:
    payload = build_trip_plan_payload(
        OpenRouterConfig(
            api_key="test-key",
            use_web_search=False,
        ),
        make_request(),
    )

    assert "plugins" not in payload
