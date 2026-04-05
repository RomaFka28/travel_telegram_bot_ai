from __future__ import annotations

import asyncio
import logging
import time

from llm_provider_pool import LLMProviderPool
from metrics import get_metrics
from openrouter_client import (
    OpenRouterError,
    extract_trip_request_with_provider,
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
        groq_first = [p for p in providers if p.name == "Groq"]
        others = [p for p in providers if p.name != "Groq"]
        ordered = groq_first + others
        last_error: Exception | None = None
        metrics = get_metrics()

        for provider in ordered:
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
        # Always prefer Groq first (highest rate limit, most reliable), fallback to others
        groq_first = [p for p in providers if p.name == "Groq"]
        others = [p for p in providers if p.name != "Groq"]
        ordered = groq_first + others
        last_error: Exception | None = None
        metrics = get_metrics()

        for i, provider in enumerate(ordered):
            start = time.perf_counter()
            try:
                if i > 0:
                    logger.info(
                        "Switching to fallback LLM provider %d/%d: %s",
                        i + 1, len(ordered), provider.name,
                    )
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

    def extract_trip_request(self, text: str, *, language_code: str = "ru") -> dict[str, object]:
        providers = self._pool.all_providers()
        groq_first = [p for p in providers if p.name == "Groq"]
        others = [p for p in providers if p.name != "Groq"]
        ordered = groq_first + others
        last_error: Exception | None = None
        metrics = get_metrics()

        for provider in ordered:
            start = time.perf_counter()
            try:
                logger.info("Trying trip extraction provider: %s", provider.name)
                result = extract_trip_request_with_provider(
                    provider,
                    text,
                    language_code=language_code,
                )
                duration = time.perf_counter() - start
                metrics.increment("llm.trip_extraction.success", tags={"provider": provider.name})
                metrics.record_time("llm.trip_extraction.response_time", duration, tags={"provider": provider.name})
                return result
            except OpenRouterError as exc:
                duration = time.perf_counter() - start
                metrics.increment("llm.trip_extraction.failure", tags={"provider": provider.name})
                metrics.record_time(
                    "llm.trip_extraction.response_time",
                    duration,
                    tags={"provider": provider.name, "status": "error"},
                )
                logger.warning("Trip extraction provider %s failed: %s", provider.name, exc)
                last_error = exc
                continue

        metrics.increment("llm.trip_extraction.all_providers_failed")
        raise OpenRouterError(
            f"All {len(providers)} trip extraction providers failed. Last error: {last_error}"
        )

    async def extract_trip_request_async(
        self,
        text: str,
        *,
        language_code: str = "ru",
    ) -> dict[str, object]:
        providers = self._pool.all_providers()
        # Always prefer Groq first (highest rate limit, most reliable), fallback to others
        groq_first = [p for p in providers if p.name == "Groq"]
        others = [p for p in providers if p.name != "Groq"]
        ordered = groq_first + others
        last_error: Exception | None = None
        metrics = get_metrics()

        for provider in ordered:
            start = time.perf_counter()
            try:
                logger.info("Trying trip extraction provider (async): %s", provider.name)
                result = await asyncio.to_thread(
                    extract_trip_request_with_provider,
                    provider,
                    text,
                    language_code=language_code,
                )
                duration = time.perf_counter() - start
                metrics.increment("llm.trip_extraction.success", tags={"provider": provider.name})
                metrics.record_time("llm.trip_extraction.response_time", duration, tags={"provider": provider.name})
                return result
            except OpenRouterError as exc:
                duration = time.perf_counter() - start
                metrics.increment("llm.trip_extraction.failure", tags={"provider": provider.name})
                metrics.record_time(
                    "llm.trip_extraction.response_time",
                    duration,
                    tags={"provider": provider.name, "status": "error"},
                )
                logger.warning("Trip extraction provider %s failed: %s", provider.name, exc)
                last_error = exc
                continue

        metrics.increment("llm.trip_extraction.all_providers_failed")
        raise OpenRouterError(
            f"All {len(providers)} trip extraction providers failed. Last error: {last_error}"
        )

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
