"""
Поиск жилья.

Обеспечивает генерацию ссылок на поисковые системы жилья
для РФ/СНГ и международных направлений.
"""
from __future__ import annotations

from dataclasses import dataclass

from travel_links import build_structured_link_results
from value_normalization import normalized_search_value


@dataclass(slots=True)
class HousingResult:
    title: str
    price_text: str
    url: str
    source: str
    note: str = ""


@dataclass(slots=True)
class HousingSearchResponse:
    mode: str
    summary: str
    results: list[HousingResult]


class HousingSearchProvider:
    """Интерфейс провайдера поиска жилья."""
    
    async def search(self, *, destination: str, dates_text: str, group_size: int) -> HousingSearchResponse:
        raise NotImplementedError


class LinkOnlyHousingSearchProvider(HousingSearchProvider):
    """Провайдер на основе генерации ссылок (без браузера)."""
    
    async def search(self, *, destination: str, dates_text: str, group_size: int) -> HousingSearchResponse:
        normalized = normalized_search_value(destination)
        if not normalized:
            return HousingSearchResponse(
                mode="links_only",
                summary="Сначала нужно уточнить направление поездки, чтобы собрать ссылки по жилью без битых результатов.",
                results=[],
            )

        structured = build_structured_link_results(
            normalized,
            dates_text,
            origin=None,
            group_size=group_size,
            context_text="жилье отель квартира суточно турбаза",
        )
        stay_style = "апартаменты / дом" if group_size >= 4 else "отель или студия"
        results = [
            HousingResult(
                title=item.title,
                price_text=item.price_text if index else f"Смотрите актуальные цены, базовый формат: {stay_style}",
                url=item.url,
                source=item.source,
                note=item.note,
            )
            for index, item in enumerate(structured.get("housing", []))
        ]
        return HousingSearchResponse(
            mode="links_only",
            summary=(
                "Пока показываю быстрые русские источники по жилью. "
                "Для компании бот советует апартаменты или дом, для 1-3 человек — отель или студию. "
                "Точные цены смотрите по ссылкам."
            ),
            results=[result for result in results if result.url],
        )


def build_housing_provider() -> HousingSearchProvider:
    """Создаёт провайдер поиска жилья (всегда LinkOnly)."""
    return LinkOnlyHousingSearchProvider()
