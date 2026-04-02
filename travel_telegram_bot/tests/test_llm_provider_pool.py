import asyncio
from unittest.mock import patch

from llm_provider_pool import LLMProvider, LLMProviderPool, build_provider_list
from llm_travel_planner import LLMTravelPlanner
from openrouter_client import OpenRouterError
from travel_planner import TravelPlanner


def test_build_provider_list_orders_by_daily_limit() -> None:
    providers = build_provider_list(
        openrouter_api_key="openrouter-key",
        openrouter_model="custom-model",
        openrouter_web_search=True,
        gemini_api_key="gemini-key",
        groq_api_key="groq-key",
    )

    assert [provider.name for provider in providers] == ["Groq", "Gemini", "OpenRouter"]
    assert [provider.daily_limit for provider in providers] == [14400, 1500, 500]
    assert providers[2].model == "custom-model"


def test_provider_pool_rotates_in_round_robin_order() -> None:
    pool = LLMProviderPool(
        [
            LLMProvider("Groq", 14400, "groq-key", "https://groq.example", "groq-model"),
            LLMProvider("Gemini", 1500, "gemini-key", "https://gemini.example", "gemini-model"),
            LLMProvider("OpenRouter", 500, "openrouter-key", "https://openrouter.example", "openrouter-model"),
        ]
    )

    async def collect_names() -> list[str]:
        return [(await pool.get_next()).name for _ in range(5)]

    assert asyncio.run(collect_names()) == ["Groq", "Gemini", "OpenRouter", "Groq", "Gemini"]


def test_llm_travel_planner_async_uses_round_robin_primary_and_fallback() -> None:
    pool = LLMProviderPool(
        [
            LLMProvider("Groq", 14400, "groq-key", "https://groq.example", "groq-model"),
            LLMProvider("Gemini", 1500, "gemini-key", "https://gemini.example", "gemini-model"),
            LLMProvider("OpenRouter", 500, "openrouter-key", "https://openrouter.example", "openrouter-model"),
        ]
    )
    planner = LLMTravelPlanner(pool)
    request = TravelPlanner().build_request_from_fields(
        title="Paris trip",
        destination="Paris",
        origin="Berlin",
        dates_text="12-14 June",
        days_count=3,
        group_size=2,
        budget_text="mid-range",
        interests_text="city, food",
        notes="",
        source_prompt="Plan a Paris trip",
        language_code="en",
    )
    expected_plan = TravelPlanner().generate_plan(request)
    attempted: list[str] = []

    def fake_generate(provider, _request):
        attempted.append(provider.name)
        if provider.name == "Groq":
            raise OpenRouterError("rate limit")
        return expected_plan

    async def run_test():
        with patch("llm_travel_planner.generate_trip_plan_with_provider", side_effect=fake_generate):
            return await planner.generate_plan_llm_async(request)

    plan = asyncio.run(run_test())

    assert plan == expected_plan
    assert attempted == ["Groq", "Gemini"]
