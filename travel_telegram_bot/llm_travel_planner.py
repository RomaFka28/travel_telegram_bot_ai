from __future__ import annotations

import asyncio
import logging

from llm_provider_pool import LLMProviderPool
from openrouter_client import (
    OpenRouterConfig,
    OpenRouterError,
    classify_budget_text,
    generate_trip_plan_with_provider,
)
from travel_planner import BudgetInterpretation, TripPlan, TripRequest, TravelPlanner

logger = logging.getLogger(__name__)


class LLMTravelPlanner(TravelPlanner):
    def __init__(self, pool: LLMProviderPool) -> None:
        super().__init__()
        self._pool = pool

    def generate_plan_llm(self, request: TripRequest) -> TripPlan:
        providers = self._pool.all_providers()
        last_error: Exception | None = None

        for provider in providers:
            try:
                logger.info("Trying LLM provider: %s", provider.name)
                return generate_trip_plan_with_provider(provider, request)
            except OpenRouterError as exc:
                logger.warning("Provider %s failed: %s", provider.name, exc)
                last_error = exc
                continue

        raise OpenRouterError(
            f"All {len(providers)} LLM providers failed. Last error: {last_error}"
        )

    async def generate_plan_llm_async(self, request: TripRequest) -> TripPlan:
        providers = self._pool.all_providers()
        primary = await self._pool.get_next()
        ordered = [primary] + [provider for provider in providers if provider.name != primary.name]
        last_error: Exception | None = None

        for provider in ordered:
            try:
                logger.info("Trying LLM provider (async): %s", provider.name)
                return await asyncio.to_thread(generate_trip_plan_with_provider, provider, request)
            except OpenRouterError as exc:
                logger.warning("Provider %s failed: %s", provider.name, exc)
                last_error = exc
                continue

        raise OpenRouterError(
            f"All {len(providers)} LLM providers failed. Last error: {last_error}"
        )

    async def generate_plan_async(self, request: TripRequest) -> TripPlan:
        try:
            return await self.generate_plan_llm_async(request)
        except OpenRouterError:
            logger.exception("All LLM providers failed in async path, falling back to heuristic planner")
            return await asyncio.to_thread(self.generate_plan_heuristic, request)

    def interpret_budget_text(self, text: str) -> BudgetInterpretation:
        heuristic = self._interpret_budget_heuristic(text)
        if heuristic.confidence >= 0.9:
            return heuristic
        try:
            provider = next(
                (item for item in self._pool.all_providers() if item.name == "OpenRouter"),
                None,
            )
            if provider is None:
                return heuristic
            config = OpenRouterConfig(
                api_key=provider.api_key,
                model=provider.model or "stepfun/step-3.5-flash:free",
                base_url=provider.base_url,
                use_web_search=False,
            )
            interpreted = classify_budget_text(config, text)
            if interpreted.confidence >= heuristic.confidence:
                return interpreted
        except OpenRouterError:
            logger.exception("LLM budget interpretation failed, using heuristic fallback")
        return heuristic

    def generate_plan(self, request: TripRequest) -> TripPlan:
        try:
            return self.generate_plan_llm(request)
        except OpenRouterError:
            logger.exception("All LLM providers failed, falling back to heuristic planner")
            return self.generate_plan_heuristic(request)

    def generate_plan_with_fallback(self, request: TripRequest) -> tuple[TripPlan, bool, str | None]:
        """
        Returns: (plan, used_llm, error_message_if_any)
        """
        try:
            plan = self.generate_plan_llm(request)
            return plan, True, None
        except OpenRouterError as exc:
            return self.generate_plan_heuristic(request), False, str(exc)
