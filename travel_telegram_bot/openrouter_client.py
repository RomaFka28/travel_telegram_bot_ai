from __future__ import annotations

import json
import re
from dataclasses import dataclass

from http_utils import safe_http_post
from llm_provider_pool import LLMProvider
from travel_planner import BudgetInterpretation, TripPlan, TripRequest, TravelPlanner


@dataclass(slots=True)
class OpenRouterConfig:
    api_key: str
    model: str = "qwen/qwen3.6-plus:free"
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout_s: int = 30
    use_web_search: bool = True
    web_max_results: int = 3


class OpenRouterError(RuntimeError):
    pass


def _supports_openrouter_web_search(config: OpenRouterConfig) -> bool:
    """Qwen не поддерживает web search плагин на OpenRouter."""
    return "openrouter.ai" in config.base_url.lower() and "qwen" not in config.model.lower()


def _build_request_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "travel-telegram-bot/1.0",
    }


def _extract_json_object(text: str) -> dict:
    if not text:
        raise OpenRouterError("Empty LLM response.")

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    brace = re.search(r"(\{[\s\S]*\})", text)
    if not brace:
        raise OpenRouterError("LLM did not return JSON object.")
    try:
        return json.loads(brace.group(1))
    except json.JSONDecodeError as exc:
        raise OpenRouterError(f"Invalid JSON from LLM: {exc}") from exc


def _coerce_trip_plan(obj: dict, request: TripRequest) -> TripPlan:
    fallback_plan = TravelPlanner().generate_plan_heuristic(request)
    field_names = (
        "context_text",
        "itinerary_text",
        "logistics_text",
        "stay_text",
        "alternatives_text",
        "budget_breakdown_text",
        "budget_total_text",
    )
    values: dict[str, str] = {}
    for field_name in field_names:
        raw_value = obj.get(field_name)
        if isinstance(raw_value, str) and raw_value.strip():
            values[field_name] = raw_value.strip()
            continue
        values[field_name] = getattr(fallback_plan, field_name)
    return TripPlan(**values)


def build_trip_plan_payload(config: OpenRouterConfig, request: TripRequest) -> dict:
    language_code = "en" if getattr(request, "language_code", "ru") == "en" else "ru"
    if language_code == "en":
        system = (
            "You are an AI travel assistant for Telegram users. "
            "Reply in English. Return only valid JSON with no markdown. "
            "Use these exact string keys: "
            "context_text, itinerary_text, logistics_text, stay_text, "
            "alternatives_text, budget_breakdown_text, budget_total_text. "
            "When web search is available, use it to find real current place names, "
            "top attractions, best neighbourhoods, and local tips for the exact destination. "
            "Always use real specific place names - never generic descriptions. "
            "Never substitute 'visit a local museum' for the actual museum name. "
            "Use destination-appropriate currency and also provide RUB equivalent with approximate rate. "
            "Do not invent exact live prices. If exact prices are unavailable, give an honest range. "
            "Keep itinerary_text in the format 'Day 1. ...\\nDay 2. ...'. "
            "budget_breakdown_text should include a detailed breakdown and a final line starting with 'Total estimate:'. "
            "budget_total_text must be a single-line total such as '≈ 900-1400 EUR per person (≈ 90 000-140 000 ₽)'."
        )
    else:
        system = (
            "You are an AI travel assistant for Telegram users. "
            "Reply in Russian. Return only valid JSON with no markdown. "
            "Use these exact string keys: "
            "context_text, itinerary_text, logistics_text, stay_text, "
            "alternatives_text, budget_breakdown_text, budget_total_text. "
            "When web search is available, use it to find real current place names, "
            "top attractions, best neighbourhoods, and local tips for the exact destination. "
            "Always use real specific place names - never generic descriptions. "
            "Never substitute 'visit a local museum' for the actual museum name. "
            "Use destination-appropriate currency and also provide RUB equivalent with approximate rate. "
            "Do not invent exact live prices. If exact prices are unavailable, give an honest range. "
            "Keep itinerary_text in the format 'День 1. ...\\nДень 2. ...'. "
            "budget_breakdown_text should include a detailed breakdown and a final line starting with 'Итого ориентир:'. "
            "budget_total_text must be a single-line total such as '≈ 85 000-120 000 ₽ на человека'. "
            "IMPORTANT: Output only human-readable travel advice in natural language. "
            "NEVER generate Python code, function calls, import statements, or technical implementation details. "
            "If you cannot fetch live prices, write a plain-text estimate without any code snippets."
        )

    user = (
        "Build a detailed trip plan from this data:\n"
        f"- destination: {request.destination}\n"
        f"- origin: {request.origin}\n"
        f"- dates_text: {request.dates_text}\n"
        f"- days_count: {request.days_count}\n"
        f"- group_size: {request.group_size}\n"
        f"- budget_text: {request.budget_text}\n"
        f"- interests_text: {request.interests_text}\n"
        f"- notes: {request.notes}\n\n"
        "Critical requirements:\n"
        "1. itinerary_text must contain REAL named places - actual mountains, museums, districts, "
        "viewpoints, restaurants, markets specific to this destination. "
        "Never write generic phrases like 'visit a museum' or 'walk around the old town'. "
        "Write the real name: 'Matterhorn viewpoint at Gornergrat', 'Bahnhofstrasse', 'Confiserie Sprungli'. "
        "If web search is available, look up top attractions and hidden gems for this exact city.\n"
        "2. Each day must have 3 named activities with one sentence explaining what makes each worth visiting.\n"
        "3. context_text must include neighbourhood names for where to stay and 2-3 specific local facts "
        "most travellers do not know about this destination.\n"
        "4. stay_text must name specific districts or neighbourhoods with a reason each suits this group size and budget. "
        "Do NOT include any prices or costs in rubles in stay_text — the bot will show prices separately. "
        "Focus on why the area fits: proximity to attractions, transport, restaurants, beach, etc.\n"
        "5. budget_breakdown_text must show amounts in local currency AND approximate RUB equivalent "
        "using current exchange rates if destination is outside Russia.\n"
        "6. Do not invent exact live prices. Give honest ranges. No marketing language.\n"
    )

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "max_tokens": 1400,
    }

    if config.use_web_search and _supports_openrouter_web_search(config):
        payload["plugins"] = [
            {
                "id": "web",
                "max_results": max(1, config.web_max_results),
            }
        ]

    return payload


def build_budget_interpretation_payload(config: OpenRouterConfig, text: str) -> dict:
    system = (
        "You classify a travel budget from casual user text for a Russian-speaking Telegram bot. "
        "Return only valid JSON with keys: display_text, budget_class, mode, amount_value, confidence. "
        "budget_class must be exactly one of: эконом, бизнес, первый класс. "
        "mode must be exactly one of: ceiling, target, floor, approx, class_only, unlimited. "
        "display_text must be short Russian text suitable for UI. "
        "amount_value must be integer or null. confidence must be a number from 0 to 1. "
        "Do not invent strict numbers if they are not in the text."
    )
    user = (
        "Interpret this budget request from a user planning a trip:\n"
        f"{text}\n\n"
        "Examples:\n"
        "- 'до 50000' => ceiling\n"
        "- 'на 50000' => target\n"
        "- 'от 50000' => floor\n"
        "- 'подешевле' => эконом\n"
        "- 'нормально, но без роскоши' => бизнес\n"
        "- 'не ограничен' => первый класс / unlimited"
    )
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "max_tokens": 240,
    }
    return payload


def build_trip_request_extraction_payload(
    config: OpenRouterConfig,
    text: str,
    *,
    language_code: str = "ru",
) -> dict:
    if language_code == "en":
        system = (
            "You normalize informal travel requests for a Telegram bot. "
            "Return only valid JSON with no markdown. "
            "Use exactly these keys: destination, origin, dates_text, days_count, group_size, "
            "budget_text, interests, needs, route_type, notes, missing_fields, is_actionable. "
            "destination/origin/dates_text/budget_text/notes must be string or null. "
            "days_count and group_size must be integer or null. "
            "interests and needs must be arrays of strings. "
            "route_type must be exactly one of: one_way, round_trip, unknown. "
            "needs may contain only: tickets, housing, activities. "
            "missing_fields may contain only: destination, origin, dates_text, route_type. "
            "Do not invent housing unless the user explicitly asks for housing or hotel. "
            "Preserve multiple interests when the user lists several things."
        )
    else:
        system = (
            "Ты нормализуешь неформальные запросы на поездку для Telegram-бота. "
            "Верни только валидный JSON без markdown. "
            "Используй ровно эти ключи: destination, origin, dates_text, days_count, group_size, "
            "budget_text, interests, needs, route_type, notes, missing_fields, is_actionable. "
            "destination/origin/dates_text/budget_text/notes должны быть строкой или null. "
            "days_count и group_size должны быть целым числом или null. "
            "interests и needs должны быть массивами строк. "
            "route_type должен быть ровно одним из: one_way, round_trip, unknown. "
            "needs может содержать только: tickets, housing, activities. "
            "missing_fields может содержать только: destination, origin, dates_text, route_type. "
            "Не добавляй housing, если пользователь явно не просил жильё. "
            "Если пользователь перечисляет несколько интересов, сохрани их все."
        )

    user = (
        "Normalize this travel request and infer only explicit or strongly implied details:\n"
        f"{text}\n\n"
        "Important:\n"
        "1. If the user asks for a one-way ticket, set route_type=one_way.\n"
        "2. If the user clearly asks for flights or tickets but omits origin, dates or route type, put them into missing_fields.\n"
        "3. Do not fabricate destination, origin, dates, housing, or interests.\n"
        "4. is_actionable should be true only when the request is ready for downstream trip creation."
    )
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "max_tokens": 450,
    }


def generate_trip_plan(config: OpenRouterConfig, request: TripRequest) -> TripPlan:
    if not config.api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is missing.")

    payload = build_trip_plan_payload(config, request)
    data = json.dumps(payload).encode("utf-8")

    try:
        raw = safe_http_post(
            config.base_url,
            data=data,
            headers=_build_request_headers(config.api_key),
            max_retries=2,
            timeout=config.timeout_s,
        )
    except Exception as exc:
        raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise OpenRouterError(f"Unexpected OpenRouter response: {raw[:5000]}") from exc

    obj = _extract_json_object(content)
    return _coerce_trip_plan(obj, request)


def extract_trip_request(config: OpenRouterConfig, text: str, *, language_code: str = "ru") -> dict:
    if not config.api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is missing.")

    payload = build_trip_request_extraction_payload(config, text, language_code=language_code)
    data = json.dumps(payload).encode("utf-8")

    try:
        raw = safe_http_post(
            config.base_url,
            data=data,
            headers=_build_request_headers(config.api_key),
            max_retries=3,
            timeout=config.timeout_s,
        )
    except Exception as exc:
        raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise OpenRouterError(f"Unexpected OpenRouter response: {raw[:5000]}") from exc

    return _extract_json_object(content)


def generate_trip_plan_with_provider(provider: LLMProvider, request: TripRequest) -> TripPlan:
    """Same as generate_trip_plan but uses LLMProvider instead of OpenRouterConfig."""
    config = OpenRouterConfig(
        api_key=provider.api_key,
        model=provider.model,
        base_url=provider.base_url,
        use_web_search=provider.use_web_search,
    )
    return generate_trip_plan(config, request)


def extract_trip_request_with_provider(
    provider: LLMProvider,
    text: str,
    *,
    language_code: str = "ru",
) -> dict:
    config = OpenRouterConfig(
        api_key=provider.api_key,
        model=provider.model,
        base_url=provider.base_url,
        use_web_search=provider.use_web_search,
    )
    return extract_trip_request(config, text, language_code=language_code)
