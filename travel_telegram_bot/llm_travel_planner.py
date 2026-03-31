from __future__ import annotations

import logging
from dataclasses import dataclass

from openrouter_client import OpenRouterConfig, OpenRouterError, generate_trip_plan
from travel_planner import TripPlan, TripRequest, TravelPlanner

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMPlannerSettings:
    openrouter_api_key: str
    openrouter_model: str = "stepfun/step-3.5-flash:free"
    openrouter_web_search: bool = True


class LLMTravelPlanner(TravelPlanner):
    def __init__(self, settings: LLMPlannerSettings) -> None:
        super().__init__()
        self._settings = settings

    def generate_plan_llm(self, request: TripRequest) -> TripPlan:
        config = OpenRouterConfig(
            api_key=self._settings.openrouter_api_key,
            model=self._settings.openrouter_model or "stepfun/step-3.5-flash:free",
            use_web_search=self._settings.openrouter_web_search,
        )
        return generate_trip_plan(config, request)

    def generate_plan(self, request: TripRequest) -> TripPlan:
        try:
            return self.generate_plan_llm(request)
        except OpenRouterError:
            logger.exception("LLM plan generation failed, falling back to heuristic planner")
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
