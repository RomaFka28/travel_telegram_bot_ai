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
    - "12–16 июня"
    - "12-16 июня"
    - "12 июня"
    
    Returns:
        Кортеж (start_date, end_date) в текущем году или None.
    """
    text = (dates_text or "").strip().lower()
    if not text or text == "не указаны":
        return None

    match = re.search(
        r"\b(\d{1,2})\s*(?:-|–|—|до)?\s*(\d{0,2})\s*([а-яё]+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    start_day = int(match.group(1))
    end_raw = match.group(2).strip()
    end_day = int(end_raw) if end_raw else start_day
    month_word = match.group(3)

    month = None
    for key, value in MONTHS_RU.items():
        if key in month_word:
            month = value
            break
    if not month:
        return None

    year = date.today().year
    try:
        start = date(year, month, start_day)
        end = date(year, month, end_day)
    except ValueError:
        return None
    if end < start:
        start, end = end, start
    return start, end
