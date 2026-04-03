from __future__ import annotations

import asyncio
import logging
import time

from llm_provider_pool import LLMProviderPool
from metrics import get_metrics
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
        metrics = get_metrics()

        for provider in providers:
            start = time.perf_counter()
            try:
                logger.info("Trying LLM provider: %s", provider.name)
                result = generate_trip_plan_with_provider(provider, request)
                duration = time.perf_counter() - start
                metrics.increment("llm.call.success", tags={"provider": provider.name})
                metrics.record_time("llm.response_time", duration, tags={"provider": provider.name})
                return result
            except OpenRouterError as exc:
                duration = time.perf_counter() - start
                logger.warning("Provider %s failed: %s", provider.name, exc)
                metrics.increment("llm.call.failure", tags={"provider": provider.name})
                metrics.record_time("llm.response_time", duration, tags={"provider": provider.name, "status": "error"})
                last_error = exc
                continue

        metrics.increment("llm.all_providers_failed")
        raise OpenRouterError(
            f"All {len(providers)} LLM providers failed. Last error: {last_error}"
        )

    async def generate_plan_llm_async(self, request: TripRequest) -> TripPlan:
        providers = self._pool.all_providers()
        primary = await self._pool.get_next()
        ordered = [primary] + [provider for provider in providers if provider.name != primary.name]
        last_error: Exception | None = None
        metrics = get_metrics()

        for provider in ordered:
            start = time.perf_counter()
            try:
                logger.info("Trying LLM provider (async): %s", provider.name)
                result = await asyncio.to_thread(generate_trip_plan_with_provider, provider, request)
                duration = time.perf_counter() - start
                metrics.increment("llm.call.success", tags={"provider": provider.name})
                metrics.record_time("llm.response_time", duration, tags={"provider": provider.name})
                return result
            except OpenRouterError as exc:
                duration = time.perf_counter() - start
                logger.warning("Provider %s failed: %s", provider.name, exc)
                metrics.increment("llm.call.failure", tags={"provider": provider.name})
                metrics.record_time("llm.response_time", duration, tags={"provider": provider.name, "status": "error"})
                last_error = exc
                continue

        metrics.increment("llm.all_providers_failed")
        raise OpenRouterError(
            f"All {len(providers)} LLM providers failed. Last error: {last_error}"
        )

    async def generate_plan_async(self, request: TripRequest) -> TripPlan:
        metrics = get_metrics()
        try:
            metrics.increment("llm.plan_generation", tags={"mode": "llm"})
            return await self.generate_plan_llm_async(request)
        except OpenRouterError as exc:
            logger.warning("Async LLM generation failed, using heuristic fallback: %s", exc)
            metrics.increment("llm.plan_generation", tags={"mode": "heuristic_fallback"})
            return await asyncio.to_thread(self.generate_plan_heuristic, request)

    def interpret_budget_text(self, text: str) -> BudgetInterpretation:
        return self._interpret_budget_heuristic(text)

    def generate_plan(self, request: TripRequest) -> TripPlan:
        metrics = get_metrics()
        try:
            metrics.increment("llm.plan_generation", tags={"mode": "llm"})
            return self.generate_plan_llm(request)
        except OpenRouterError as exc:
            logger.warning("Sync LLM generation failed, using heuristic fallback: %s", exc)
            metrics.increment("llm.plan_generation", tags={"mode": "heuristic_fallback"})
            return self.generate_plan_heuristic(request)

    def generate_plan_with_fallback(self, request: TripRequest) -> tuple[TripPlan, bool, str | None]:
        """
        Returns: (plan, used_llm, error_message_if_any)
        """
        metrics = get_metrics()
        try:
            plan = self.generate_plan_llm(request)
            metrics.increment("llm.plan_generation", tags={"mode": "llm"})
            return plan, True, None
        except OpenRouterError as exc:
            metrics.increment("llm.plan_generation", tags={"mode": "heuristic_fallback"})
            return self.generate_plan_heuristic(request), False, str(exc)
