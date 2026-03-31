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
    model: str = "stepfun/step-3.5-flash:free"
    base_url: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout_s: int = 60
    use_web_search: bool = True
    web_max_results: int = 3


class OpenRouterError(RuntimeError):
    pass


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


def build_trip_plan_payload(config: OpenRouterConfig, request: TripRequest) -> dict:
    system = (
        "You are an AI travel assistant for Russian-speaking users. "
        "Reply in Russian. Return only valid JSON with no markdown. "
        "Use these exact string keys: "
        "context_text, itinerary_text, logistics_text, stay_text, "
        "alternatives_text, budget_breakdown_text, budget_total_text. "
        "Use destination-appropriate currency when it is obvious; otherwise explain that local pricing needs a separate live check. "
        "If web search is available, use fresh public information when it helps. "
        "Do not invent exact live prices. If exact prices are unavailable, give an honest range or guidance. "
        "Keep itinerary_text in the format 'День 1. ...\\nДень 2. ...'. "
        "budget_breakdown_text should include a detailed breakdown and a final line starting with 'Итого ориентир:'. "
        "budget_total_text must be a single-line total such as '≈ 85 000-120 000 ₽ на человека' or 'нужна проверка live-цен'."
    )

    user = (
        "Build a draft trip plan from this data:\n"
        f"- destination: {request.destination}\n"
        f"- origin: {request.origin}\n"
        f"- dates_text: {request.dates_text}\n"
        f"- days_count: {request.days_count}\n"
        f"- group_size: {request.group_size}\n"
        f"- budget_text: {request.budget_text}\n"
        f"- interests_text: {request.interests_text}\n"
        f"- notes: {request.notes}\n"
        "Keep the plan practical for a Telegram travel bot. "
        "Prefer concise, useful travel guidance over generic marketing language."
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

    if config.use_web_search:
        payload["plugins"] = [
            {
                "id": "web",
                "max_results": max(1, config.web_max_results),
            }
        ]

    return payload


def generate_trip_plan(config: OpenRouterConfig, request: TripRequest) -> TripPlan:
    if not config.api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is missing.")

    payload = build_trip_plan_payload(config, request)
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
    except Exception as exc:  # noqa: BLE001
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
    missing = [key for key in required if not isinstance(obj.get(key), str) or not obj.get(key).strip()]
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
