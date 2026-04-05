"""
Утилиты для работы с датами.

Содержит функции для парсинга дат из текста, используемые
различными модулями (weather, travelpayouts, и др.).
"""
from __future__ import annotations

import re
from datetime import date


MONTHS_RU: dict[str, int] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "мая": 5,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}


def parse_dates_range(dates_text: str) -> tuple[date, date] | None:
    """
    Парсит диапазон дат из текста.

    Поддерживаемые форматы:
    - "12–16 июня" / "12-16 июня"
    - "12 июня - 16 июня" / "12 июня по 16 июня"
    - "12 июня" (single date)
    - "12.06 - 16.06" / "12.06.2026 по 16.06.2026"

    Returns:
        Кортеж (start_date, end_date) или None.
    """
    text = (dates_text or "").strip().lower()
    if not text or text == "не указаны":
        return None

    # Формат: "12 июня - 16 июня" / "12 июня по 16 июня" / "с 12 июня до 16 июня"
    full_range = re.search(
        r"\b(\d{1,2})\s+([а-яё]+)\b.*?\b(\d{1,2})\s+([а-яё]+)\b",
        text,
    )
    if full_range:
        d1 = int(full_range.group(1))
        d2 = int(full_range.group(3))
        m1 = _month_from_word(full_range.group(2))
        m2 = _month_from_word(full_range.group(4))
        if m1 and m2:
            year = date.today().year
            try:
                start = date(year, m1, d1)
                end = date(year, m2, d2)
                if end < start:
                    start, end = end, start
                return start, end
            except ValueError:
                pass

    # Формат: "12-16 июня" / "12–16 июня"
    match = re.search(
        r"\b(\d{1,2})\s*(?:-|–|—|до)\s*(\d{1,2})\s*([а-яё]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        start_day = int(match.group(1))
        end_day = int(match.group(2))
        month_word = match.group(3)
        month = _month_from_word(month_word)
        if month:
            year = date.today().year
            try:
                start = date(year, month, start_day)
                end = date(year, month, end_day)
                if end < start:
                    start, end = end, start
                return start, end
            except ValueError:
                pass

    # Single: "12 июня"
    single = re.search(r"\b(\d{1,2})\s+([а-яё]+)\b", text)
    if single:
        day = int(single.group(1))
        month = _month_from_word(single.group(2))
        if month:
            year = date.today().year
            try:
                d = date(year, month, day)
                return d, d
            except ValueError:
                pass

    # Numeric: "12.06 - 16.06" / "12.06.2026 по 16.06.2026"
    numeric_range = re.search(
        r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*(?:по|до|-|–|—)\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?",
        text,
    )
    if numeric_range:
        d1, m1 = int(numeric_range.group(1)), int(numeric_range.group(2))
        d2, m2 = int(numeric_range.group(4)), int(numeric_range.group(5))
        y1 = int(numeric_range.group(3)) if numeric_range.group(3) else date.today().year
        y2 = int(numeric_range.group(6)) if numeric_range.group(6) else y1
        if y1 < 100:
            y1 += 2000
        if y2 < 100:
            y2 += 2000
        try:
            start = date(y1, m1, d1)
            end = date(y2, m2, d2)
            if end < start:
                start, end = end, start
            return start, end
        except ValueError:
            pass

    return None


def _month_from_word(word: str) -> int | None:
    """Извлекает номер месяца из слова (с поддержкой окончаний)."""
    word = word.lower().strip()
    for key, value in MONTHS_RU.items():
        if word.startswith(key) or key in word:
            return value
    return None
