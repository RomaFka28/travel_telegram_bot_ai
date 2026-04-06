"""Utilities for parsing and resolving trip dates."""
from __future__ import annotations

import re
from datetime import date, timedelta


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
    Parse a date or date range from free-form text.

    Supported formats:
    - "12-16 июня"
    - "12 июня - 16 июня"
    - "12 июня"
    - "12.06 - 16.06"
    """
    text = (dates_text or "").strip().lower()
    if not text or text in {"не указаны", "не указано", "-"}:
        return None

    full_range = re.search(
        r"\b(\d{1,2})\s+([а-яё]+)\b.*?\b(\d{1,2})\s+([а-яё]+)\b",
        text,
    )
    if full_range:
        start_day = int(full_range.group(1))
        end_day = int(full_range.group(3))
        start_month = _month_from_word(full_range.group(2))
        end_month = _month_from_word(full_range.group(4))
        if start_month and end_month:
            year = date.today().year
            try:
                start = date(year, start_month, start_day)
                end = date(year, end_month, end_day)
                if end < start:
                    start, end = end, start
                return start, end
            except ValueError:
                pass

    short_range = re.search(
        r"\b(\d{1,2})\s*(?:-|–|—|до)\s*(\d{1,2})\s*([а-яё]+)",
        text,
        flags=re.IGNORECASE,
    )
    if short_range:
        start_day = int(short_range.group(1))
        end_day = int(short_range.group(2))
        month = _month_from_word(short_range.group(3))
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

    single = re.search(r"\b(\d{1,2})\s+([а-яё]+)\b", text)
    if single:
        day = int(single.group(1))
        month = _month_from_word(single.group(2))
        if month:
            year = date.today().year
            try:
                parsed = date(year, month, day)
                return parsed, parsed
            except ValueError:
                pass

    numeric_range = re.search(
        r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\s*(?:по|до|-|–|—)\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?",
        text,
    )
    if numeric_range:
        start_day, start_month = int(numeric_range.group(1)), int(numeric_range.group(2))
        end_day, end_month = int(numeric_range.group(4)), int(numeric_range.group(5))
        start_year = int(numeric_range.group(3)) if numeric_range.group(3) else date.today().year
        end_year = int(numeric_range.group(6)) if numeric_range.group(6) else start_year
        if start_year < 100:
            start_year += 2000
        if end_year < 100:
            end_year += 2000
        try:
            start = date(start_year, start_month, start_day)
            end = date(end_year, end_month, end_day)
            if end < start:
                start, end = end, start
            return start, end
        except ValueError:
            pass

    numeric_single = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", text)
    if numeric_single:
        day = int(numeric_single.group(1))
        month = int(numeric_single.group(2))
        year = int(numeric_single.group(3)) if numeric_single.group(3) else date.today().year
        if year < 100:
            year += 2000
        try:
            parsed = date(year, month, day)
            return parsed, parsed
        except ValueError:
            pass

    return None


def is_one_way_trip_text(*texts: str | None) -> bool:
    """Return True when text explicitly asks for a one-way ticket."""
    lowered = "\n".join((text or "") for text in texts).lower()
    triggers = (
        "в одну сторону",
        "без обратного",
        "без обратного билета",
        "только туда",
        "one way",
        "one-way",
        "oneway",
    )
    return any(trigger in lowered for trigger in triggers)


def resolve_trip_dates(dates_text: str | None, days_count: int | None) -> tuple[date | None, date | None]:
    """
    Resolve effective trip start/end dates from free-form date text plus duration.

    Explicit ranges win. A single date expands by ``days_count - 1`` days.
    """
    text = (dates_text or "").strip()
    if not text or text.lower() in {"не указаны", "не указано", "-"}:
        return None, None

    parsed = parse_dates_range(text)
    if parsed is None:
        return None, None

    start_date, parsed_end_date = parsed
    if _has_explicit_range(text):
        return start_date, parsed_end_date

    normalized_days = max(1, int(days_count or 1))
    if normalized_days == 1:
        return start_date, start_date
    return start_date, start_date + timedelta(days=normalized_days - 1)


def _month_from_word(word: str) -> int | None:
    """Extract month number from a Russian month word."""
    normalized = word.lower().strip()
    for key, value in MONTHS_RU.items():
        if normalized.startswith(key) or key in normalized:
            return value
    return None


def _has_explicit_range(text: str) -> bool:
    """Check whether the source text explicitly contains a date range."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return False

    explicit_patterns = (
        r"\b(?:с\s*)?\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\s*(?:по|до|-|–|—)\s*\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b",
        r"(?:с\s+)?\d{1,2}\s+[а-яё]+(?:\s+\d{4})?\s*(?:по|до|-|–|—)\s*\d{1,2}\s+[а-яё]+(?:\s+\d{4})?",
        r"\b\d{1,2}\s*(?:-|–|—|до)\s*\d{1,2}\s+[а-яё]+\b",
    )
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in explicit_patterns)
