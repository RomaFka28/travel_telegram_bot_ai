from __future__ import annotations

import asyncio
import logging

from llm_provider_pool import LLMProviderPool
from openrouter_client import OpenRouterError, generate_trip_plan_with_provider
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
        except OpenRouterError as exc:
            logger.warning("Async LLM generation failed, using heuristic fallback: %s", exc)
            return await asyncio.to_thread(self.generate_plan_heuristic, request)

    def interpret_budget_text(self, text: str) -> BudgetInterpretation:
        return self._interpret_budget_heuristic(text)

    def generate_plan(self, request: TripRequest) -> TripPlan:
        try:
            return self.generate_plan_llm(request)
        except OpenRouterError as exc:
            logger.warning("Sync LLM generation failed, using heuristic fallback: %s", exc)
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
