from openrouter_client import OpenRouterConfig, _build_request_headers, build_trip_plan_payload
from travel_planner import TravelPlanner


def make_request(language_code: str = "en"):
    return TravelPlanner().build_request_from_fields(
        title="Test trip",
        destination="Kazan" if language_code == "en" else "Казань",
        origin="Tomsk" if language_code == "en" else "Томск",
        dates_text="12-14 June" if language_code == "en" else "12-14 июня",
        days_count=3,
        group_size=4,
        budget_text="mid-range" if language_code == "en" else "средний",
        interests_text="food, walks, history" if language_code == "en" else "еда, прогулки, история",
        notes="need a quick draft trip plan" if language_code == "en" else "нужен быстрый черновик поездки",
        source_prompt="Plan Kazan" if language_code == "en" else "Хочу в Казань",
        language_code=language_code,
    )


def test_build_trip_plan_payload_enables_web_search_plugin() -> None:
    payload = build_trip_plan_payload(
        OpenRouterConfig(
            api_key="test-key",
            model="stepfun/step-3.5-flash:free",
            use_web_search=True,
            web_max_results=4,
        ),
        make_request("en"),
    )

    assert payload["model"] == "stepfun/step-3.5-flash:free"
    assert payload["plugins"] == [{"id": "web", "max_results": 4}]
    assert payload["messages"][0]["role"] == "system"
    assert "Return only valid JSON" in payload["messages"][0]["content"]
    assert "real specific place names" in payload["messages"][0]["content"]
    assert "Matterhorn viewpoint at Gornergrat" in payload["messages"][1]["content"]
    assert "destination: Kazan" in payload["messages"][1]["content"]


def test_build_trip_plan_payload_can_disable_web_search_plugin() -> None:
    payload = build_trip_plan_payload(
        OpenRouterConfig(
            api_key="test-key",
            use_web_search=False,
        ),
        make_request("en"),
    )

    assert "plugins" not in payload


def test_build_trip_plan_payload_does_not_send_plugins_to_gemini_or_groq() -> None:
    gemini_payload = build_trip_plan_payload(
        OpenRouterConfig(
            api_key="test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            model="gemini-2.0-flash",
            use_web_search=True,
        ),
        make_request("en"),
    )
    groq_payload = build_trip_plan_payload(
        OpenRouterConfig(
            api_key="test-key",
            base_url="https://api.groq.com/openai/v1/chat/completions",
            model="llama-3.3-70b-versatile",
            use_web_search=True,
        ),
        make_request("en"),
    )

    assert "plugins" not in gemini_payload
    assert "plugins" not in groq_payload


def test_build_request_headers_include_standard_user_agent() -> None:
    headers = _build_request_headers("test-key")

    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"] == "travel-telegram-bot/1.0"


def test_build_trip_plan_payload_russian_prompt_mentions_real_named_places() -> None:
    payload = build_trip_plan_payload(
        OpenRouterConfig(api_key="test-key"),
        make_request("ru"),
    )

    assert "Reply in Russian" in payload["messages"][0]["content"]
    assert "actual museum name" in payload["messages"][0]["content"]
    assert "REAL named places" in payload["messages"][1]["content"]
