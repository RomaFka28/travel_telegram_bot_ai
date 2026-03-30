from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from travel_planner import TripPlan, TripRequest


@dataclass(slots=True)
class OpenRouterConfig:
    api_key: str
    model: str = "openrouter/free"
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout_s: int = 60


class OpenRouterError(RuntimeError):
    pass


def _extract_json_object(text: str) -> dict:
    if not text:
        raise OpenRouterError("Empty LLM response.")

    # If model wrapped JSON in a code fence, unwrap it.
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    # Try direct JSON first.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: find the first {...} block.
    brace = re.search(r"(\{[\s\S]*\})", text)
    if not brace:
        raise OpenRouterError("LLM did not return JSON object.")
    try:
        return json.loads(brace.group(1))
    except json.JSONDecodeError as exc:
        raise OpenRouterError(f"Invalid JSON from LLM: {exc}") from exc


def generate_trip_plan(config: OpenRouterConfig, request: TripRequest) -> TripPlan:
    if not config.api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is missing.")

    system = (
        "Ты — AI travel-помощник. Отвечай по-русски. Все суммы указывай в RUB (₽).\n"
        "Верни ТОЛЬКО валидный JSON-объект (без markdown и без пояснений) со строгими ключами:\n"
        "context_text, itinerary_text, logistics_text, stay_text, alternatives_text, budget_breakdown_text, budget_total_text.\n"
        "itinerary_text: маршрут по дням в формате 'День 1. ...\\nДень 2. ...'.\n"
        "budget_breakdown_text: подробная разбивка + строка 'Итого ориентир: ...'.\n"
        "budget_total_text: одна строка с итогом (например '≈ 85 000–120 000 ₽ на человека').\n"
        "Если каких-то данных нет, честно дай разумный ориентир и предположения."
    )

    user = (
        "Собери черновик поездки по данным:\n"
        f"- направление: {request.destination}\n"
        f"- откуда: {request.origin}\n"
        f"- даты (текст): {request.dates_text}\n"
        f"- длительность: {request.days_count} дней\n"
        f"- группа: {request.group_size} человек\n"
        f"- бюджет (как написал пользователь): {request.budget_text}\n"
        f"- интересы: {request.interests_text}\n"
        f"- заметки/контекст: {request.notes}\n"
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

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=config.base_url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=config.timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise OpenRouterError(f"OpenRouter HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise OpenRouterError(f"OpenRouter connection error: {exc}") from exc

    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 - want robust error surface
        raise OpenRouterError(f"Unexpected OpenRouter response: {raw[:5000]}") from exc

    obj = _extract_json_object(content)
    required = [
        "context_text",
        "itinerary_text",
        "logistics_text",
        "stay_text",
        "alternatives_text",
        "budget_breakdown_text",
        "budget_total_text",
    ]
    missing = [k for k in required if not isinstance(obj.get(k), str) or not obj.get(k).strip()]
    if missing:
        raise OpenRouterError(f"LLM JSON missing fields: {', '.join(missing)}")

    return TripPlan(
        context_text=obj["context_text"].strip(),
        itinerary_text=obj["itinerary_text"].strip(),
        logistics_text=obj["logistics_text"].strip(),
        stay_text=obj["stay_text"].strip(),
        alternatives_text=obj["alternatives_text"].strip(),
        budget_breakdown_text=obj["budget_breakdown_text"].strip(),
        budget_total_text=obj["budget_total_text"].strip(),
    )

