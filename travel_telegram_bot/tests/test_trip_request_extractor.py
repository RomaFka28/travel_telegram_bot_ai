import asyncio
from unittest.mock import AsyncMock, patch

from bot.group_chat_analyzer import GroupChatAnalyzer
from llm_provider_pool import LLMProvider, LLMProviderPool
from llm_travel_planner import LLMTravelPlanner
from openrouter_client import OpenRouterError
from travel_planner import TravelPlanner
from trip_request_extractor import TripRequestExtractor


def test_trip_request_extractor_fallback_preserves_multiple_interests_and_one_way() -> None:
    extractor = TripRequestExtractor(TravelPlanner())

    extraction = extractor.extract(
        "Хочу в Стамбул один, вылет из Тбилиси 12 июня, билет в одну сторону, бюджет Бизнес, интересуют прогулки и еда.",
        language_code="ru",
    )

    assert extraction.destination == "Стамбул"
    assert extraction.origin == "Тбилиси"
    assert extraction.route_type == "one_way"
    assert "tickets" in extraction.needs
    assert "housing" not in extraction.needs
    assert "прогулки" in extraction.interests
    assert "еда" in extraction.interests
    assert extraction.missing_fields == []
    assert extraction.is_actionable is True


def test_trip_request_extractor_reports_missing_ticket_fields() -> None:
    extractor = TripRequestExtractor(TravelPlanner())

    extraction = extractor.extract(
        "Хочу в Стамбул 12 июня, нужен билет, бюджет Бизнес, люблю прогулки и еду.",
        language_code="ru",
    )

    assert extraction.destination == "Стамбул"
    assert "tickets" in extraction.needs
    assert "origin" in extraction.missing_fields
    assert "route_type" in extraction.missing_fields
    assert extraction.is_actionable is False


def test_trip_request_extractor_async_falls_back_when_llm_fails() -> None:
    planner = LLMTravelPlanner(
        LLMProviderPool(
            [
                LLMProvider(
                    name="OpenRouter",
                    daily_limit=500,
                    api_key="token",
                    base_url="https://openrouter.ai/api/v1/chat/completions",
                    model="google/gemini-2.0-flash-exp:free",
                    use_web_search=True,
                )
            ]
        )
    )
    extractor = TripRequestExtractor(planner)

    with patch.object(
        planner,
        "extract_trip_request_async",
        new=AsyncMock(side_effect=OpenRouterError("boom")),
    ):
        extraction = asyncio.run(
            extractor.extract_async(
                "Хочу в Стамбул один, вылет из Тбилиси 12 июня, билет в одну сторону, бюджет Бизнес, интересуют прогулки и еда.",
                language_code="ru",
                planner=planner,
            )
        )

    assert extraction.destination == "Стамбул"
    assert extraction.route_type == "one_way"
    assert "прогулки" in extraction.interests
    assert "еда" in extraction.interests


def test_group_chat_analyzer_uses_request_extractor_normalization() -> None:
    planner = TravelPlanner()
    extractor = TripRequestExtractor(planner)
    analyzer = GroupChatAnalyzer(planner=planner, request_extractor=extractor)

    signal = analyzer.analyze(
        "Давайте в Стамбул, вылет из Тбилиси 12 июня, билет в одну сторону, интересуют прогулки и еда."
    )

    assert signal.destination == "Стамбул"
    assert signal.origin == "Тбилиси"
    assert signal.route_type == "one_way"
    assert "tickets" in signal.detected_needs
    assert "housing" not in signal.detected_needs
    assert "прогулки" in signal.interests
    assert "еда" in signal.interests
