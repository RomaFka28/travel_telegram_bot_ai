from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from travel_links import build_structured_link_results


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


class HousingSearchProvider(Protocol):
    async def search(self, *, destination: str, dates_text: str, group_size: int) -> HousingSearchResponse:
        ...


class LinkOnlyHousingSearchProvider:
    async def search(self, *, destination: str, dates_text: str, group_size: int) -> HousingSearchResponse:
        structured = build_structured_link_results(
            destination,
            dates_text,
            origin=None,
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


class PlaywrightHousingSearchProvider:
    def __init__(self, timeout_ms: int = 12000) -> None:
        self._timeout_ms = timeout_ms

    async def search(self, *, destination: str, dates_text: str, group_size: int) -> HousingSearchResponse:
        try:
            from playwright.async_api import async_playwright
        except Exception:
            fallback = LinkOnlyHousingSearchProvider()
            response = await fallback.search(destination=destination, dates_text=dates_text, group_size=group_size)
            response.summary = (
                "Playwright-режим пока недоступен в окружении. Ниже быстрые русские источники по жилью."
            )
            return response

        query = destination.strip() or "Россия"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(
                    f"https://ostrovok.ru/hotel/search/?q={query}",
                    wait_until="domcontentloaded",
                    timeout=self._timeout_ms,
                )
                title = await page.title()
            finally:
                await browser.close()

        fallback = LinkOnlyHousingSearchProvider()
        response = await fallback.search(destination=destination, dates_text=dates_text, group_size=group_size)
        response.mode = "playwright_stub"
        response.summary = (
            f"Playwright-слой подключён и может ходить в браузер. "
            f"Пока выдаю безопасный fallback, найденная страница: {title}."
        )
        return response


def build_housing_provider(*, playwright_enabled: bool, timeout_ms: int) -> HousingSearchProvider:
    if playwright_enabled:
        return PlaywrightHousingSearchProvider(timeout_ms=timeout_ms)
    return LinkOnlyHousingSearchProvider()
