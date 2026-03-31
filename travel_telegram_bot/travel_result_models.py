from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(slots=True)
class TravelSearchResult:
    title: str
    price_text: str
    url: str
    source: str
    score: int = 0
    budget_fit: str = ""
    dates: str = ""
    note: str = ""


def trim_results(results: list[TravelSearchResult], *, limit: int = 3) -> list[TravelSearchResult]:
    return [result for result in results if result.url][:limit]


def serialize_results(results: list[TravelSearchResult]) -> str:
    return json.dumps([asdict(result) for result in trim_results(results)], ensure_ascii=False)


def deserialize_results(raw: str | None) -> list[TravelSearchResult]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []

    results: list[TravelSearchResult] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        results.append(
            TravelSearchResult(
                title=str(item.get("title") or ""),
                price_text=str(item.get("price_text") or ""),
                url=str(item.get("url") or ""),
                source=str(item.get("source") or ""),
                score=int(item.get("score") or 0),
                budget_fit=str(item.get("budget_fit") or ""),
                dates=str(item.get("dates") or ""),
                note=str(item.get("note") or ""),
            )
        )
    return trim_results(results)


def serialize_needs(needs: list[str]) -> str:
    unique = list(dict.fromkeys(needs))
    return json.dumps(unique, ensure_ascii=False)


def deserialize_needs(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item)]
