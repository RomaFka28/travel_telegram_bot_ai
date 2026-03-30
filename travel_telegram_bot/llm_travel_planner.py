from __future__ import annotations

from dataclasses import dataclass

from openrouter_client import OpenRouterConfig, OpenRouterError, generate_trip_plan
from travel_planner import TripPlan, TripRequest, TravelPlanner


@dataclass(slots=True)
class LLMPlannerSettings:
    openrouter_api_key: str
    openrouter_model: str = "stepfun/step-3.5-flash:free"


class LLMTravelPlanner(TravelPlanner):
    def __init__(self, settings: LLMPlannerSettings) -> None:
        super().__init__()
        self._settings = settings

    def generate_plan_llm(self, request: TripRequest) -> TripPlan:
        config = OpenRouterConfig(
            api_key=self._settings.openrouter_api_key,
            model=self._settings.openrouter_model or "stepfun/step-3.5-flash:free",
        )
        return generate_trip_plan(config, request)

    def generate_plan(self, request: TripRequest) -> TripPlan:
        # Default behavior unchanged: heuristics.
        return super().generate_plan(request)

    def generate_plan_with_fallback(self, request: TripRequest) -> tuple[TripPlan, bool, str | None]:
        """
        Returns: (plan, used_llm, error_message_if_any)
        """
        try:
            plan = self.generate_plan_llm(request)
            return plan, True, None
        except OpenRouterError as exc:
            return super().generate_plan(request), False, str(exc)

