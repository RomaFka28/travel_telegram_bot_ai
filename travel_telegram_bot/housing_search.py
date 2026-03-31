from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from travel_links import build_links_text


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
        links_text = build_links_text(destination, dates_text, origin=None).splitlines()
        results = [
            HousingResult(
                title="Поиск жилья на Островке",
                price_text="Актуальные цены смотрите по ссылке",
                url=links_text[2].split(": ", 1)[1] if len(links_text) > 2 else "",
                source="Островок",
            ),
            HousingResult(
                title="Поиск жилья на Яндекс Путешествиях",
                price_text="Актуальные цены смотрите по ссылке",
                url=links_text[3].split(": ", 1)[1] if len(links_text) > 3 else "",
                source="Яндекс Путешествия",
            ),
        ]
        return HousingSearchResponse(
            mode="links_only",
            summary="Пока показываю быстрые русские ссылки на поиск жилья. Для точных вариантов можно позже включить Playwright-режим.",
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
                "Playwright-режим пока недоступен в окружении. Ниже быстрые русские ссылки на поиск жилья."
            )
            return response

        query = destination.strip() or "Россия"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(f"https://ostrovok.ru/hotel/search/?q={query}", wait_until="domcontentloaded", timeout=self._timeout_ms)
                title = await page.title()
            finally:
                await browser.close()

        fallback = LinkOnlyHousingSearchProvider()
        response = await fallback.search(destination=destination, dates_text=dates_text, group_size=group_size)
        response.mode = "playwright_stub"
        response.summary = (
            f"Playwright-слой подключён и может ходить в браузер. Пока выдаю безопасный fallback, найденная страница: {title}."
        )
        return response


def build_housing_provider(*, playwright_enabled: bool, timeout_ms: int) -> HousingSearchProvider:
    if playwright_enabled:
        return PlaywrightHousingSearchProvider(timeout_ms=timeout_ms)
    return LinkOnlyHousingSearchProvider()
